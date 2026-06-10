"""Modèles SQLAlchemy — tous importés ici pour que Base.metadata les connaisse."""
from app.models.university import University
from app.models.program import (
    Program,
    AdmissionForm,
    FormField,
    FieldType,
    RequiredDocument,
)
from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.webhook_log import WebhookDelivery, WebhookStatus
from app.models.form_field_value import ApplicationFieldValue
from app.models.program_criteria import ProgramCriteria
from app.models.admin_chat import AdminChatSession

__all__ = [
    "University",
    "Program",
    "AdmissionForm",
    "FormField",
    "FieldType",
    "RequiredDocument",
    "Application",
    "ApplicationStatus",
    "Document",
    "DocumentType",
    "WebhookDelivery",
    "WebhookStatus",
    "ApplicationFieldValue",
    "ProgramCriteria",
    "AdminChatSession",
]
