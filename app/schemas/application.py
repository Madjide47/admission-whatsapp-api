"""Schémas Pydantic pour les candidatures (Application)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.application import ApplicationStatus


# ---------------- Base ----------------
class ApplicationBase(BaseModel):
    """Champs communs à la création/lecture."""

    student_phone: str = Field(
        ...,
        min_length=8,
        max_length=32,
        description="Numéro de téléphone au format international, ex: +22890123456",
    )
    student_name: str | None = Field(None, max_length=255)
    student_email: EmailStr | None = None
    program: str | None = Field(None, max_length=255)


# ---------------- Entrées ----------------
class ApplicationCreate(ApplicationBase):
    """Création d'une candidature manuellement via l'API."""


class ApplicationUpdate(BaseModel):
    """Mise à jour partielle d'une candidature (PATCH)."""

    student_name: str | None = Field(None, max_length=255)
    student_email: EmailStr | None = None
    program: str | None = Field(None, max_length=255)
    status: ApplicationStatus | None = None
    ai_notes: str | None = None


class ApplicationDecisionRequest(BaseModel):
    """Décision envoyée par l'université sur une candidature."""

    decision: ApplicationStatus = Field(
        ...,
        description="ACCEPTED ou REJECTED uniquement",
    )
    comment: str | None = Field(
        None,
        max_length=2000,
        description="Commentaire optionnel transmis à l'étudiant via WhatsApp",
    )


# ---------------- Sorties ----------------
class DocumentRead(BaseModel):
    """Document inclus dans la réponse d'une candidature."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_type: str
    gcs_path: str
    is_valid: bool
    ocr_text: str | None = None
    validation_errors: list[str] | None = None
    uploaded_at: datetime


class ApplicationRead(ApplicationBase):
    """Représentation complète d'une candidature."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    university_id: uuid.UUID
    program_id: uuid.UUID | None = None
    status: ApplicationStatus
    validation_score: float | None = None
    ai_notes: str | None = None
    decision_comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentRead] = []


class ApplicationListItem(BaseModel):
    """Version allégée pour les listes (sans documents)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_phone: str
    student_name: str | None
    program: str | None
    status: ApplicationStatus
    validation_score: float | None
    created_at: datetime
    updated_at: datetime
