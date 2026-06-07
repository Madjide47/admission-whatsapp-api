"""Tâches Celery de classification IA et de validation finale."""
import logging
import uuid

from celery import shared_task

from sqlalchemy import select

from app.database import get_db_session
from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import Program
from app.services.ai_classifier import get_ai_classifier
from app.services.storage_service import get_storage_service
from app.services.validator import ApplicationValidator
from app.services.whatsapp_bot import DOCUMENT_LABELS, get_next_required_document, send_whatsapp

logger = logging.getLogger(__name__)


def _send_document_feedback(
    application: Application,
    detected_type: DocumentType,
    awaited_type: DocumentType | None,
    is_valid: bool,
    errors: list[str],
) -> None:
    """Envoie un message WhatsApp à l'étudiant après classification d'un document.

    - is_valid : le document est du bon type ET exploitable → confirmation + doc suivant.
    - mauvais type (detected_type != awaited_type) → on explique qu'on attendait autre chose.
    - bon type mais illisible/incomplet → on demande de renvoyer plus net.
    """
    if is_valid:
        label_cap = DOCUMENT_LABELS.get(detected_type, detected_type.value).replace("*", "").strip().capitalize()
        db_fresh = get_db_session()
        try:
            fresh_app = db_fresh.get(Application, application.id)
            next_doc = get_next_required_document(fresh_app) if fresh_app else None
        finally:
            db_fresh.close()

        if next_doc:
            msg = f"✅ {label_cap} validé !\n\nEnvoyez maintenant {DOCUMENT_LABELS[next_doc]}."
        else:
            msg = f"✅ {label_cap} validé ! Tous vos documents sont reçus, validation finale en cours..."
    elif awaited_type is not None and detected_type != awaited_type:
        awaited_label = DOCUMENT_LABELS.get(awaited_type, awaited_type.value)
        msg = (
            f"❌ Ce document ne correspond pas à ce qui est demandé.\n\n"
            f"Merci d'envoyer {awaited_label}."
        )
    else:
        awaited_label = DOCUMENT_LABELS.get(awaited_type, awaited_type.value) if awaited_type else ""
        label_cap = (awaited_label or DOCUMENT_LABELS.get(detected_type, detected_type.value)).replace("*", "").strip().capitalize()
        errors_str = "\n• ".join(errors) if errors else "Document illisible ou incomplet"
        msg = (
            f"❌ {label_cap} non valide :\n• {errors_str}\n\n"
            "Merci de renvoyer un document plus lisible (bonne lumière, texte net)."
        )

    send_whatsapp(application.student_phone, msg)


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

        application = db.get(Application, document.application_id)

        # Type attendu à cette étape : l'indice de légende s'il existe, sinon
        # le prochain document requis dans l'ordre de collecte. On ne passe que
        # l'indice explicite au modèle (pour ne PAS le biaiser et lui faire
        # valider une image quelconque comme « diplôme »).
        hinted = document.document_type if document.document_type != DocumentType.AUTRE else None
        awaited = hinted or (get_next_required_document(application) if application else None)

        classifier = get_ai_classifier()
        mime = (document.mime_type or "").lower()
        result = None

        # Pour une image, on envoie la VRAIE image à l'IA (vision) : seul moyen
        # de valider une photo d'identité (sans texte) et plus fiable que l'OCR.
        if mime.startswith("image/"):
            try:
                image_bytes = get_storage_service().download_to_bytes(document.gcs_path)
                result = classifier.classify_image(
                    image_bytes=image_bytes,
                    mime_type=document.mime_type,
                    ocr_text=document.ocr_text or "",
                    expected_type=hinted,
                )
            except Exception:
                logger.warning(
                    "Analyse vision indisponible pour %s — repli sur le texte OCR",
                    document_id,
                    exc_info=True,
                )

        # PDF, ou repli si la vision a échoué : classification basée sur le texte OCR.
        if result is None:
            result = classifier.classify(
                ocr_text=document.ocr_text or "",
                expected_type=hinted,
            )

        # Le document doit être du type attendu. Sinon (ex : photo random envoyée
        # à la place du diplôme), on le refuse pour cette étape.
        type_ok = awaited is None or result.type == awaited
        is_valid = result.is_valid and type_ok

        errors = list(result.errors or [])
        if not type_ok:
            awaited_label = DOCUMENT_LABELS.get(awaited, awaited.value).replace("*", "").strip()
            errors = [f"Document attendu : {awaited_label} (type détecté : {result.type.value})."] + errors

        document.document_type = result.type
        document.classification_result = result.model_dump(mode="json")
        document.is_valid = is_valid
        document.validation_errors = errors if errors else None
        db.add(document)
        db.commit()
        db.refresh(document)

        # Feedback WhatsApp immédiat sur ce document
        try:
            if application:
                _send_document_feedback(application, result.type, awaited, is_valid, errors)
        except Exception:
            logger.warning("Impossible d'envoyer le feedback WhatsApp post-classification", exc_info=True)

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
            # Vérifier si la période d'inscription du programme est ouverte
            program = db.execute(
                select(Program).where(
                    Program.university_id == application.university_id,
                    Program.name == application.program,
                    Program.is_active.is_(True),
                )
            ).scalar_one_or_none()

            if program and not program.is_enrollment_open():
                # Inscriptions fermées → mise en attente, pas d'envoi immédiat
                application.status = ApplicationStatus.PENDING_ENROLLMENT
                db.add(application)
                db.commit()
                try:
                    send_whatsapp(
                        application.student_phone,
                        "✅ Vos documents ont tous été validés, votre dossier est complet !\n\n"
                        "🗓️ Les inscriptions ne sont pas encore ouvertes. Votre candidature sera "
                        "automatiquement envoyée à l'université dès l'ouverture. "
                        "Nous vous tiendrons informé(e). 🙏",
                    )
                except Exception:
                    logger.warning("Impossible d'envoyer la notification PENDING_ENROLLMENT", exc_info=True)
                return application.status.value

            try:
                send_whatsapp(
                    application.student_phone,
                    "🎉 Votre dossier est complet et a été transmis à l'université !\n\n"
                    "Vous recevrez une réponse ici dès qu'une décision sera prise. 🙏",
                )
            except Exception:
                logger.warning("Impossible d'envoyer la notification de validation", exc_info=True)

            from app.workers.webhook_tasks import dispatch_validated_application_task

            dispatch_validated_application_task.delay(str(application.id))

        return application.status.value
    finally:
        db.close()
