"""Tâches Celery de classification IA et de validation finale."""
import logging
import uuid

from celery import shared_task

from app.database import get_db_session
from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.services.ai_classifier import get_ai_classifier
from app.services.validator import ApplicationValidator

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="app.workers.ai_tasks.classify_document",
    max_retries=3,
    default_retry_delay=60,
)
def classify_document_task(self, document_id: str) -> str:
    """Classifie un document via Claude à partir du texte OCR."""
    db = get_db_session()
    try:
        document = db.get(Document, uuid.UUID(document_id))
        if document is None:
            logger.error("Document introuvable: %s", document_id)
            return "no_document"

        classifier = get_ai_classifier()
        result = classifier.classify(
            ocr_text=document.ocr_text or "",
            expected_type=document.document_type if document.document_type != DocumentType.AUTRE else None,
        )

        document.document_type = result.type
        document.classification_result = result.model_dump(mode="json")
        document.is_valid = result.is_valid
        document.validation_errors = result.errors if result.errors else None
        db.add(document)
        db.commit()
        db.refresh(document)

        # Déclenche la revalidation globale du dossier
        check_application_completion_task.delay(str(document.application_id))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erreur classification IA %s: %s", document_id, exc)
        raise self.retry(exc=exc) from exc
    finally:
        db.close()


@shared_task(
    name="app.workers.ai_tasks.check_application_completion",
)
def check_application_completion_task(application_id: str) -> str:
    """Recalcule la complétude du dossier et déclenche le webhook si validé."""
    db = get_db_session()
    try:
        application = db.get(Application, uuid.UUID(application_id))
        if application is None:
            logger.error("Application introuvable: %s", application_id)
            return "no_application"

        validator = ApplicationValidator(db)
        application = validator.apply_validation(application)

        if application.status == ApplicationStatus.VALIDATED:
            # On enchaîne sur l'envoi du webhook
            from app.workers.webhook_tasks import dispatch_validated_application_task

            dispatch_validated_application_task.delay(str(application.id))

        return application.status.value
    finally:
        db.close()
