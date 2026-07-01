"""Configuración global del bot mediante pydantic-settings. Carga variables desde .env."""

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ruta raíz del proyecto (dos niveles arriba de este archivo)
_ROOT = Path(__file__).parent.parent
_ENV_FILE = _ROOT / ".env"


class BotMode(str, Enum):
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


class MT5Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="MT5_", extra="ignore")

    login: int = Field(..., description="Número de cuenta MT5")
    password: str = Field(..., description="Contraseña de la cuenta MT5")
    server: str = Field(..., description="Nombre del servidor Pepperstone")
    path: str = Field(..., description="Ruta al ejecutable terminal64.exe")


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="DB_", extra="ignore")

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    name: str = Field(..., description="Nombre de la base de datos")
    user: str = Field(..., description="Usuario de PostgreSQL")
    password: str = Field(..., description="Contraseña de PostgreSQL")

    @property
    def url(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="TELEGRAM_", extra="ignore")

    bot_token: str = Field(..., description="Token del bot de Telegram")
    chat_id: str = Field(..., description="Chat ID donde se envían notificaciones")


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="", extra="ignore")

    risk_pct_per_trade: float = Field(default=0.5, ge=0.01, le=5.0)
    max_daily_drawdown_pct: float = Field(default=3.0, ge=0.1, le=20.0)
    max_weekly_drawdown_pct: float = Field(default=6.0, ge=0.1, le=30.0)
    max_monthly_drawdown_pct: float = Field(default=10.0, ge=0.1, le=50.0)
    max_open_positions: int = Field(default=3, ge=1, le=20)
    max_daily_trades: int = Field(default=10, ge=1, le=100)
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    kill_switch_balance_pct: float = Field(default=80.0, ge=10.0, le=99.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    bot_mode: BotMode = Field(default=BotMode.SHADOW, alias="BOT_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Umbral mínimo de confianza para que una señal pase a validación.
    # Señales por debajo se descartan y se registran con rejection_reason="low_confidence".
    min_confidence_to_trade: float = Field(
        default=0.75, ge=0.0, le=1.0, alias="MIN_CONFIDENCE_TO_TRADE"
    )

    mt5: MT5Settings = Field(default_factory=MT5Settings)
    db: DBSettings = Field(default_factory=DBSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)

    @property
    def mode(self) -> BotMode:
        return self.bot_mode


settings = Settings()
