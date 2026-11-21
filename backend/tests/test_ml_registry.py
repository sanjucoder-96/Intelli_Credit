"""ML registry + serving integration (Phase 6, ADR-0010).

Registers a model into the existing ``ml_models`` table, resolves + loads the
champion in-process, predicts through the full DB→artifact→feature→PD chain,
and enforces the structural real-data guard (a synthetic-lineage model cannot be
registered) and single-champion promotion.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.analysis.features import build_pd_features, feature_row
from app.core.config import get_settings
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import ModelStage
from app.infrastructure.ml.registry import (
    SyntheticTrainingDataError,
    clear_champion_cache,
    get_champion_row,
    load_champion_pd_model,
    register_model,
)
from tests.ml_fixtures import build_fixture_pd_model
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


def _financials() -> FinancialLineItems:
    return FinancialLineItems(
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
    )


async def test_register_and_load_champion_predicts(tmp_path: Path) -> None:
    clear_champion_cache()
    name = f"pd_model_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / name / "1.0.0"
    card = build_fixture_pd_model(directory, name=name, version="1.0.0")

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            row = await register_model(
                session, card=card, artifact_path=str(directory), stage=ModelStage.CHAMPION
            )
            await session.commit()
            assert row.stage is ModelStage.CHAMPION
            assert row.feature_list["feature_schema_version"] == card.feature_schema_version

        # Resolve champion by the configured name + load + predict.
        settings = get_settings().model_copy(
            update={"ml": get_settings().ml.model_copy(update={"champion_model_name": name})}
        )
        async with factory() as session:
            model = await load_champion_pd_model(session, settings)
        assert model is not None
        assert model.card.name == name and model.card.version == "1.0.0"
        prediction = model.predict(feature_row(build_pd_features(_financials())))
        assert 0.0 <= prediction.pd <= 1.0
        assert len(prediction.contributions) == len(card.feature_names)


async def test_synthetic_lineage_is_rejected(tmp_path: Path) -> None:
    name = f"pd_bad_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / name / "1.0.0"
    card = build_fixture_pd_model(
        directory, name=name, version="1.0.0", data_origin="synthetic_demo"
    )
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            with pytest.raises(SyntheticTrainingDataError):
                await register_model(
                    session, card=card, artifact_path=str(directory), stage=ModelStage.CHAMPION
                )


async def test_promotion_archives_the_previous_champion(tmp_path: Path) -> None:
    clear_champion_cache()
    name = f"pd_model_{uuid.uuid4().hex[:8]}"
    d1 = tmp_path / name / "1.0.0"
    d2 = tmp_path / name / "2.0.0"
    card1 = build_fixture_pd_model(d1, name=name, version="1.0.0")
    card2 = build_fixture_pd_model(d2, name=name, version="2.0.0")

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            await register_model(
                session, card=card1, artifact_path=str(d1), stage=ModelStage.CHAMPION
            )
            await register_model(
                session, card=card2, artifact_path=str(d2), stage=ModelStage.CHAMPION
            )
            await session.commit()
        async with factory() as session:
            champion = await get_champion_row(session, name)
        assert champion is not None and champion.version == "2.0.0"


async def test_no_champion_returns_none(tmp_path: Any) -> None:
    clear_champion_cache()
    settings = get_settings().model_copy(
        update={
            "ml": get_settings().ml.model_copy(
                update={"champion_model_name": f"absent_{uuid.uuid4().hex[:8]}"}
            )
        }
    )
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            model = await load_champion_pd_model(session, settings)
    assert model is None
