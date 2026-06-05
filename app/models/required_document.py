"""Modèle RequiredDocument — document obligatoire pour un programme donné.

# TODO(dev1): valider la structure et créer la migration Alembic
#             (ex: alembic/versions/0003_add_required_documents.py)
#
# Ce modèle est un stub créé par Dev 2 pour le validator dynamique et le
# flow séquentiel du bot WhatsApp. Dev 1 doit valider, ajouter la relation
# inverse sur Program, et générer la migration.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.document import DocumentType


class RequiredDocument(Base):
    """Document obligatoire (ou optionnel) pour un programme académique.

    L'ordre (order) détermine dans quel ordre le bot demande les documents.
    """

    __tablename__ = "required_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        sa.Enum(DocumentType, name="document_type_enum"),
        nullable=False,
    )
    is_required: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    label: Mapped[str | None] = mapped_column(
        sa.String(255),
        nullable=True,
        comment="Libellé affiché à l'étudiant (ex: 'Diplôme du baccalauréat')",
    )
    order: Mapped[int] = mapped_column(
        sa.Integer,
        default=0,
        nullable=False,
        comment="Ordre de demande séquentielle dans le bot",
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<RequiredDocument program={self.program_id} "
            f"type={self.document_type.value} required={self.is_required}>"
        )
