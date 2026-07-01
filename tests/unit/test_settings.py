"""Tests unitarios para config.settings."""

import os
import pytest
from pydantic import ValidationError


class TestBotMode:
    def test_import_bot_mode(self):
        from config.settings import BotMode
        assert BotMode.SHADOW.value == "SHADOW"
        assert BotMode.PAPER.value == "PAPER"
        assert BotMode.DEMO.value == "DEMO"
        assert BotMode.LIVE.value == "LIVE"

    def test_bot_mode_is_str_enum(self):
        from config.settings import BotMode
        assert isinstance(BotMode.SHADOW, str)


class TestSettingsLoads:
    def test_settings_singleton_importable(self):
        from config.settings import settings
        assert settings is not None

    def test_settings_has_mt5(self):
        from config.settings import settings
        assert hasattr(settings, "mt5")
        assert settings.mt5.server != ""

    def test_settings_has_db(self):
        from config.settings import settings
        assert hasattr(settings, "db")
        assert settings.db.port == 5432

    def test_settings_has_risk(self):
        from config.settings import settings
        assert hasattr(settings, "risk")
        assert 0 < settings.risk.risk_pct_per_trade <= 5.0

    def test_db_url_format(self):
        from config.settings import settings
        assert settings.db.url.startswith("postgresql+psycopg2://")

    def test_mode_property(self):
        from config.settings import settings, BotMode
        assert isinstance(settings.mode, BotMode)

    def test_min_confidence_default_is_075(self):
        """Day trading activo: umbral por defecto bajado a 0.75 (era 0.85).

        Se valida el default del campo (independiente de .env) para que el
        filtro no se endurezca por accidente en máquinas sin la variable.
        """
        from config.settings import Settings
        assert Settings.model_fields["min_confidence_to_trade"].default == 0.75


class TestRiskSettingsBounds:
    def test_risk_pct_bounds(self):
        from config.settings import RiskSettings
        # Pydantic debe rechazar valores fuera de rango
        with pytest.raises(ValidationError):
            RiskSettings(
                risk_pct_per_trade=100.0,  # > 5.0
                max_daily_drawdown_pct=3.0,
                max_weekly_drawdown_pct=6.0,
                max_monthly_drawdown_pct=10.0,
                max_open_positions=3,
                max_daily_trades=10,
                max_consecutive_losses=3,
                kill_switch_balance_pct=80.0,
            )

    def test_kill_switch_bounds(self):
        from config.settings import RiskSettings
        with pytest.raises(ValidationError):
            RiskSettings(
                risk_pct_per_trade=0.5,
                max_daily_drawdown_pct=3.0,
                max_weekly_drawdown_pct=6.0,
                max_monthly_drawdown_pct=10.0,
                max_open_positions=3,
                max_daily_trades=10,
                max_consecutive_losses=3,
                kill_switch_balance_pct=5.0,  # < 10.0
            )
