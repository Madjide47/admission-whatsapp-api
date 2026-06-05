"""Modèle University — un client API (une université)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class University(Base):
    """Université cliente de l'API SaaS.

    Détient :
      - une paire (API key, API secret) hashée pour l'auth des requêtes,
      - une URL et un secret de webhook pour recevoir les événements.
    """

    __tablename__ = "universities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # Auth API — on stocke uniquement les hashs bcrypt
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    api_secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Préfixe public de l'API key (les 12 premiers caractères) — permet
    # de retrouver rapidement la bonne ligne lors de la vérification bcrypt.
    api_key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Webhook — URL où POST les événements, secret HMAC pour signer le payload
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relations inverses
    applications: Mapped[list["Application"]] = relationship(  # type: ignore[name-defined]
        back_populates="university",
        cascade="all, delete-orphan",
    )
    programs: Mapped[list["Program"]] = relationship(  # type: ignore[name-defined]
        back_populates="university",
        cascade="all, delete-orphan",
    )
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(  # type: ignore[name-defined]
        back_populates="university",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<University id={self.id} name={self.name!r}>"
