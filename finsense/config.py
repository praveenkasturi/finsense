from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FINSENSE_")

    app_name: str = "FinSense Stock Intelligence"
    app_env: str = "dev"
    max_position_pct: float = Field(default=5.0, ge=0.1, le=25.0)
    var_limit_pct: float = Field(default=1.5, ge=0.1, le=10.0)
    confidence_threshold_buy: float = Field(default=0.62, ge=0.4, le=0.95)
    confidence_threshold_sell: float = Field(default=0.62, ge=0.4, le=0.95)
    max_portfolio_var_pct: float = Field(default=3.0, ge=0.5, le=15.0)
    max_correlation_cluster: float = Field(default=0.75, ge=0.3, le=1.0)
    enable_ml_training: bool = True


settings = Settings()
