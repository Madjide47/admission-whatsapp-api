"""Service WhatsApp Bot — conversation guidée via Twilio.

Gère la machine à états de la conversation :
    WELCOME → COLLECT_NAME → COLLECT_PROGRAM → COLLECT_DOCS
            → WAITING_VALIDATION → DONE
"""
import logging
import re
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from app.config import settings
from app.models.application import Application, ApplicationStatus
from app.models.document import DocumentType
from app.models.university import University

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """États de la machine de conversation WhatsApp."""

    WELCOME = "WELCOME"
    COLLECT_NAME = "COLLECT_NAME"
    COLLECT_PROGRAM = "COLLECT_PROGRAM"
    COLLECT_DOCS = "COLLECT_DOCS"
    WAITING_VALIDATION = "WAITING_VALIDATION"
    DONE = "DONE"


# Mapping mot-clé → type de document attendu (utile pour les uploads)
DOCUMENT_KEYWORDS: dict[str, DocumentType] = {
    "diplome": DocumentType.DIPLOME,
    "diplôme": DocumentType.DIPLOME,
    "bac": DocumentType.DIPLOME,
    "releve": DocumentType.RELEVE_NOTES,
    "relevé": DocumentType.RELEVE_NOTES,
    "notes": DocumentType.RELEVE_NOTES,
    "bulletin": DocumentType.RELEVE_NOTES,
    "carte": DocumentType.CARTE_IDENTITE,
    "identité": DocumentType.CARTE_IDENTITE,
    "identite": DocumentType.CARTE_IDENTITE,
    "cin": DocumentType.CARTE_IDENTITE,
    "passeport": DocumentType.CARTE_IDENTITE,
    "photo": DocumentType.PHOTO,
}


class WhatsAppBot:
    """Bot conversationnel pour collecter les candidatures."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self.from_number = settings.TWILIO_WHATSAPP_NUMBER

    # ------------------------------------------------------------------
    # Entrée principale
    # ------------------------------------------------------------------
    def handle_incoming_message(
        self,
        from_number: str,
        message_body: str,
        media_url: str | None = None,
        media_content_type: str | None = None,
        university: University | None = None,
    ) -> dict:
        """Traite un message WhatsApp entrant et fait progresser la conversation.

        Retourne un dict décrivant l'action effectuée — utilisé par les tests
        et le webhook Twilio. Le bot envoie aussi des messages sortants
        directement via l'API Twilio.
        """
        normalized = (message_body or "").strip()
        application = self._get_or_create_application(from_number, university)

        # Si un média est joint, c'est un upload de document à traiter
        if media_url:
            return self._handle_media(application, media_url, media_content_type, normalized)

        # Sinon, on suit la machine à états
        state = ConversationState(application.conversation_state or ConversationState.WELCOME.value)
        handler = {
            ConversationState.WELCOME: self._handle_welcome,
            ConversationState.COLLECT_NAME: self._handle_collect_name,
            ConversationState.COLLECT_PROGRAM: self._handle_collect_program,
            ConversationState.COLLECT_DOCS: self._handle_collect_docs,
            ConversationState.WAITING_VALIDATION: self._handle_waiting,
            ConversationState.DONE: self._handle_done,
        }[state]
        return handler(application, normalized)

    # ------------------------------------------------------------------
    # Handlers par état
    # ------------------------------------------------------------------
    def _handle_welcome(self, application: Application, text: str) -> dict:
        # Détection d'intention "statut" même au démarrage
        if re.search(r"\b(statut|status|où en|ou en)\b", text.lower()):
            return self._send_status(application)

        self.send_message(
            application.student_phone,
            "Bonjour ! 👋 Bienvenue dans le service d'admission universitaire.\n\n"
            "Je vais vous guider pour soumettre votre candidature.\n\n"
            "Pour commencer, merci de m'indiquer votre *nom complet*.",
        )
        application.conversation_state = ConversationState.COLLECT_NAME.value
        self.db.add(application)
        self.db.commit()
        return {"state": application.conversation_state, "action": "asked_name"}

    def _handle_collect_name(self, application: Application, text: str) -> dict:
        if len(text) < 2:
            self.send_message(
                application.student_phone,
                "Merci d'envoyer votre nom complet (au moins 2 caractères).",
            )
            return {"state": application.conversation_state, "action": "name_too_short"}

        application.student_name = text[:255]
        application.conversation_state = ConversationState.COLLECT_PROGRAM.value
        self.db.add(application)
        self.db.commit()

        self.send_message(
            application.student_phone,
            f"Enchanté {application.student_name} ! 🎓\n\n"
            "Quel *programme* souhaitez-vous intégrer ?\n"
            "Ex : Licence Informatique, Master Gestion, etc.",
        )
        return {"state": application.conversation_state, "action": "asked_program"}

    def _handle_collect_program(self, application: Application, text: str) -> dict:
        if len(text) < 3:
            self.send_message(
                application.student_phone,
                "Merci de préciser le programme visé (au moins 3 caractères).",
            )
            return {"state": application.conversation_state, "action": "program_too_short"}

        application.program = text[:255]
        application.conversation_state = ConversationState.COLLECT_DOCS.value
        self.db.add(application)
        self.db.commit()

        self.send_message(
            application.student_phone,
            "Parfait ! 📄\n\n"
            "Veuillez maintenant m'envoyer les documents suivants, "
            "un par un (PDF ou photo) :\n\n"
            "1️⃣ Votre *diplôme* (ou attestation du bac)\n"
            "2️⃣ Votre *relevé de notes*\n"
            "3️⃣ Votre *carte d'identité* (ou passeport)\n"
            "4️⃣ Une *photo d'identité* récente\n\n"
            "Astuce : précisez en légende le type de document "
            "(ex: « diplome », « releve », « carte », « photo »).",
        )
        return {"state": application.conversation_state, "action": "asked_documents"}

    def _handle_collect_docs(self, application: Application, text: str) -> dict:
        # Si l'étudiant demande où il en est
        if re.search(r"\b(statut|status|où en|ou en|liste)\b", text.lower()):
            return self._send_status(application)

        # Sinon, on rappelle ce qui manque
        return self._send_status(application)

    def _handle_waiting(self, application: Application, text: str) -> dict:
        self.send_message(
            application.student_phone,
            "Votre dossier est en cours de vérification. "
            "Vous recevrez une notification dès qu'une décision sera prise. 🙏",
        )
        return {"state": application.conversation_state, "action": "waiting_notice"}

    def _handle_done(self, application: Application, text: str) -> dict:
        if application.status == ApplicationStatus.ACCEPTED:
            msg = "🎉 Félicitations, votre candidature a été *acceptée* !"
        elif application.status == ApplicationStatus.REJECTED:
            msg = "Nous sommes désolés, votre candidature n'a pas été retenue cette année."
        else:
            msg = "Votre candidature est clôturée. Merci !"
        if application.decision_comment:
            msg += f"\n\nCommentaire : {application.decision_comment}"
        self.send_message(application.student_phone, msg)
        return {"state": application.conversation_state, "action": "final_notice"}

    # ------------------------------------------------------------------
    # Upload média
    # ------------------------------------------------------------------
    def _handle_media(
        self,
        application: Application,
        media_url: str,
        media_content_type: str | None,
        caption: str,
    ) -> dict:
        """Une pièce jointe a été envoyée — on confie à la chaîne async.

        On déclenche ici la tâche Celery qui se chargera de :
         - télécharger le fichier depuis Twilio,
         - le stocker dans GCS,
         - lancer OCR + classification IA.
        """
        # Import local pour éviter une dépendance circulaire au démarrage
        from app.workers.ocr_tasks import process_incoming_media

        doc_type = self._guess_document_type(caption)
        process_incoming_media.delay(
            application_id=str(application.id),
            media_url=media_url,
            media_content_type=media_content_type,
            hint_document_type=doc_type.value if doc_type else None,
        )

        self.send_message(
            application.student_phone,
            "📥 Document reçu ! Je l'analyse, vous recevrez un retour dans quelques minutes.",
        )
        return {
            "state": application.conversation_state,
            "action": "media_queued",
            "hint": doc_type.value if doc_type else None,
        }

    @staticmethod
    def _guess_document_type(caption: str) -> DocumentType | None:
        if not caption:
            return None
        lower = caption.lower()
        for keyword, doc_type in DOCUMENT_KEYWORDS.items():
            if keyword in lower:
                return doc_type
        return None

    # ------------------------------------------------------------------
    # Envoi de messages
    # ------------------------------------------------------------------
    def send_message(self, to_number: str, text: str) -> str | None:
        """Envoie un message WhatsApp via Twilio. Retourne le SID Twilio."""
        to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        try:
            msg = self.client.messages.create(body=text, from_=self.from_number, to=to)
            logger.info("Message WhatsApp envoyé à %s (sid=%s)", to, msg.sid)
            return msg.sid
        except TwilioRestException as e:
            logger.exception("Erreur envoi Twilio: %s", e)
            return None

    def send_document_request(self, to_number: str, doc_type: DocumentType) -> None:
        """Demande un document précis à l'étudiant."""
        labels = {
            DocumentType.DIPLOME: "votre *diplôme* (ou attestation du baccalauréat)",
            DocumentType.RELEVE_NOTES: "votre *relevé de notes*",
            DocumentType.CARTE_IDENTITE: "votre *carte d'identité* (ou passeport)",
            DocumentType.PHOTO: "une *photo d'identité* récente",
        }
        label = labels.get(doc_type, "un document complémentaire")
        self.send_message(
            to_number,
            f"Pour compléter votre dossier, merci d'envoyer {label}.\n"
            "📎 Vous pouvez joindre un PDF ou une photo directement à WhatsApp.",
        )

    def notify_decision(self, to_number: str, decision: ApplicationStatus, comment: str | None) -> None:
        """Notifie l'étudiant de la décision finale de l'université."""
        if decision == ApplicationStatus.ACCEPTED:
            text = "🎉 *Félicitations !* Votre candidature a été acceptée."
        elif decision == ApplicationStatus.REJECTED:
            text = (
                "Nous vous remercions pour votre candidature. "
                "Malheureusement, elle n'a pas été retenue cette année."
            )
        else:
            text = f"Mise à jour de votre candidature : {decision.value}"

        if comment:
            text += f"\n\n💬 Commentaire de l'université :\n{comment}"

        self.send_message(to_number, text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_or_create_application(
        self, from_number: str, university: University | None
    ) -> Application:
        """Récupère la candidature active de l'étudiant ou en crée une nouvelle."""
        phone = from_number.replace("whatsapp:", "")

        stmt = (
            select(Application)
            .where(Application.student_phone == phone)
            .where(
                Application.status.in_(
                    [
                        ApplicationStatus.COLLECTING,
                        ApplicationStatus.VALIDATING,
                        ApplicationStatus.VALIDATED,
                        ApplicationStatus.SENT_TO_UNIVERSITY,
                    ]
                )
            )
            .order_by(Application.created_at.desc())
            .limit(1)
        )
        existing = self.db.execute(stmt).scalar_one_or_none()
        if existing:
            return existing

        # Pas d'application active — il en faut une nouvelle.
        # Si on n'a pas d'université connue (ex : numéro Twilio non rattaché),
        # on prend la première université active du système.
        if university is None:
            university = self.db.execute(
                select(University).where(University.is_active.is_(True)).limit(1)
            ).scalar_one_or_none()
            if university is None:
                raise RuntimeError(
                    "Aucune université active n'est configurée pour recevoir des candidatures."
                )

        application = Application(
            university_id=university.id,
            student_phone=phone,
            status=ApplicationStatus.COLLECTING,
            conversation_state=ConversationState.WELCOME.value,
        )
        self.db.add(application)
        self.db.commit()
        self.db.refresh(application)
        return application

    def _send_status(self, application: Application) -> dict:
        """Envoie un récapitulatif des documents reçus / manquants."""
        from app.services.validator import REQUIRED_DOCUMENT_TYPES

        provided = {d.document_type for d in application.documents if d.is_valid}
        missing = REQUIRED_DOCUMENT_TYPES - provided

        if not missing:
            self.send_message(
                application.student_phone,
                "✅ Votre dossier est complet. Validation en cours, je reviens vers vous !",
            )
        else:
            lines = ["📋 *État de votre dossier* :", ""]
            for d in REQUIRED_DOCUMENT_TYPES:
                check = "✅" if d in provided else "⏳"
                lines.append(f"{check} {d.value}")
            lines.append("")
            lines.append("Envoyez les documents manquants pour finaliser votre candidature.")
            self.send_message(application.student_phone, "\n".join(lines))

        return {"state": application.conversation_state, "action": "status_sent"}
