"""Tâches Celery liées à l'OCR et à l'ingestion des médias WhatsApp."""
import io
import logging
import uuid

import httpx
from celery import shared_task

from app.config import settings
from app.database import get_db_session
from app.models.application import Application
from app.models.document import Document, DocumentType
from app.services.ocr_service import get_ocr_service
from app.services.storage_service import get_storage_service

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="app.workers.ocr_tasks.process_incoming_media",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(httpx.HTTPError,),
)
def process_incoming_media(
    self,
    application_id: str,
    media_url: str,
    media_content_type: str | None = None,
    hint_document_type: str | None = None,
) -> str:
    """Télécharge un média Twilio, le stocke dans GCS, lance l'OCR.

    Twilio héberge les médias sur des URLs nécessitant Basic Auth avec
    Account SID + Auth Token. On délègue ensuite à la chaîne IA.
    """
    db = get_db_session()
    try:
        application = db.get(Application, uuid.UUID(application_id))
        if application is None:
            logger.error("Application introuvable: %s", application_id)
            return "no_application"

        # Téléchargement depuis Twilio
        with httpx.Client(
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            response = client.get(media_url)
            response.raise_for_status()
            content = response.content
            mime = response.headers.get("Content-Type", media_content_type or "application/octet-stream")

        # Upload GCS
        storage = get_storage_service()
        filename = media_url.split("/")[-1] or "document"
        gcs_path = storage.upload_document(
            application_id=application.id,
            file_stream=io.BytesIO(content),
            original_filename=filename,
            mime_type=mime,
        )

        # Création du Document
        document = Document(
            application_id=application.id,
            document_type=DocumentType(hint_document_type) if hint_document_type else DocumentType.AUTRE,
            original_filename=filename,
            mime_type=mime,
            file_size=len(content),
            gcs_path=gcs_path,
            is_valid=False,
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        # Lancement OCR + IA en chaîne
        run_ocr_task.delay(str(document.id))
        return str(document.id)
    finally:
        db.close()


@shared_task(
    bind=True,
    name="app.workers.ocr_tasks.run_ocr",
    max_retries=3,
    default_retry_delay=30,
)
def run_ocr_task(self, document_id: str) -> str:
    """Effectue l'OCR sur un document déjà stocké dans GCS."""
    db = get_db_session()
    try:
        document = db.get(Document, uuid.UUID(document_id))
        if document is None:
            logger.error("Document introuvable: %s", document_id)
            return "no_document"

        storage = get_storage_service()
        content = storage.download_to_bytes(document.gcs_path)

        ocr = get_ocr_service()
        text = ocr.extract_text(content, mime_type=document.mime_type)

        document.ocr_text = text
        db.add(document)
        db.commit()

        # Enchaîne sur la classification IA
        from app.workers.ai_tasks import classify_document_task

        classify_document_task.delay(document_id)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erreur OCR document %s: %s", document_id, exc)
        raise self.retry(exc=exc) from exc
    finally:
        db.close()
