from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment; secrets are never logged."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_prefix="RECOVER_",
        extra="ignore",
    )

    database_url: str
    razorpay_webhook_secret: str
    merchant_id: str = "local-test-merchant"
    log_level: str = "INFO"
    worker_lease_seconds: int = 60
    worker_max_attempts: int = 5
    worker_retry_base_seconds: int = 5
    worker_retry_max_seconds: int = 300
    worker_poll_seconds: float = 1.0
    razorpay_mode: str = "test"
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    payment_link_expiry_seconds: int = 259200
    ai_advisor_enabled: bool = False
    ai_advisor_provider: str = "DISABLED"
    ai_advisor_model: str = ""
    ai_advisor_timeout_seconds: float = 5.0
    ai_advisor_max_output_tokens: int = 400
    ai_advisor_confidence_threshold: float = 0.70
    ai_advisor_base_url: str | None = None
    ai_advisor_api_key: str | None = None
    reporting_auth_required: bool = False
