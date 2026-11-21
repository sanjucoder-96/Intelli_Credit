"""Workflow YAML loading and definition validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationFailedError
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.loader import load_definition, load_directory

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_smoke_definition() -> None:
    definition = load_definition(FIXTURES / "wf_smoke.yaml")
    assert definition.key == "wf_smoke"
    assert definition.version == 1
    assert [node.name for node in definition.nodes] == ["gather", "summarize"]
    assert definition.nodes[1].after == ["gather"]
    assert definition.dashboard[0].type == "narrative"
    assert definition.agents["noop"].budget is not None


def test_load_directory_empty_is_fine(tmp_path: Path) -> None:
    assert load_directory(tmp_path) == []
    assert load_directory(tmp_path / "does-not-exist") == []


def test_registry_load_builtin(tmp_path: Path) -> None:
    from app.workflows.registry import WorkflowRegistry

    registry = WorkflowRegistry()
    assert registry.load_builtin(tmp_path) == 0  # empty dir: valid, zero definitions

    (tmp_path / "wf_smoke.yaml").write_text(
        (FIXTURES / "wf_smoke.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert registry.load_builtin(tmp_path) == 1
    assert registry.keys() == ["wf_smoke"]


def test_invalid_yaml_raises_with_path(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(ValidationFailedError) as excinfo:
        load_definition(bad)
    assert "broken.yaml" in excinfo.value.message


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValidationFailedError):
        load_definition(bad)


def test_duplicate_node_names_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate node names"):
        WorkflowDefinition(
            key="dup",
            version=1,
            nodes=[
                NodeSpec(name="a", agent="noop"),
                NodeSpec(name="a", agent="noop"),
            ],
        )


def test_unknown_after_reference_rejected() -> None:
    with pytest.raises(ValueError, match="unknown nodes"):
        WorkflowDefinition(
            key="dangling",
            version=1,
            nodes=[NodeSpec(name="a", agent="noop", after=["ghost"])],
        )


def test_cycle_rejected() -> None:
    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition(
            key="cyclic",
            version=1,
            nodes=[
                NodeSpec(name="a", agent="noop", after=["b"]),
                NodeSpec(name="b", agent="noop", after=["a"]),
            ],
        )


def test_dashboard_block_vocabulary_enforced(tmp_path: Path) -> None:
    bad = tmp_path / "block.yaml"
    bad.write_text(
        "key: badblock\nversion: 1\n"
        "nodes:\n  - name: a\n    agent: noop\n"
        "dashboard:\n  - type: pie_chart\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailedError):
        load_definition(bad)
