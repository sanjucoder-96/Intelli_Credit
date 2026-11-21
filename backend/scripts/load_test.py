"""Concurrency / load baseline (Phase-9 item 3).

Runs N full credit_appraisal workflows concurrently against the live stack and
reports a baseline: per-run latency (p50/p95/max), throughput, and failure rate
at concurrency levels 5 / 10 / 25. The LLM narrative slots use a schema-scripted
caller so the load test is deterministic and needs no Gemini — it exercises the
real deterministic engine, the durable checkpointer, and Postgres/Redis under
concurrency.

This ESTABLISHES A BASELINE — it is not an optimization run. If a level cannot
complete cleanly, the observed failures are reported, not hidden.

    IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.load_test
    ... -m scripts.load_test --levels 5,10,25 --db postgresql+asyncpg://...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import selectors
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "IC_DATABASE__URL",
    "postgresql+asyncpg://intellicredit:intellicredit@127.0.0.1:5432/intellicredit_test",
)
os.environ.setdefault("IC_AUTH__JWT_SECRET", "unit-test-secret-at-least-16-chars")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.agents.llm import LLMResult  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.context import TenantContext  # noqa: E402
from app.docintel.schemas import FinancialLineItems  # noqa: E402
from app.domain.enums import DataOrigin, Role, WorkflowExecutionStatus  # noqa: E402
from app.infrastructure.db.models import Company, LoanApplication, Organization  # noqa: E402
from app.infrastructure.gemini import GeminiCallResult  # noqa: E402
from app.workflows.checkpointer import build_checkpointer  # noqa: E402
from app.workflows.loader import load_definition  # noqa: E402
from app.workflows.persistence import create_execution, ensure_workflow_row  # noqa: E402
from app.workflows.runner import WorkflowRunner  # noqa: E402
from app.workflows.runtime import build_runtime_bundle, make_context_factory  # noqa: E402
from app.workflows.state import initial_state  # noqa: E402

_DEFINITION = (
    Path(__file__).resolve().parents[1] / "app/workflows/definitions/credit_appraisal.yaml"
)
_SCRIPTED = {
    "recommendation": {"rationale": "ok", "key_strengths": ["x"], "key_risks": ["y"]},
    "recommendation:critic": {
        "groundedness": 0.9,
        "completeness": 0.9,
        "consistency": 1.0,
        "passed": True,
    },
    "cam_generator": {"executive_summary": "Deterministic approval within limit."},
}


class _Scripted:
    async def call(self, request: Any, *, governor: Any = None) -> LLMResult:
        parsed = request.schema_type(**_SCRIPTED.get(request.purpose, {}))
        return LLMResult(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            call=GeminiCallResult(model="scripted", cost_usd=0.0),
        )


def _financials() -> dict[str, Any]:
    return FinancialLineItems(
        fiscal_year="2025-26",
        revenue=Decimal("100000000"),
        ebitda=Decimal("18000000"),
        net_profit=Decimal("9000000"),
        total_assets=Decimal("120000000"),
        total_equity=Decimal("60000000"),
        total_debt=Decimal("30000000"),
        current_assets=Decimal("50000000"),
        current_liabilities=Decimal("25000000"),
        inventory=Decimal("15000000"),
        receivables=Decimal("20000000"),
        interest_expense=Decimal("3000000"),
        cash_flow_from_operations=Decimal("15000000"),
    ).model_dump(mode="json")


async def _run_one(factory: Any, definition: Any) -> tuple[bool, float]:
    start = time.perf_counter()
    try:
        async with factory() as session:
            s = uuid.uuid4().hex[:8]
            org = Organization(name=f"Load {s}", slug=f"load-{s}", is_active=True)
            session.add(org)
            await session.flush()
            company = Company(
                org_id=org.id, legal_name=f"Acme {s}", data_origin=DataOrigin.SYNTHETIC_DEMO
            )
            session.add(company)
            await session.flush()
            case = LoanApplication(org_id=org.id, company_id=company.id, title=f"Load {s}")
            session.add(case)
            await session.commit()
            org_id, case_id = org.id, case.id

        tenant = TenantContext(org_id=org_id, user_id=uuid.uuid4(), role=Role.CREDIT_OFFICER)
        run_id = str(uuid.uuid4())
        runner = WorkflowRunner(factory)
        state = initial_state(run_id, "credit_appraisal")
        state["scratch"] = {"financials": _financials()}

        async with factory() as session:
            row = await ensure_workflow_row(session, definition)
            execution = await create_execution(
                session,
                org_id=org_id,
                workflow_id=row.id,
                thread_id=run_id,
                loan_application_id=case_id,
            )
            execution_id = execution.id
            await session.commit()

        scripted = _Scripted()

        def ctx_factory(node: Any, st: Any) -> Any:
            bundle = build_runtime_bundle(
                session_factory=factory,
                org_id=org_id,
                execution_id=execution_id,
                run_id=run_id,
                tenant=tenant,
                settings=get_settings(),
                redis=None,
                case_id=case_id,
            )
            ctx = make_context_factory(bundle, definition)(node, st)
            ctx.caller = scripted
            return ctx

        async with build_checkpointer() as saver:
            await runner._invoke(definition, execution_id, run_id, state, saver, ctx_factory)
        async with build_checkpointer() as saver:
            outcome = await runner.resume(
                definition,
                thread_id=run_id,
                checkpointer=saver,
                resume_state={"scratch": {"officer_notes": []}},
                context_factory=ctx_factory,
            )
        ok = outcome.status is WorkflowExecutionStatus.COMPLETED
        return ok, time.perf_counter() - start
    except Exception as exc:  # a failure at a load level is REPORTED, not hidden
        print(f"    run failed: {type(exc).__name__}: {str(exc)[:120]}")
        return False, time.perf_counter() - start


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


async def _run_level(factory: Any, definition: Any, concurrency: int) -> dict[str, Any]:
    started = time.perf_counter()
    results = await asyncio.gather(*[_run_one(factory, definition) for _ in range(concurrency)])
    wall = time.perf_counter() - started
    latencies = sorted(lat for _ok, lat in results)
    completed = sum(1 for ok, _ in results if ok)
    failed = concurrency - completed
    return {
        "concurrency": concurrency,
        "completed": completed,
        "failed": failed,
        "failure_rate": round(failed / concurrency, 4),
        "wall_s": round(wall, 3),
        "throughput_runs_per_s": round(completed / wall, 3) if wall else 0.0,
        "p50_s": round(_pct(latencies, 0.5), 3),
        "p95_s": round(_pct(latencies, 0.95), 3),
        "max_s": round(latencies[-1], 3) if latencies else 0.0,
    }


async def _main(levels: list[int], db_url: str) -> int:
    engine = create_async_engine(db_url, pool_size=10, max_overflow=30, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    definition = load_definition(_DEFINITION)

    print("\n=== IntelliCredit concurrency / load baseline ===")
    print(f"db = {db_url.split('@')[-1]}   workflow = credit_appraisal (offline scripted LLM)\n")
    print("  warming up (checkpointer + pool)...")
    await _run_one(factory, definition)

    rows: list[dict[str, Any]] = []
    for level in levels:
        print(f"  running concurrency={level} ...")
        rows.append(await _run_level(factory, definition, level))

    header = f"\n{'conc':>5} {'ok':>4} {'fail':>4} {'fail%':>6} {'wall_s':>7}"
    print(f"{header} {'thru/s':>7} {'p50':>6} {'p95':>6} {'max':>6}")
    print("  " + "-" * 66)
    for r in rows:
        print(
            f"{r['concurrency']:>5} {r['completed']:>4} {r['failed']:>4} "
            f"{r['failure_rate'] * 100:>5.1f}% {r['wall_s']:>7} {r['throughput_runs_per_s']:>7} "
            f"{r['p50_s']:>6} {r['p95_s']:>6} {r['max_s']:>6}"
        )
    await engine.dispose()
    total_failed = sum(r["failed"] for r in rows)
    print(f"\n  total failures across all levels: {total_failed}")
    return 0 if total_failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="5,10,25")
    parser.add_argument("--db", default=os.environ["IC_DATABASE__URL"])
    args = parser.parse_args()
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    loop = (
        asyncio.SelectorEventLoop(selectors.SelectSelector())
        if sys.platform == "win32"
        else asyncio.new_event_loop()
    )
    try:
        return loop.run_until_complete(_main(levels, args.db))
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
