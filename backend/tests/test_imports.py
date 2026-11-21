"""Every module in the app package must import cleanly.

This is the repo-wide broken-import tripwire: a bad import anywhere —
including modules nothing else references yet — fails this single test.
"""

from __future__ import annotations

import importlib
import pkgutil

import app


def test_every_app_module_imports() -> None:
    failures: dict[str, str] = {}
    for module_info in pkgutil.walk_packages(app.__path__, prefix="app."):
        # Alembic revision files are executed by Alembic, not imported as app
        # modules (their filenames are not identifiers).
        if module_info.name.startswith("app.infrastructure.db.migrations"):
            continue
        try:
            importlib.import_module(module_info.name)
        except Exception as exc:  # noqa: BLE001 — report every failure at once
            failures[module_info.name] = f"{type(exc).__name__}: {exc}"

    assert not failures, f"Modules failed to import: {failures}"
