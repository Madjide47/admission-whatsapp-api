"""Valeurs des champs dynamiques saisies par un étudiant pour sa candidature."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ApplicationFieldValue(Base):
    """Valeur d'un champ dynamique pour une candidature donnée.

    Une ligne par (application, form_field) rempli par l'étudiant.
    """

    __tablename__ = "application_field_values"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("form_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    application: Mapped["Application"] = relationship(  # type: ignore[name-defined]
        back_populates="field_values"
    )
    field: Mapped["FormField"] = relationship(  # type: ignore[name-defined]
        back_populates="field_values"
    )

    def __repr__(self) -> str:
        return f"<ApplicationFieldValue app={self.application_id} field={self.field_id}>"
