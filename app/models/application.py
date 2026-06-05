"""Modèle Application — une candidature étudiante."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ApplicationStatus(str, enum.Enum):
    """États possibles d'une candidature — flux complet v2."""

    CHOOSING_UNIVERSITY = "CHOOSING_UNIVERSITY"      # WhatsApp : choix de l'université
    CHOOSING_PROGRAM = "CHOOSING_PROGRAM"             # WhatsApp : choix du programme
    COLLECTING_FIELDS = "COLLECTING_FIELDS"           # Saisie des champs du formulaire
    COLLECTING_DOCUMENTS = "COLLECTING_DOCUMENTS"     # Upload des documents requis
    VALIDATING = "VALIDATING"                         # Validation OCR + IA en cours
    VALIDATED = "VALIDATED"                           # Dossier complet et validé
    SENT_TO_UNIVERSITY = "SENT_TO_UNIVERSITY"         # Webhook envoyé à l'université
    ACCEPTED = "ACCEPTED"                             # Décision favorable
    REJECTED = "REJECTED"                             # Décision défavorable


class Application(Base):
    """Candidature d'un étudiant à un programme universitaire."""

    __tablename__ = "applications"

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
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Étudiant
    student_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    student_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    student_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Nom du programme (chaîne libre, conservé pour compatibilité)
    program: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status_enum"),
        default=ApplicationStatus.COLLECTING_FIELDS,
        nullable=False,
        index=True,
    )

    validation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_state: Mapped[str | None] = mapped_column(String(50), nullable=True)

    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relations
    university: Mapped["University"] = relationship(  # type: ignore[name-defined]
        back_populates="applications"
    )
    program_obj: Mapped["Program | None"] = relationship(  # type: ignore[name-defined]
        back_populates="applications",
        foreign_keys=[program_id],
    )
    documents: Mapped[list["Document"]] = relationship(  # type: ignore[name-defined]
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Document.uploaded_at",
    )
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(  # type: ignore[name-defined]
        back_populates="application",
        cascade="all, delete-orphan",
    )
    field_values: Mapped[list["ApplicationFieldValue"]] = relationship(  # type: ignore[name-defined]
        back_populates="application",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Application id={self.id} phone={self.student_phone} "
            f"status={self.status.value}>"
        )
