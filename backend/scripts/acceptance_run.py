"""IntelliCredit hackathon acceptance run (Phase-8 item 14).

Produces a single, honest acceptance report. Each capability maps to the
Phase-8 test module(s) that verify it; this script RUNS those tests and reports
their real pass/fail — it never manufactures a result. It also embeds actual
measured numbers where measurements exist (extraction accuracy over the eval
corpus, deterministic hot-path latencies).

    ./.venv/Scripts/python.exe -m scripts.acceptance_run           # deterministic only
    IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.acceptance_run   # + live DB

Integration categories are reported as SKIP unless IC_INTEGRATION_TESTS=1 and a
live Postgres/Redis are available.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]

# capability -> (test modules, needs_live_services)
_CATEGORIES: list[tuple[str, list[str], bool]] = [
    ("Extraction accuracy", ["tests/test_extraction_eval.py"], False),
    ("Evidence provenance", ["tests/test_evidence_integrity.py"], False),
    ("GST / bank analysis", ["tests/test_indian_context.py"], False),
    ("Research (safety+shape)", ["tests/test_research_eval.py"], False),
    ("Fraud / consistency", ["tests/test_docintel_validators.py"], False),
    (
        "ML risk (PD + SHAP)",
        [
            "tests/test_ml_feature_parity.py",
            "tests/test_ml_validation.py",
            "tests/test_pd_model.py",
            "tests/test_features.py",
        ],
        False,
    ),
    ("Deterministic decision", ["tests/test_decision_invariants.py"], False),
    ("Gemini resilience", ["tests/test_gemini_resilience.py"], False),
    ("CAM consistency", ["tests/test_cam_consistency.py"], False),
    ("PDF generation", ["tests/test_cam_pdf.py"], False),
    ("Security checks", ["tests/test_security.py"], False),
    ("Security hardening", ["tests/test_security_hardening.py"], False),
    ("Prompt injection", ["tests/test_evidence_integrity.py"], False),
    ("Observability metrics", ["tests/test_observability.py"], False),
    ("Extraction/research bench", ["tests/test_benchmarks.py"], False),
    ("Demo reliability mode", ["tests/test_execution_mode.py"], False),
    ("Performance baseline", ["tests/test_perf_baseline.py"], False),
    # --- categories that require live Postgres/Redis ---
    ("HITL reliability", ["tests/test_hitl_adversarial.py"], True),
    ("Restart / resume", ["tests/test_workflow_resilience.py"], True),
    ("CAM cross-surface", ["tests/test_cam_api_consistency.py"], True),
    ("Tenant isolation", ["tests/test_security_isolation.py"], True),
]


def _run_pytest(modules: list[str]) -> tuple[str, int, int]:
    """Run pytest on the modules; return (verdict, passed, failed)."""
    env = dict(os.environ)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", *modules],
        cwd=_BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    passed = failed = 0
    for raw in out.splitlines():
        # e.g. "28 passed, 1 warning in 1.23s" — commas separate the tokens.
        parts = raw.strip().replace(",", " ").split()
        for i, tok in enumerate(parts):
            if tok == "passed" and i and parts[i - 1].isdigit():
                passed = int(parts[i - 1])
            if tok == "failed" and i and parts[i - 1].isdigit():
                failed = int(parts[i - 1])
    verdict = "PASS" if proc.returncode == 0 and failed == 0 else "FAIL"
    return verdict, passed, failed


def _measurements() -> dict[str, object]:
    from eval.extraction_corpus import build_corpus, score_corpus  # noqa: PLC0415
    from eval.perf import collect_perf_baseline  # noqa: PLC0415

    return {
        "extraction": score_corpus(build_corpus()).summary(),
        "perf_ms": collect_perf_baseline(iterations=100),
    }


def main() -> int:
    live = bool(os.environ.get("IC_INTEGRATION_TESTS"))
    rows: list[tuple[str, str, str]] = []
    overall_ok = True

    for name, modules, needs_live in _CATEGORIES:
        if needs_live and not live:
            rows.append((name, "SKIP", "needs IC_INTEGRATION_TESTS=1 + live Postgres/Redis"))
            continue
        verdict, passed, failed = _run_pytest(modules)
        detail = f"{passed} passed" + (f", {failed} failed" if failed else "")
        rows.append((name, verdict, detail))
        if verdict != "PASS":
            overall_ok = False

    measured = _measurements()

    # ---- render ----
    width = max(len(n) for n, _, _ in rows)
    print("\nINTELLICREDIT ACCEPTANCE RUN")
    print("=" * (width + 34))
    for name, verdict, detail in rows:
        print(f"{name.ljust(width)}   {verdict:5}   {detail}")
    print("-" * (width + 34))
    print(f"{'Overall'.ljust(width)}   {'PASS' if overall_ok else 'FAIL'}")
    print("=" * (width + 34))

    ex = measured["extraction"]  # type: ignore[index]
    print("\nMeasured — extraction eval (deterministic corpus):")
    print(f"  classification_accuracy = {ex['classification_accuracy']}")  # type: ignore[index]
    print(
        f"  field_precision={ex['field_precision']}  field_recall={ex['field_recall']}"  # type: ignore[index]
        f"  numeric_accuracy={ex['numeric_accuracy']}  page_accuracy={ex['page_accuracy']}"
    )  # type: ignore[index]
    print(f"  fabrications={ex['fabrications']} (must be 0)  unknown_kept={ex['unknown_kept']}")  # type: ignore[index]

    print("\nMeasured — hot-path latency (p50 ms):")
    for path, stats in measured["perf_ms"].items():  # type: ignore[union-attr]
        p50, p95 = stats["p50_ms"], stats["p95_ms"]
        print(f"  {path:24} p50={p50:>7} p95={p95:>7} max={stats['max_ms']:>7}")

    if not live:
        print(
            "\nNOTE: integration categories were SKIPPED (set IC_INTEGRATION_TESTS=1 with a live "
            "Postgres/Redis to run HITL, restart/resume, CAM cross-surface, and tenant isolation)."
        )

    report = {
        "overall": "PASS" if overall_ok else "FAIL",
        "categories": [{"name": n, "verdict": v, "detail": d} for n, v, d in rows],
        "measured": measured,
        "live_services": live,
    }
    out_path = _BACKEND / "eval" / "acceptance_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report written to {out_path}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
