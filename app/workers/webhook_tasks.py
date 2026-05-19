"""Tâches Celery d'envoi de webhooks avec backoff exponentiel.

Retries explicites : 1min, 5min, 15min, 1h, 6h.
Chaque tentative est loggée dans la table WebhookDelivery.
"""
import logging
import uuid

from celery import shared_task

from app.database import get_db_session
from app.models.application import Application, ApplicationStatus
from app.models.university import University
from app.models.webhook_log import WebhookDelivery, WebhookStatus
from app.services.storage_service import get_storage_service
from app.services.webhook_service import WebhookService

logger = logging.getLogger(__name__)

# Délais de retry en secondes : 1min, 5min, 15min, 1h, 6h
RETRY_DELAYS = [60, 300, 900, 3600, 21600]


# ----------------------------------------------------------------------
# Construction du payload "application.validated"
# ----------------------------------------------------------------------
@shared_task(name="app.workers.webhook_tasks.dispatch_validated_application")
def dispatch_validated_application_task(application_id: str) -> str:
    """Construit le payload d'un dossier validé et déclenche l'envoi."""
    db = get_db_session()
    try:
        application = db.get(Application, uuid.UUID(application_id))
        if application is None:
            logger.error("Application introuvable: %s", application_id)
            return "no_application"

        if application.status != ApplicationStatus.VALIDATED:
            logger.info("Application %s pas en VALIDATED (%s) — skip", application.id, application.status)
            return "skipped"

        university = db.get(University, application.university_id)
        if university is None or not university.is_active:
            logger.warning("Université inactive ou introuvable pour app %s", application.id)
            return "no_university"

        # Construire la liste documents (avec URL signée)
        storage = get_storage_service()
        docs_payload = []
        for doc in application.documents:
            docs_payload.append(
                {
                    "id": str(doc.id),
                    "type": doc.document_type.value,
                    "gcs_url": storage.generate_signed_url(doc.gcs_path),
                    "ocr_text": (doc.ocr_text or "")[:2000],
                    "is_valid": doc.is_valid,
                    "classification": doc.classification_result,
                }
            )

        data = {
            "application_id": str(application.id),
            "student": {
                "phone": application.student_phone,
                "name": application.student_name,
                "email": application.student_email,
            },
            "program": application.program,
            "documents": docs_payload,
            "validation_score": application.validation_score,
            "ai_notes": application.ai_notes,
            "submitted_at": application.created_at.isoformat() if application.created_at else None,
        }

        service = WebhookService(db)
        delivery = service.create_delivery(
            university=university,
            event_type="application.validated",
            data=data,
            application_id=application.id,
        )

        # Passe l'application en SENT_TO_UNIVERSITY avant tentative d'envoi
        application.status = ApplicationStatus.SENT_TO_UNIVERSITY
        db.add(application)
        db.commit()

        send_webhook_task.delay(str(delivery.id))
        return str(delivery.id)
    finally:
        db.close()


@shared_task(name="app.workers.webhook_tasks.dispatch_decision_acknowledged")
def dispatch_decision_acknowledged_task(application_id: str) -> str:
    """Webhook optionnel — accusé de réception d'une décision côté SaaS."""
    db = get_db_session()
    try:
        application = db.get(Application, uuid.UUID(application_id))
        if application is None:
            return "no_application"
        university = db.get(University, application.university_id)
        if university is None:
            return "no_university"

        service = WebhookService(db)
        delivery = service.create_delivery(
            university=university,
            event_type="application.decision.acknowledged",
            data={
                "application_id": str(application.id),
                "decision": application.status.value,
                "comment": application.decision_comment,
                "decided_at": application.decided_at.isoformat() if application.decided_at else None,
            },
            application_id=application.id,
        )
        send_webhook_task.delay(str(delivery.id))
        return str(delivery.id)
    finally:
        db.close()


# ----------------------------------------------------------------------
# Envoi effectif avec retry exponentiel
# ----------------------------------------------------------------------
@shared_task(bind=True, name="app.workers.webhook_tasks.send_webhook", max_retries=len(RETRY_DELAYS))
def send_webhook_task(self, delivery_id: str) -> str:
    """Envoie un webhook et le replanifie en cas d'échec."""
    db = get_db_session()
    try:
        delivery = db.get(WebhookDelivery, uuid.UUID(delivery_id))
        if delivery is None:
            logger.error("WebhookDelivery introuvable: %s", delivery_id)
            return "no_delivery"

        if delivery.status == WebhookStatus.SUCCESS:
            return "already_success"

        service = WebhookService(db)
        success = service.send(delivery)

        if success:
            return "success"

        # Échec — on planifie le prochain retry si on en a encore en stock
        attempt_index = self.request.retries  # 0 lors du 1er échec
        if attempt_index < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt_index]
            logger.info(
                "Webhook %s replanifié dans %ds (tentative %d/%d)",
                delivery.id,
                delay,
                attempt_index + 1,
                len(RETRY_DELAYS),
            )
            raise self.retry(countdown=delay, exc=Exception("Webhook failed"))

        # Toutes les tentatives épuisées
        delivery.status = WebhookStatus.FAILED
        db.add(delivery)
        db.commit()
        logger.error("Webhook %s définitivement échoué après %d tentatives", delivery.id, delivery.attempt_count)
        return "exhausted"
    finally:
        db.close()
