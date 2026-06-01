"""Schémas Pydantic pour les documents."""
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentType


class DocumentCreate(BaseModel):
    """Création d'un document associé à une candidature.

    Le fichier est envoyé en multipart/form-data ; ce schéma est utilisé
    en complément pour les métadonnées.
    """

    document_type: DocumentType = Field(
        default=DocumentType.AUTRE,
        description="Type de document attendu. Sera reclassifié par l'IA après OCR.",
    )


class DocumentRead(BaseModel):
    """Représentation complète d'un document."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    document_type: DocumentType
    original_filename: str | None
    mime_type: str | None
    file_size: int | None
    gcs_path: str
    ocr_text: str | None
    classification_result: dict[str, Any] | None
    is_valid: bool
    validation_errors: list[str] | None
    uploaded_at: datetime


class DocumentClassificationResult(BaseModel):
    """Résultat structuré renvoyé par le service de classification IA."""

    type: DocumentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
