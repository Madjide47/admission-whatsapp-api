"""Modèle Document — fichier rattaché à une candidature."""
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DocumentType(str, enum.Enum):
    """Type de document attendu pour une candidature."""

    DIPLOME = "DIPLOME"
    RELEVE_NOTES = "RELEVE_NOTES"
    CARTE_IDENTITE = "CARTE_IDENTITE"
    PHOTO = "PHOTO"
    AUTRE = "AUTRE"


class Document(Base):
    """Document fourni par un étudiant (PDF, image)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type_enum"),
        default=DocumentType.AUTRE,
        nullable=False,
        index=True,
    )

    # Métadonnées du fichier
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)

    # Chemin dans GCS — toujours obligatoire, jamais stocké localement
    gcs_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # Résultat OCR brut
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Résultat brut du classifier IA (JSONB)
    classification_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Liste d'erreurs de validation (JSONB pour requêtes Postgres natives)
    validation_errors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relation
    application: Mapped["Application"] = relationship(  # type: ignore[name-defined]
        back_populates="documents"
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id} type={self.document_type.value} "
            f"valid={self.is_valid}>"
        )
