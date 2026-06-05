"""Modèles pour les formulaires d'admission dynamiques v2.

Hiérarchie :
  University → Program → AdmissionForm → FormField / RequiredDocument
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FieldType(str, enum.Enum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    TEXTAREA = "textarea"


class Program(Base):
    """Programme universitaire proposé par une université cliente."""

    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Domaine académique (ex: Informatique, Droit) — filtre bot WhatsApp",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Période d'inscription — null = aucune restriction sur cette borne
    enrollment_start: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date d'ouverture des inscriptions (null = toujours ouvert)",
    )
    enrollment_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date de fermeture des inscriptions (null = pas de limite)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    university: Mapped["University"] = relationship(back_populates="programs")  # type: ignore[name-defined]
    admission_forms: Mapped[list["AdmissionForm"]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
    )
    applications: Mapped[list["Application"]] = relationship(  # type: ignore[name-defined]
        back_populates="program_obj",
    )

    def is_enrollment_open(self, reference_date: date | None = None) -> bool:
        """Retourne True si les inscriptions sont ouvertes à la date donnée (ou aujourd'hui)."""
        today = reference_date or date.today()
        if self.enrollment_start and today < self.enrollment_start:
            return False
        if self.enrollment_end and today > self.enrollment_end:
            return False
        return True

    def __repr__(self) -> str:
        return f"<Program id={self.id} name={self.name!r}>"


class AdmissionForm(Base):
    """Formulaire d'admission configurable pour un programme.

    Un programme peut avoir un seul formulaire publié à la fois.
    """

    __tablename__ = "admission_forms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    program: Mapped["Program"] = relationship(back_populates="admission_forms")
    fields: Mapped[list["FormField"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormField.order",
    )
    required_documents: Mapped[list["RequiredDocument"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="RequiredDocument.order",
    )

    def __repr__(self) -> str:
        return f"<AdmissionForm id={self.id} published={self.is_published}>"


class FormField(Base):
    """Champ dynamique d'un formulaire d'admission (texte, email, date…)."""

    __tablename__ = "form_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    form_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admission_forms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=FieldType.TEXT.value
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_regex: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    form: Mapped["AdmissionForm"] = relationship(back_populates="fields")
    field_values: Mapped[list["ApplicationFieldValue"]] = relationship(  # type: ignore[name-defined]
        back_populates="field",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<FormField id={self.id} label={self.label!r} order={self.order}>"


class RequiredDocument(Base):
    """Document obligatoire ou optionnel défini par l'admin pour un formulaire."""

    __tablename__ = "required_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    form_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admission_forms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    form: Mapped["AdmissionForm"] = relationship(back_populates="required_documents")

    def __repr__(self) -> str:
        return f"<RequiredDocument id={self.id} type={self.document_type!r}>"
