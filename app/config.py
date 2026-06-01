"""Configuration centralisée chargée depuis les variables d'environnement.

Utilise pydantic-settings pour valider et typer toutes les variables.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Paramètres de l'application — chargés depuis .env ou variables d'env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------- Environnement ----------------
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    SECRET_KEY: str = Field(..., min_length=32)

    # ---------------- Base de données ----------------
    DATABASE_URL: str = Field(
        ...,
        description="URL SQLAlchemy, ex: postgresql+psycopg://user:pwd@host:5432/db",
    )
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # ---------------- Redis & Celery ----------------
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # ---------------- Twilio WhatsApp ----------------
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_WHATSAPP_NUMBER: str = Field(
        ...,
        description="Format requis: whatsapp:+14155238886",
    )

    # ---------------- Anthropic Claude ----------------
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 1024
    ANTHROPIC_TIMEOUT: int = 30

    # ---------------- Google Cloud Storage ----------------
    GCS_BUCKET_NAME: str
    GCS_PROJECT_ID: str
    GOOGLE_APPLICATION_CREDENTIALS: str | None = None
    GCS_SIGNED_URL_EXPIRY_MINUTES: int = 60

    # ---------------- Webhooks ----------------
    WEBHOOK_TIMESTAMP_TOLERANCE: int = 300  # secondes — fenêtre anti-replay
    WEBHOOK_TIMEOUT: int = 10
    WEBHOOK_MAX_RETRIES: int = 5

    # ---------------- Monitoring ----------------
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # ---------------- Rate limiting ----------------
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_WHATSAPP: str = "30/minute"

    # ---------------- CORS ----------------
    CORS_ALLOWED_ORIGINS: str = ""

    # ---------------- OCR ----------------
    TESSERACT_LANG: str = "fra+eng"
    TESSERACT_CMD: str = ""

    # ---------------- Mode démo ----------------
    # Active les stubs locaux : Twilio → console, GCS → filesystem, IA → mock
    DEMO_MODE: bool = False

    # ---------------- Interface Admin ----------------
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""  # obligatoire en production

    # ---------------- Calculés / dérivés ----------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        """Convertit la chaîne CSV en liste, vide en dev."""
        if not self.CORS_ALLOWED_ORIGINS.strip():
            return ["*"] if not self.is_production else []
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @field_validator("TWILIO_WHATSAPP_NUMBER")
    @classmethod
    def validate_whatsapp_number(cls, v: str) -> str:
        if not v.startswith("whatsapp:+"):
            raise ValueError(
                "TWILIO_WHATSAPP_NUMBER doit commencer par 'whatsapp:+' "
                "(ex: whatsapp:+14155238886)"
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne l'instance singleton de Settings (cache mémoire)."""
    return Settings()


settings = get_settings()
