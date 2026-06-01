"""Schémas Pydantic pour la configuration et le suivi des webhooks."""
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.webhook_log import WebhookStatus


# ---------------- Configuration ----------------
class WebhookConfigUpdate(BaseModel):
    """Mise à jour de la configuration webhook d'une université."""

    webhook_url: HttpUrl = Field(
        ...,
        description="URL HTTPS qui recevra les événements signés",
    )
    regenerate_secret: bool = Field(
        default=False,
        description="Si true, génère un nouveau secret HMAC et le retourne en clair",
    )


class WebhookConfigRead(BaseModel):
    """Représentation de la configuration webhook (sans le secret en clair)."""

    model_config = ConfigDict(from_attributes=True)

    webhook_url: str | None
    has_secret: bool


class WebhookConfigCreatedSecret(BaseModel):
    """Réponse exceptionnelle qui inclut le secret en clair après génération."""

    webhook_url: str
    webhook_secret: str = Field(
        ...,
        description="Secret HMAC en clair — affiché UNE SEULE FOIS, à stocker côté université",
    )


# ---------------- Livraisons ----------------
class WebhookDeliveryRead(BaseModel):
    """Log d'une tentative de livraison de webhook."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    idempotency_key: str
    status: WebhookStatus
    attempt_count: int
    last_attempt_at: datetime | None
    response_status_code: int | None
    last_error: str | None
    created_at: datetime


# ---------------- Payload émis ----------------
class WebhookPayloadEnvelope(BaseModel):
    """Enveloppe standard des payloads webhook."""

    event: str
    timestamp: int = Field(..., description="Timestamp Unix (anti-replay)")
    idempotency_key: str = Field(..., description="UUID unique par événement")
    data: dict[str, Any]
