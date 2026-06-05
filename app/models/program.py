"""Modèle Program — programme académique proposé par une université.

# TODO(dev1): valider la structure et créer la migration Alembic
#             (ex: alembic/versions/0002_add_programs.py)
#
# Ce modèle est un stub créé par Dev 2 pour le flow de sélection du bot
# WhatsApp (CHOOSE_UNIVERSITY → CHOOSE_PROGRAM). Dev 1 doit le valider,
# ajouter la relation inverse sur University, et générer la migration.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Program(Base):
    """Programme académique rattaché à une université."""

    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Program id={self.id} name={self.name!r}>"
