"""Phase-9 observability smoke (item 1).

Verifies the metrics helpers register on the default registry, expose on the
/metrics text, carry the intended labels, and never raise (best-effort). No
services required — this exercises the pure metric primitives + helper API.
"""

from __future__ import annotations

from prometheus_client import generate_latest

from app.core.metrics import (
    observe_agent_node,
    observe_cam_generation,
    observe_celery_task,
    observe_db_query,
    observe_ingest_stage,
    observe_llm_call,
    observe_ml_inference,
    observe_workflow,
)


def _emit_all() -> None:
    observe_workflow("credit_appraisal", "completed", 1.2)
    observe_agent_node("risk_assessment", "ok", 0.05)
    observe_llm_call(
        "gemini-flash",
        status="ok",
        mode="live",
        duration_s=0.4,
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.001,
    )
    observe_llm_call("gemini-flash", status="ok", mode="replay_fallback")
    observe_ingest_stage("extract_domain", "done", 0.3)
    observe_ingest_stage("extract_domain", "failed", 0.1)
    observe_ml_inference(True, 0.002)
    observe_cam_generation("ok", 0.01)
    observe_celery_task("workflow.run", "success", duration_s=1.5, queue_latency_s=0.02)
    observe_db_query("select", 0.003)


def test_all_helpers_emit_and_expose_on_metrics() -> None:
    _emit_all()
    text = generate_latest().decode()
    for metric in (
        "ic_workflow_duration_seconds",
        "ic_workflow_runs_total",
        "ic_agent_node_duration_seconds",
        "ic_llm_duration_seconds",
        "ic_llm_calls_total",
        "ic_llm_cost_usd_total",
        "ic_ingest_stage_duration_seconds",
        "ic_document_processing_failures_total",
        "ic_ml_inference_duration_seconds",
        "ic_cam_generation_duration_seconds",
        "ic_celery_task_duration_seconds",
        "ic_celery_queue_latency_seconds",
        "ic_db_query_duration_seconds",
    ):
        assert metric in text, f"{metric} missing from /metrics output"


def test_llm_mode_label_distinguishes_live_from_fallback() -> None:
    observe_llm_call("gemini-flash", status="ok", mode="live")
    observe_llm_call("gemini-flash", status="ok", mode="replay_fallback")
    text = generate_latest().decode()
    assert 'mode="live"' in text
    assert 'mode="replay_fallback"' in text


def test_helpers_never_raise_on_bad_input() -> None:
    # Metrics are best-effort — a weird value must not raise.
    observe_workflow("k", "s", None)
    observe_ingest_stage("s", "done", None)
    observe_celery_task("t", "success")  # no duration/queue → no-op counter path
