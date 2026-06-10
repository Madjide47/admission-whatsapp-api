"""Service WhatsApp Bot — conversation guidée via Twilio.

Gère la machine à états de la conversation :
    WELCOME → COLLECT_NAME → COLLECT_PROGRAM → COLLECT_DOCS
            → WAITING_VALIDATION → DONE
"""
import logging
import re
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from app.config import settings
from app.models.application import Application, ApplicationStatus
from app.models.document import DocumentType
from app.models.form_field_value import ApplicationFieldValue
from app.models.program import FormField, Program
from app.models.university import University

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """États de la machine de conversation WhatsApp."""

    WELCOME = "WELCOME"
    COLLECT_INTEREST = "COLLECT_INTEREST"     # v2 : domaine d'intérêt pour filtrer les universités
    CHOOSE_UNIVERSITY = "CHOOSE_UNIVERSITY"   # v2 : sélection université depuis une liste filtrée
    COLLECT_NAME = "COLLECT_NAME"
    CHOOSE_PROGRAM = "CHOOSE_PROGRAM"         # v2 : sélection programme depuis une liste
    CONFIRM_PREREQUISITES = "CONFIRM_PREREQUISITES"  # v2 : affichage prérequis + oui/non
    COLLECT_PROGRAM = "COLLECT_PROGRAM"       # fallback texte libre (pas de programmes configurés)
    COLLECT_FIELDS = "COLLECT_FIELDS"         # v2 : saisie des champs dynamiques du formulaire
    AWAITING_ENROLLMENT_CHOICE = "AWAITING_ENROLLMENT_CHOICE"  # v2 : inscriptions fermées, choix étudiant
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

# Étiquettes humaines pour chaque type de document
DOCUMENT_LABELS: dict[DocumentType, str] = {
    DocumentType.DIPLOME: "votre *diplôme* (ou attestation du baccalauréat)",
    DocumentType.RELEVE_NOTES: "votre *relevé de notes*",
    DocumentType.CARTE_IDENTITE: "votre *carte d'identité* (ou passeport)",
    DocumentType.PHOTO: "une *photo d'identité* récente",
}

# Ordre dans lequel les documents sont demandés (séquentiel)
REQUIRED_DOCUMENT_TYPES_ORDERED: list[DocumentType] = [
    DocumentType.DIPLOME,
    DocumentType.RELEVE_NOTES,
    DocumentType.CARTE_IDENTITE,
    DocumentType.PHOTO,
]


def get_next_required_document(
    application: Application,
    required_types: list[DocumentType] | None = None,
) -> DocumentType | None:
    """Retourne le prochain document requis non encore validé.

    required_types : liste ordonnée à utiliser. Si None, fallback sur
    REQUIRED_DOCUMENT_TYPES_ORDERED (hardcodé). Les workers passent None ;
    le bot passe la liste chargée depuis required_documents via _get_required_doc_types.
    """
    types = required_types if required_types is not None else REQUIRED_DOCUMENT_TYPES_ORDERED
    provided_valid = {d.document_type for d in application.documents if d.is_valid}
    for doc_type in types:
        if doc_type not in provided_valid:
            return doc_type
    return None


def send_whatsapp(to_number: str, text: str) -> str | None:
    """Envoie un message WhatsApp via Twilio sans session DB.

    Utilisée par les workers Celery qui n'ont pas accès à l'instance WhatsAppBot.
    """
    to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"

    if settings.DEMO_MODE:
        logger.info("[DEMO] WhatsApp → %s :\n%s\n%s", to, "-" * 40, text)
        return "DEMO_SID"

    try:
        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        msg = client.messages.create(body=text, from_=settings.TWILIO_WHATSAPP_NUMBER, to=to)
        logger.info("WhatsApp envoyé à %s (sid=%s)", to, msg.sid)
        return msg.sid
    except TwilioRestException as e:
        logger.exception("Erreur envoi Twilio: %s", e)
        return None


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

        state = ConversationState(application.conversation_state or ConversationState.WELCOME.value)

        # Candidature en attente d'ouverture des inscriptions → rien à faire pour l'étudiant
        if application.status == ApplicationStatus.PENDING_ENROLLMENT:
            self.send_message(
                application.student_phone,
                "⏳ Votre dossier est complet et validé.\n\n"
                "Nous attendons l'ouverture des inscriptions pour l'envoyer à l'université. "
                "Vous serez notifié(e) dès qu'il sera transmis. 🙏",
            )
            return {"state": state.value, "action": "pending_enrollment_notice"}

        # Commandes globales — interceptées avant le handler d'état
        lower = normalized.lower()
        _SKIP_GLOBAL = {ConversationState.WELCOME, ConversationState.DONE}
        if state not in _SKIP_GLOBAL:
            if re.search(r"\b(statut|status|où en|ou en)\b", lower):
                return self._send_status(application)
            if re.search(r"\b(aide|help|sos|perdu|quoi faire|que faire)\b", lower):
                return self._send_contextual_help(application, state)

        handler = {
            ConversationState.WELCOME: self._handle_welcome,
            ConversationState.COLLECT_INTEREST: self._handle_collect_interest,
            ConversationState.CHOOSE_UNIVERSITY: self._handle_choose_university,
            ConversationState.COLLECT_NAME: self._handle_collect_name,
            ConversationState.CHOOSE_PROGRAM: self._handle_choose_program,
            ConversationState.CONFIRM_PREREQUISITES: self._handle_confirm_prerequisites,
            ConversationState.COLLECT_PROGRAM: self._handle_collect_program,
            ConversationState.AWAITING_ENROLLMENT_CHOICE: self._handle_awaiting_enrollment_choice,
            ConversationState.COLLECT_FIELDS: self._handle_collect_fields,
            ConversationState.COLLECT_DOCS: self._handle_collect_docs,
            ConversationState.WAITING_VALIDATION: self._handle_waiting,
            ConversationState.DONE: self._handle_done,
        }[state]
        return handler(application, normalized)

    # ------------------------------------------------------------------
    # Validateurs de champs
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(text: str) -> str | None:
        """Retourne un message d'erreur ou None si le nom est valide."""
        stripped = text.strip()
        if len(stripped) < 2:
            return "Votre nom doit contenir au moins 2 caractères."
        if len(stripped) > 100:
            return "Votre nom est trop long (100 caractères maximum)."
        if not any(c.isalpha() for c in stripped):
            return "Votre nom doit contenir des lettres."
        if any(c.isdigit() for c in stripped):
            return (
                "Votre nom ne doit pas contenir de chiffres.\n"
                "Envoyez votre *nom et prénom complets* (ex : Kofi Mensah)."
            )
        return None

    @staticmethod
    def _validate_program_text(text: str) -> str | None:
        """Retourne un message d'erreur ou None si le programme est valide."""
        stripped = text.strip()
        if len(stripped) < 3:
            return "Merci de préciser le programme visé (au moins 3 caractères)."
        if len(stripped) > 150:
            return "Le nom du programme est trop long (150 caractères maximum)."
        if not any(c.isalpha() for c in stripped):
            return "Le nom du programme doit contenir des lettres."
        return None

    @staticmethod
    def _choice_error(text: str, count: int) -> str:
        """Message d'erreur adapté : numéro hors plage vs texte non numérique."""
        stripped = text.strip()
        if stripped.isdigit():
            return (
                f"Le numéro *{int(stripped)}* ne correspond à aucune option. "
                f"Choisissez entre *1* et *{count}*."
            )
        return (
            f"Je n'ai pas compris *« {stripped[:30]} »*. "
            f"Répondez avec un *numéro* entre *1* et *{count}*."
        )

    def _send_contextual_help(self, application: Application, state: ConversationState) -> dict:
        """Envoie un message d'aide adapté à l'état actuel de la conversation."""
        help_map = {
            ConversationState.COLLECT_INTEREST: (
                "Répondez avec le *numéro* du domaine qui vous intéresse."
            ),
            ConversationState.CHOOSE_UNIVERSITY: (
                "Répondez avec le *numéro* de l'université de votre choix."
            ),
            ConversationState.COLLECT_NAME: (
                "Envoyez votre *nom et prénom complets*, en lettres uniquement.\n"
                "Exemple : *Kofi Mensah*"
            ),
            ConversationState.CHOOSE_PROGRAM: (
                "Répondez avec le *numéro* du programme que vous souhaitez intégrer."
            ),
            ConversationState.CONFIRM_PREREQUISITES: (
                "Répondez *oui* pour continuer votre candidature, ou *non* pour "
                "choisir un autre programme."
            ),
            ConversationState.COLLECT_PROGRAM: (
                "Tapez le nom du programme visé.\n"
                "Exemple : *Licence Informatique*, *Master Droit des Affaires*"
            ),
            ConversationState.COLLECT_FIELDS: (
                "Répondez par un texte à la question posée.\n"
                "Chaque réponse fait avancer votre dossier."
            ),
            ConversationState.COLLECT_DOCS: (
                "Envoyez vos documents *en photo ou PDF* directement ici.\n"
                "Tapez *statut* pour voir les documents déjà reçus et ceux qui manquent."
            ),
            ConversationState.WAITING_VALIDATION: (
                "Votre dossier est en cours de vérification. "
                "Vous serez notifié dès qu'une décision sera prise. 🙏"
            ),
        }
        msg = help_map.get(state, "Envoyez *Bonjour* pour démarrer une candidature.")
        self.send_message(application.student_phone, f"ℹ️ *Aide* :\n\n{msg}")
        return {"state": application.conversation_state, "action": "help_sent"}

    # ------------------------------------------------------------------
    # Handlers par état
    # ------------------------------------------------------------------
    # Mots-clés qui déclenchent la conversation
    _TRIGGER_KEYWORDS = re.compile(
        r"\b(bonjour|bonsoir|salut|hello|hi|hey|coucou|"
        r"candidature|inscription|admission|postuler|dossier|candidater|"
        r"commencer|démarrer|demarrer|start|aide|help|"
        r"je veux|je souhaite|je voudrais)\b",
        re.IGNORECASE,
    )

    def _handle_welcome(self, application: Application, text: str) -> dict:
        lower = text.lower()

        if re.search(r"\b(statut|status|où en|ou en)\b", lower):
            return self._send_status(application)

        if not self._TRIGGER_KEYWORDS.search(lower):
            self.send_message(
                application.student_phone,
                "👋 Bonjour ! Je suis l'assistant d'admission universitaire.\n\n"
                "Pour démarrer votre candidature, envoyez l'un de ces mots :\n\n"
                "• *Bonjour*\n"
                "• *Salut*\n"
                "• *Candidature*\n"
                "• *Inscription*\n"
                "• *Commencer*",
            )
            return {"state": application.conversation_state, "action": "prompt_trigger"}

        domains = self._list_domains()

        if domains:
            # Domaines configurés → liste numérotée
            lines = [
                "Bonjour ! 👋 Je suis l'assistant d'admission universitaire.\n",
                "Dans quel *domaine* souhaitez-vous poursuivre vos études ?\n",
            ]
            for i, d in enumerate(domains, start=1):
                lines.append(f"{i}. {d}")
            lines.append("\nRépondez avec le *numéro* de votre choix.")
            self.send_message(application.student_phone, "\n".join(lines))
            application.conversation_state = ConversationState.COLLECT_INTEREST.value
            self.db.add(application)
            self.db.commit()
            return {"state": application.conversation_state, "action": "listed_domains"}

        # Aucun domaine configuré → aller directement aux universités
        universities = self._list_universities()
        if not universities:
            self.send_message(
                application.student_phone,
                "Désolé, aucune université n'est disponible pour le moment. 🙏",
            )
            return {"state": application.conversation_state, "action": "no_university"}

        if len(universities) == 1:
            application.university_id = universities[0].id
            application.conversation_state = ConversationState.COLLECT_NAME.value
            self.db.add(application)
            self.db.commit()
            self.send_message(
                application.student_phone,
                f"Bonjour ! 👋 Bienvenue à *{universities[0].name}*. "
                "Je vais vous aider à soumettre votre dossier. 🚀\n\n"
                "Pour commencer, quel est votre *nom complet* ?",
            )
            return {"state": application.conversation_state, "action": "asked_name"}

        lines = ["Bonjour ! 👋 Dans quelle université souhaitez-vous postuler ?\n"]
        for i, u in enumerate(universities, start=1):
            lines.append(f"{i}. {u.name}")
        lines.append("\nRépondez avec le *numéro* de votre choix.")
        self.send_message(application.student_phone, "\n".join(lines))
        application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
        self.db.add(application)
        self.db.commit()
        return {"state": application.conversation_state, "action": "listed_universities"}

    def _handle_collect_interest(self, application: Application, text: str) -> dict:
        domains = self._list_domains()
        idx = self._parse_numeric_choice(text, len(domains))

        if idx is None:
            lines = [self._choice_error(text, len(domains)), ""]
            for i, d in enumerate(domains, start=1):
                lines.append(f"{i}. {d}")
            self.send_message(application.student_phone, "\n".join(lines))
            return {"state": application.conversation_state, "action": "invalid_domain_choice"}

        chosen_domain = domains[idx]
        universities = self._find_universities_for_domain(chosen_domain)

        if not universities:
            universities = self._list_universities()

        if len(universities) == 1:
            # Sélection automatique de la seule université du domaine
            application.university_id = universities[0].id
            application.ai_notes = None
            application.conversation_state = ConversationState.COLLECT_NAME.value
            self.db.add(application)
            self.db.commit()
            self.send_message(
                application.student_phone,
                f"✅ Domaine *{chosen_domain}* sélectionné !\n"
                f"Université : *{universities[0].name}*\n\n"
                "Quel est votre *nom complet* ?",
            )
            return {"state": application.conversation_state, "action": "asked_name"}

        lines = [
            f"Voici les universités qui proposent des formations en *{chosen_domain}* :\n"
        ]
        for i, u in enumerate(universities, start=1):
            lines.append(f"{i}. {u.name}")
        lines.append("\nRépondez avec le *numéro* de votre choix.")
        self.send_message(application.student_phone, "\n".join(lines))

        application.ai_notes = chosen_domain
        application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
        self.db.add(application)
        self.db.commit()
        return {
            "state": application.conversation_state,
            "action": "listed_universities",
            "domain": chosen_domain,
            "count": len(universities),
        }

    def _handle_choose_university(self, application: Application, text: str) -> dict:
        # Reconstruire la même liste que celle montrée dans _handle_collect_interest
        interest = application.ai_notes
        if interest:
            universities = self._find_universities_by_interest(interest) or self._list_universities()
        else:
            universities = self._list_universities()

        idx = self._parse_numeric_choice(text, len(universities))

        if idx is None:
            lines = [self._choice_error(text, len(universities)), ""]
            for i, u in enumerate(universities, start=1):
                lines.append(f"{i}. {u.name}")
            self.send_message(application.student_phone, "\n".join(lines))
            return {"state": application.conversation_state, "action": "invalid_university_choice"}

        chosen = universities[idx]
        application.university_id = chosen.id
        application.ai_notes = None  # libère le stockage temporaire de l'intérêt
        application.conversation_state = ConversationState.COLLECT_NAME.value
        self.db.add(application)
        self.db.commit()
        self.send_message(
            application.student_phone,
            f"✅ *{chosen.name}* sélectionnée !\n\nQuel est votre *nom complet* ?",
        )
        return {"state": application.conversation_state, "action": "asked_name"}

    def _handle_collect_name(self, application: Application, text: str) -> dict:
        error = self._validate_name(text)
        if error:
            self.send_message(application.student_phone, f"❌ {error}")
            return {"state": application.conversation_state, "action": "name_invalid"}

        application.student_name = text.strip()[:100]
        programs = self._list_programs(application.university_id)

        if not programs:
            # Pas de programmes configurés → texte libre (backward compat)
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

        if len(programs) == 1:
            # Auto-sélection du seul programme disponible
            application.program = programs[0].name
            application.conversation_state = ConversationState.COLLECT_DOCS.value
            self.db.add(application)
            self.db.commit()
            first_label = DOCUMENT_LABELS[REQUIRED_DOCUMENT_TYPES_ORDERED[0]]
            self.send_message(
                application.student_phone,
                f"Enchanté {application.student_name} ! 🎓\n\n"
                f"Programme sélectionné : *{programs[0].name}*\n\n"
                f"Commençons par {first_label}.\n\n"
                "📎 Envoyez-le en photo ou PDF.",
            )
            return {"state": application.conversation_state, "action": "asked_documents"}

        # Plusieurs programmes — liste numérotée
        application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
        self.db.add(application)
        self.db.commit()
        lines = [f"Enchanté {application.student_name} ! 🎓\n\nQuel programme souhaitez-vous intégrer ?\n"]
        for i, p in enumerate(programs, start=1):
            lines.append(f"{i}. {p.name}")
        lines.append("\nRépondez avec le *numéro* de votre choix.")
        self.send_message(application.student_phone, "\n".join(lines))
        return {"state": application.conversation_state, "action": "listed_programs"}

    def _handle_choose_program(self, application: Application, text: str) -> dict:
        programs = self._list_programs(application.university_id)
        idx = self._parse_numeric_choice(text, len(programs))

        if idx is None:
            lines = [self._choice_error(text, len(programs)), ""]
            for i, p in enumerate(programs, start=1):
                lines.append(f"{i}. {p.name}")
            self.send_message(application.student_phone, "\n".join(lines))
            return {"state": application.conversation_state, "action": "invalid_program_choice"}

        chosen = programs[idx]
        application.program = chosen.name
        application.program_id = chosen.id

        # Vérifier si les inscriptions sont ouvertes pour ce programme
        if not chosen.is_enrollment_open():
            return self._propose_enrollment_choice(application, chosen)

        return self._maybe_show_prerequisites(
            application, f"✅ Programme *{chosen.name}* sélectionné !"
        )

    def _propose_enrollment_choice(self, application: Application, program: "Program") -> dict:
        """Inscriptions fermées pour ce programme — présente 2 options à l'étudiant."""
        application.conversation_state = ConversationState.AWAITING_ENROLLMENT_CHOICE.value
        self.db.add(application)
        self.db.commit()

        date_info = ""
        if program.enrollment_start:
            date_info += f"\n\n📅 Ouverture : *{program.enrollment_start.strftime('%d/%m/%Y')}*"
        if program.enrollment_end:
            date_info += f"\n📅 Fermeture : *{program.enrollment_end.strftime('%d/%m/%Y')}*"

        self.send_message(
            application.student_phone,
            f"⚠️ Les inscriptions pour *{program.name}* ne sont pas encore ouvertes.{date_info}\n\n"
            "Que souhaitez-vous faire ?\n\n"
            "1️⃣ Je reviendrai pendant la période d'inscription\n"
            "2️⃣ Je dépose ma candidature maintenant (elle sera envoyée à l'université dès l'ouverture)",
        )
        return {"state": application.conversation_state, "action": "enrollment_closed"}

    def _handle_awaiting_enrollment_choice(self, application: Application, text: str) -> dict:
        idx = self._parse_numeric_choice(text, 2)

        if idx is None:
            self.send_message(
                application.student_phone,
                self._choice_error(text, 2) + "\n\n"
                "1️⃣ Je reviendrai pendant la période d'inscription\n"
                "2️⃣ Je dépose ma candidature maintenant",
            )
            return {"state": application.conversation_state, "action": "invalid_enrollment_choice"}

        if idx == 0:
            # Option 1 : revenir plus tard → on supprime la candidature
            phone = application.student_phone
            program = self._find_program(application.university_id, application.program)
            self.db.delete(application)
            self.db.commit()

            date_reminder = ""
            if program and program.enrollment_start:
                date_reminder = (
                    f"\n\n📅 Les inscriptions ouvrent le "
                    f"*{program.enrollment_start.strftime('%d/%m/%Y')}*."
                )
            self.send_message(
                phone,
                f"D'accord ! Revenez pendant la période d'inscription pour postuler. 😊{date_reminder}\n\n"
                "Envoyez *Bonjour* quand vous êtes prêt(e).",
            )
            return {"state": ConversationState.WELCOME.value, "action": "enrollment_deferred"}

        # Option 2 : dépôt immédiat — champs puis documents, envoi différé
        return self._maybe_show_prerequisites(
            application,
            "✅ Votre candidature sera transmise à l'université dès l'ouverture des inscriptions.",
        )

    def _handle_collect_program(self, application: Application, text: str) -> dict:
        error = self._validate_program_text(text)
        if error:
            self.send_message(application.student_phone, f"❌ {error}")
            return {"state": application.conversation_state, "action": "program_invalid"}

        application.program = text.strip()[:150]
        return self._begin_collection(application, "Parfait ! 📄")

    def _maybe_show_prerequisites(self, application: Application, intro: str) -> dict:
        """Avant la collecte : affiche les prérequis du programme (si configurés et
        whatsapp_display) et demande oui/non. Sinon, démarre directement la collecte."""
        criteria = self._get_program_criteria(application)
        if criteria and criteria.whatsapp_display and criteria.has_any_prerequisite():
            application.conversation_state = ConversationState.CONFIRM_PREREQUISITES.value
            self.db.add(application)
            self.db.commit()
            self.send_message(
                application.student_phone,
                f"{intro}\n\n{self._format_prerequisites(application.program, criteria)}",
            )
            return {"state": application.conversation_state, "action": "asked_prerequisites"}
        return self._begin_collection(application, intro)

    def _handle_confirm_prerequisites(self, application: Application, text: str) -> dict:
        """Réponse oui/non à l'affichage des prérequis du programme."""
        answer = text.strip().lower()
        if answer in {"oui", "o", "yes", "y", "ok", "1", "d'accord", "daccord"}:
            return self._begin_collection(
                application, "✅ Parfait, poursuivons votre candidature !"
            )
        if answer in {"non", "n", "no", "2"}:
            programs = self._list_programs(application.university_id)
            application.program = None
            application.program_id = None
            application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
            self.db.add(application)
            self.db.commit()
            lines = [
                "Pas de souci 🙂 Vous pouvez choisir un autre programme :",
                "",
            ]
            for i, p in enumerate(programs, start=1):
                lines.append(f"{i}. {p.name}")
            self.send_message(application.student_phone, "\n".join(lines))
            return {"state": application.conversation_state, "action": "prerequisites_declined"}

        self.send_message(
            application.student_phone,
            "Répondez *oui* pour continuer votre candidature, ou *non* pour choisir "
            "un autre programme.",
        )
        return {"state": application.conversation_state, "action": "invalid_prereq_choice"}

    def _get_program_criteria(self, application: Application):
        """Critères d'admission du programme de la candidature (ou None)."""
        from app.models.program_criteria import ProgramCriteria

        try:
            program_id = application.program_id
            if program_id is None:
                program = self._find_program(application.university_id, application.program)
                program_id = program.id if program else None
            if program_id is None:
                return None
            return self.db.execute(
                select(ProgramCriteria).where(ProgramCriteria.program_id == program_id)
            ).scalar_one_or_none()
        except Exception:
            logger.warning("Lecture des critères impossible pour app %s", application.id, exc_info=True)
            return None

    @staticmethod
    def _format_prerequisites(program_name: str | None, criteria) -> str:
        lines = [
            f"📋 *Prérequis — {program_name or 'ce programme'}*",
            "",
            "Avant de soumettre votre candidature, vérifiez que vous remplissez les "
            "conditions suivantes :",
            "",
        ]
        if criteria.required_degree:
            lines.append(f"• {criteria.required_degree}")
        if criteria.min_average is not None:
            lines.append(f"• Moyenne générale ≥ {('%g' % float(criteria.min_average))}/20")
        if criteria.accepted_specialties:
            lines.append(f"• Spécialité : {', '.join(criteria.accepted_specialties)}")
        for prereq in (criteria.prerequisites or []):
            lines.append(f"• {prereq}")
        if criteria.additional_notes:
            lines += ["", f"ℹ️ {criteria.additional_notes}"]
        lines += ["", "Souhaitez-vous continuer votre candidature ? Répondez *oui* ou *non*."]
        return "\n".join(lines)

    def _begin_collection(self, application: Application, intro: str) -> dict:
        """Après sélection du programme : champs dynamiques d'abord s'il y en a,
        sinon directement les documents. ``intro`` = message de confirmation."""
        fields = self._get_required_fields(application)
        first_field = self._next_unanswered_field(application, fields)
        if first_field is not None:
            application.conversation_state = ConversationState.COLLECT_FIELDS.value
            self.db.add(application)
            self.db.commit()
            self.send_message(
                application.student_phone,
                f"{intro}\n\nQuelques informations à compléter d'abord. 📝\n\n"
                f"{self._field_question(first_field)}",
            )
            return {"state": application.conversation_state, "action": "asked_field"}
        return self._begin_documents(application, intro)

    def _begin_documents(self, application: Application, intro: str | None = None) -> dict:
        """Passe à la collecte des documents et demande le premier."""
        application.conversation_state = ConversationState.COLLECT_DOCS.value
        self.db.add(application)
        self.db.commit()
        required = self._get_required_doc_types(application)
        first_doc = required[0] if required else REQUIRED_DOCUMENT_TYPES_ORDERED[0]
        first_label = DOCUMENT_LABELS.get(first_doc, "un premier document")
        prefix = f"{intro}\n\n" if intro else ""
        self.send_message(
            application.student_phone,
            f"{prefix}Envoyez vos documents un par un. Commençons par {first_label}.\n\n"
            "📎 En photo ou PDF directement dans cette conversation.\n"
            "Tapez *statut* à tout moment pour voir où vous en êtes.",
        )
        return {"state": application.conversation_state, "action": "asked_documents"}

    def _handle_collect_fields(self, application: Application, text: str) -> dict:
        """Collecte les réponses aux champs dynamiques du formulaire, un par un."""
        fields = self._get_required_fields(application)
        current = self._next_unanswered_field(application, fields)
        if current is None:
            # Plus aucun champ en attente → on passe aux documents
            return self._begin_documents(application, "✅ Informations enregistrées !")

        value = (text or "").strip()
        if not value:
            self.send_message(
                application.student_phone,
                f"❌ Merci de répondre à la question.\n\n{self._field_question(current)}",
            )
            return {"state": application.conversation_state, "action": "field_empty"}

        if current.validation_regex:
            try:
                if re.fullmatch(current.validation_regex, value) is None:
                    self.send_message(
                        application.student_phone,
                        f"❌ La réponse ne semble pas valide pour *{current.label}*.\n\n"
                        f"{self._field_question(current)}",
                    )
                    return {"state": application.conversation_state, "action": "field_invalid"}
            except re.error:
                logger.warning("validation_regex invalide pour le champ %s", current.id)

        self.db.add(
            ApplicationFieldValue(
                application_id=application.id,
                field_id=current.id,
                value=value[:2000],
            )
        )
        self.db.commit()

        nxt = self._next_unanswered_field(application, fields)
        if nxt is not None:
            self.send_message(
                application.student_phone,
                f"✅ Enregistré.\n\n{self._field_question(nxt)}",
            )
            return {"state": application.conversation_state, "action": "asked_field"}

        return self._begin_documents(
            application, "✅ Merci, toutes les informations sont enregistrées !"
        )

    def _get_required_fields(self, application: Application) -> list[FormField]:
        """Champs requis du formulaire publié du programme (vide si aucun)."""
        from app.services.validator import ApplicationValidator

        return ApplicationValidator(self.db).get_required_fields(application)

    def _answered_field_ids(self, application: Application) -> set:
        return set(
            self.db.execute(
                select(ApplicationFieldValue.field_id).where(
                    ApplicationFieldValue.application_id == application.id
                )
            ).scalars().all()
        )

    def _next_unanswered_field(
        self, application: Application, fields: list[FormField]
    ) -> FormField | None:
        answered = self._answered_field_ids(application)
        for f in fields:
            if f.id not in answered:
                return f
        return None

    @staticmethod
    def _field_question(field: FormField) -> str:
        return f"📝 {field.label}"

    def _handle_collect_docs(self, application: Application, text: str) -> dict:
        # On rappelle le prochain document spécifique attendu
        # (statut est géré globalement dans handle_incoming_message)
        required = self._get_required_doc_types(application)
        next_doc = get_next_required_document(application, required_types=required)
        if next_doc:
            label = DOCUMENT_LABELS[next_doc]
            self.send_message(
                application.student_phone,
                f"📎 J'attends {label}.\n\n"
                "Envoyez-le en photo ou PDF directement dans cette conversation.\n"
                "Tapez *statut* pour voir où vous en êtes.",
            )
        else:
            self.send_message(
                application.student_phone,
                "✅ Tous vos documents ont été reçus ! Validation finale en cours... 🙏",
            )
        return {"state": application.conversation_state, "action": "reminded_docs"}

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

        if settings.DEMO_MODE:
            logger.info(
                "[DEMO] WhatsApp → %s :\n%s\n%s",
                to,
                "-" * 40,
                text,
            )
            return "DEMO_SID"

        try:
            msg = self.client.messages.create(body=text, from_=self.from_number, to=to)
            logger.info("Message WhatsApp envoyé à %s (sid=%s)", to, msg.sid)
            return msg.sid
        except Exception as e:
            # On capture TOUTE erreur d'envoi (TwilioRestException quota 429,
            # mais aussi erreurs réseau/DNS, timeouts…) : send_message ne doit
            # jamais crasher, et retourner None permet le retry côté tâche Celery.
            logger.exception("Erreur envoi WhatsApp à %s: %s", to, e)
            return None

    def send_document_request(self, to_number: str, doc_type: DocumentType) -> None:
        """Demande un document précis à l'étudiant."""
        label = DOCUMENT_LABELS.get(doc_type, "un document complémentaire")
        self.send_message(
            to_number,
            f"Pour compléter votre dossier, merci d'envoyer {label}.\n"
            "📎 Vous pouvez joindre un PDF ou une photo directement à WhatsApp.",
        )

    def notify_decision(
        self, to_number: str, decision: ApplicationStatus, comment: str | None
    ) -> str | None:
        """Notifie l'étudiant de la décision finale de l'université.

        Retourne le SID Twilio en cas de succès, ``None`` si l'envoi a échoué
        (permet à la tâche Celery appelante de décider d'un retry).
        """
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

        return self.send_message(to_number, text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_required_doc_types(self, application: Application) -> list[DocumentType]:
        """Charge la liste ordonnée des documents requis pour cette candidature.

        Délègue au validator dynamique (RequiredDocument en base) avec fallback hardcodé.
        """
        from app.services.validator import ApplicationValidator
        return ApplicationValidator(self.db).get_required_doc_types(application)

    def _list_domains(self) -> list[str]:
        """Retourne les domaines académiques distincts configurés sur les programmes actifs."""
        rows = list(
            self.db.execute(
                select(Program.domain)
                .where(Program.is_active.is_(True), Program.domain.isnot(None))
                .distinct()
                .order_by(Program.domain)
            ).scalars().all()
        )
        return [r for r in rows if r]

    def _find_universities_for_domain(self, domain: str) -> list[University]:
        """Retourne les universités actives proposant au moins un programme dans ce domaine."""
        matching_ids = list(
            self.db.execute(
                select(Program.university_id)
                .where(Program.is_active.is_(True), Program.domain == domain)
                .distinct()
            ).scalars().all()
        )
        if not matching_ids:
            return []
        return list(
            self.db.execute(
                select(University)
                .where(
                    University.id.in_(matching_ids),
                    University.is_active.is_(True),
                )
                .order_by(University.name)
            ).scalars().all()
        )

    def _find_universities_by_interest(self, interest: str) -> list[University]:
        """Alias conservé pour compatibilité — délègue à _find_universities_for_domain."""
        return self._find_universities_for_domain(interest)

    def _list_universities(self) -> list[University]:
        """Retourne les universités actives triées par nom."""
        return list(
            self.db.execute(
                select(University).where(University.is_active.is_(True)).order_by(University.name)
            ).scalars().all()
        )

    def _list_programs(self, university_id) -> list[Program]:
        """Retourne les programmes actifs d'une université, triés par nom."""
        if university_id is None:
            return []
        return list(
            self.db.execute(
                select(Program)
                .where(Program.university_id == university_id, Program.is_active.is_(True))
                .order_by(Program.name)
            ).scalars().all()
        )

    def _find_program(self, university_id, program_name: str | None) -> Program | None:
        """Retrouve un programme actif par (université, nom) — cohérent avec le validator."""
        if university_id is None or not program_name:
            return None
        return self.db.execute(
            select(Program).where(
                Program.university_id == university_id,
                Program.name == program_name,
                Program.is_active.is_(True),
            )
        ).scalar_one_or_none()

    @staticmethod
    def _parse_numeric_choice(text: str, count: int) -> int | None:
        """Valide une réponse numérique et retourne l'index 0-based, ou None."""
        try:
            n = int(text.strip())
            if 1 <= n <= count:
                return n - 1
        except ValueError:
            pass
        return None

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
                        ApplicationStatus.CHOOSING_UNIVERSITY,
                        ApplicationStatus.CHOOSING_PROGRAM,
                        ApplicationStatus.COLLECTING_FIELDS,
                        ApplicationStatus.COLLECTING_DOCUMENTS,
                        ApplicationStatus.VALIDATING,
                        ApplicationStatus.VALIDATED,
                        ApplicationStatus.PENDING_ENROLLMENT,
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
            status=ApplicationStatus.CHOOSING_UNIVERSITY,
            conversation_state=ConversationState.WELCOME.value,
        )
        self.db.add(application)
        self.db.commit()
        self.db.refresh(application)
        return application

    def _send_status(self, application: Application) -> dict:
        """Envoie un récapitulatif des documents reçus / manquants."""
        required = self._get_required_doc_types(application)
        provided = {d.document_type for d in application.documents if d.is_valid}
        missing = set(required) - provided

        if not missing:
            self.send_message(
                application.student_phone,
                "✅ Votre dossier est complet. Validation en cours, je reviens vers vous !",
            )
        else:
            lines = ["📋 *État de votre dossier* :", ""]
            for doc_type in required:
                check = "✅" if doc_type in provided else "⏳"
                lines.append(f"{check} {doc_type.value}")
            lines.append("")
            lines.append("Envoyez les documents manquants pour finaliser votre candidature.")
            self.send_message(application.student_phone, "\n".join(lines))

        return {"state": application.conversation_state, "action": "status_sent"}
