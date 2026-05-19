"""Modèle WebhookDelivery — log de chaque tentative de livraison de webhook."""
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WebhookStatus(str, enum.Enum):
    """État d'une livraison de webhook."""

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WebhookDelivery(Base):
    """Enregistre chaque envoi de webhook vers une université.

    Chaque tentative incrémente attempt_count. La table sert d'audit
    et permet de rejouer manuellement un événement.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # event_type : application.validated, application.decision.requested, etc.
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # idempotency_key — garantit qu'un même événement n'est pas dédupliqué
    # côté université. Utilisé dans le header X-Webhook-Event-Id.
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[WebhookStatus] = mapped_column(
        Enum(WebhookStatus, name="webhook_status_enum"),
        default=WebhookStatus.PENDING,
        nullable=False,
        index=True,
    )
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relations
    university: Mapped["University"] = relationship(  # type: ignore[name-defined]
        back_populates="webhook_deliveries"
    )
    application: Mapped["Application | None"] = relationship(  # type: ignore[name-defined]
        back_populates="webhook_deliveries"
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookDelivery id={self.id} event={self.event_type} "
            f"status={self.status.value} attempts={self.attempt_count}>"
        )
