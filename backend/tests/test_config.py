"""Settings parsing and fail-fast validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, SettingsError, get_settings


def test_settings_parse_from_environment(settings: Settings) -> None:
    assert settings.app.env == "testing"
    assert settings.database.url.startswith("postgresql+asyncpg://")
    assert settings.auth.jwt_algorithm == "HS256"
    assert settings.celery.queues == ["agents", "ingest", "ocr"]


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_APP__DEBUG", "true")
    settings = Settings(_env_file=None)
    assert settings.app.debug is True


def test_missing_required_settings_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "IC_DATABASE__URL",
        "IC_REDIS__URL",
        "IC_AUTH__JWT_SECRET",
        "IC_CELERY__BROKER_URL",
        "IC_CELERY__RESULT_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    get_settings.cache_clear()
    try:
        with pytest.raises(SettingsError) as excinfo:
            get_settings()
        # Missing nested models report at the group level (IC_DATABASE, not
        # IC_DATABASE__URL) — precise enough to point at the right variables.
        message = str(excinfo.value)
        assert "IC_DATABASE" in message
        assert "IC_AUTH" in message
    finally:
        get_settings.cache_clear()


def test_jwt_secret_minimum_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_AUTH__JWT_SECRET", "too-short")
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None)
