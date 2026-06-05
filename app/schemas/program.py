"""Schémas Pydantic pour les programmes, formulaires et champs dynamiques."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.program import FieldType


# ======================================================================
# FormField
# ======================================================================
class FormFieldCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=255)
    field_type: FieldType = FieldType.TEXT
    order: int = Field(0, ge=0)
    is_required: bool = True
    validation_regex: str | None = Field(None, max_length=500)


class FormFieldRead(FormFieldCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    form_id: uuid.UUID
    created_at: datetime


# ======================================================================
# RequiredDocument
# ======================================================================
class RequiredDocumentCreate(BaseModel):
    document_type: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=255)
    order: int = Field(0, ge=0)
    is_required: bool = True


class RequiredDocumentRead(RequiredDocumentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    form_id: uuid.UUID


# ======================================================================
# AdmissionForm
# ======================================================================
class AdmissionFormUpdate(BaseModel):
    """Payload PUT /admin/forms/{program_id} — remplace toute la config du formulaire."""
    fields: list[FormFieldCreate] = Field(default_factory=list)
    required_documents: list[RequiredDocumentCreate] = Field(default_factory=list)


class AdmissionFormRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    program_id: uuid.UUID
    is_published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    fields: list[FormFieldRead] = []
    required_documents: list[RequiredDocumentRead] = []


# ======================================================================
# Program
# ======================================================================
class ProgramCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    is_active: bool = True


class ProgramUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    is_active: bool | None = None


class ProgramRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    university_id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProgramReadWithForm(ProgramRead):
    """Programme avec son formulaire publié (si existant)."""
    published_form: AdmissionFormRead | None = None


# ======================================================================
# Seed university
# ======================================================================
class SeedUniversityRequest(BaseModel):
    name: str = Field("Université Demo", min_length=1, max_length=255)
    email: str = Field("demo@universite-test.local", max_length=255)


class SeedUniversityResponse(BaseModel):
    university_id: uuid.UUID
    name: str
    email: str
    api_key: str
    api_secret: str
    webhook_secret: str
    program_id: uuid.UUID
    program_name: str
    form_id: uuid.UUID
    warning: str = "Ces credentials sont affichés une seule fois. Conservez-les."
