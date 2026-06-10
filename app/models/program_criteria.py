"""Modèle ProgramCriteria — prérequis et critères d'admission d'un programme.

Configuré par l'admin (via l'API ou le chatbot admin) et réutilisé à la fois
dans le chat admin (filtrage des candidatures) et le bot WhatsApp (affichage
des prérequis au moment du choix de programme).
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JsonB


class ProgramCriteria(Base):
    """Critères d'admission configurables pour un programme (1 par programme)."""

    __tablename__ = "program_criteria"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Liste de prérequis textuels libres (ex : "Bac+3 minimum", "Lettre de motivation")
    prerequisites: Mapped[list | None] = mapped_column(JsonB, nullable=True)
    # Moyenne minimale requise (ex : 14.00). Null = pas de seuil.
    min_average: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    # Niveau de diplôme requis (ex : "Bac+3")
    required_degree: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Spécialités acceptées (ex : ["Économie", "Gestion"])
    accepted_specialties: Mapped[list | None] = mapped_column(JsonB, nullable=True)
    additional_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Afficher ces prérequis dans le bot WhatsApp au choix du programme
    whatsapp_display: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    updated_by_admin: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    program: Mapped["Program"] = relationship(  # type: ignore[name-defined]
        back_populates="criteria"
    )

    def has_any_prerequisite(self) -> bool:
        """True si au moins un critère exploitable est configuré."""
        return bool(
            (self.prerequisites and len(self.prerequisites) > 0)
            or self.min_average is not None
            or self.required_degree
            or (self.accepted_specialties and len(self.accepted_specialties) > 0)
        )

    def __repr__(self) -> str:
        return f"<ProgramCriteria program_id={self.program_id} min_average={self.min_average}>"
