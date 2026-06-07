"""Service de validation globale d'une candidature.

Détermine si un dossier est complet et calcule un score de validation global.

Stratégie de chargement des documents requis :
  1. Cherche un enregistrement Program correspondant au programme de l'application.
  2. Si trouvé, charge les RequiredDocument (is_required=True) triés par order.
  3. Si introuvable ou vide → fallback sur REQUIRED_DOCUMENT_TYPES (set hardcodé).
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import AdmissionForm, FormField, Program, RequiredDocument

logger = logging.getLogger(__name__)


# Documents obligatoires par défaut — utilisés quand aucun RequiredDocument n'est configuré
REQUIRED_DOCUMENT_TYPES: set[DocumentType] = {
    DocumentType.DIPLOME,
    DocumentType.RELEVE_NOTES,
    DocumentType.CARTE_IDENTITE,
    DocumentType.PHOTO,
}

# Ordre de référence pour le fallback (cohérent avec whatsapp_bot)
_REQUIRED_ORDERED: list[DocumentType] = [
    DocumentType.DIPLOME,
    DocumentType.RELEVE_NOTES,
    DocumentType.CARTE_IDENTITE,
    DocumentType.PHOTO,
]


class ApplicationValidator:
    """Évalue la complétude et la qualité d'une candidature."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def validate(self, application: Application) -> tuple[bool, float, list[str]]:
        """Retourne (is_complete, score, raisons).

        - is_complete : tous les documents requis sont présents et valides
        - score       : moyenne des confidences IA (0.0 – 1.0)
        - raisons     : liste lisible des problèmes détectés
        """
        reasons: list[str] = []

        if not application.student_name:
            reasons.append("Nom de l'étudiant manquant.")
        if not application.program:
            reasons.append("Programme universitaire non précisé.")

        required_types = self._load_required_doc_types(application)

        docs_by_type: dict[DocumentType, list[Document]] = {}
        for doc in application.documents:
            docs_by_type.setdefault(doc.document_type, []).append(doc)

        for required in required_types:
            docs = docs_by_type.get(required, [])
            if not docs:
                reasons.append(f"Document manquant : {required.value}.")
                continue
            valid_docs = [d for d in docs if d.is_valid]
            if not valid_docs:
                reasons.append(
                    f"Aucun document valide pour {required.value} "
                    f"({len(docs)} fourni(s) mais invalide(s))."
                )

        confidences: list[float] = []
        for doc in application.documents:
            if doc.is_valid and doc.classification_result and "confidence" in doc.classification_result:
                confidences.append(float(doc.classification_result["confidence"]))
        score = sum(confidences) / len(confidences) if confidences else 0.0

        return len(reasons) == 0, round(score, 3), reasons

    def apply_validation(self, application: Application) -> Application:
        """Effectue la validation et met à jour le statut de la candidature."""
        is_complete, score, reasons = self.validate(application)

        application.validation_score = score
        application.ai_notes = (
            "Dossier validé automatiquement."
            if is_complete
            else "Problèmes détectés :\n- " + "\n- ".join(reasons)
        )

        if is_complete:
            application.status = ApplicationStatus.VALIDATED
        else:
            application.status = ApplicationStatus.COLLECTING_DOCUMENTS

        self.db.add(application)
        self.db.commit()
        self.db.refresh(application)

        logger.info(
            "Validation candidature %s — complet=%s score=%s problèmes=%d",
            application.id,
            is_complete,
            score,
            len(reasons),
        )
        return application

    def get_required_doc_types(self, application: Application) -> list[DocumentType]:
        """Retourne la liste ordonnée des types requis pour cette candidature.

        Utilisable par le bot et les workers pour connaître l'ordre de demande.
        """
        return self._load_required_doc_types(application)

    def get_required_fields(self, application: Application) -> list[FormField]:
        """Champs (FormField) requis du formulaire publié du programme, ordonnés.

        Vide s'il n'y a pas de formulaire publié ou aucun champ requis configuré
        (le bot saute alors l'étape « questions »).
        """
        form = self._get_published_form(application)
        if form is None:
            return []
        try:
            return list(
                self.db.execute(
                    select(FormField)
                    .where(
                        FormField.form_id == form.id,
                        FormField.is_required.is_(True),
                    )
                    .order_by(FormField.order, FormField.label)
                ).scalars().all()
            )
        except Exception:
            logger.warning(
                "Erreur chargement form_fields pour app %s", application.id, exc_info=True
            )
            return []

    def _get_published_form(self, application: Application) -> AdmissionForm | None:
        """Formulaire publié du programme de l'application (ou None)."""
        if not application.program or not application.university_id:
            return None
        try:
            program = self.db.execute(
                select(Program).where(
                    Program.university_id == application.university_id,
                    Program.name == application.program,
                    Program.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if program is None:
                return None
            return self.db.execute(
                select(AdmissionForm)
                .where(
                    AdmissionForm.program_id == program.id,
                    AdmissionForm.is_published.is_(True),
                )
                .order_by(AdmissionForm.published_at.desc())
            ).scalar_one_or_none()
        except Exception:
            logger.warning(
                "Erreur recherche formulaire publié pour app %s", application.id, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Chargement dynamique
    # ------------------------------------------------------------------

    def _load_required_doc_types(self, application: Application) -> list[DocumentType]:
        """Charge les types requis depuis required_documents, ou retourne le fallback.

        Fallback sur REQUIRED_DOCUMENT_TYPES si :
          - application.program est vide
          - aucun Program correspondant en base
          - aucun RequiredDocument configuré pour ce programme
          - erreur DB inattendue
        """
        if not application.program or not application.university_id:
            return _REQUIRED_ORDERED[:]

        try:
            program = self.db.execute(
                select(Program).where(
                    Program.university_id == application.university_id,
                    Program.name == application.program,
                    Program.is_active.is_(True),
                )
            ).scalar_one_or_none()

            if program is None:
                return _REQUIRED_ORDERED[:]

            form = self.db.execute(
                select(AdmissionForm)
                .where(
                    AdmissionForm.program_id == program.id,
                    AdmissionForm.is_published.is_(True),
                )
                .order_by(AdmissionForm.published_at.desc())
            ).scalar_one_or_none()

            if form is None:
                return _REQUIRED_ORDERED[:]

            required = list(
                self.db.execute(
                    select(RequiredDocument)
                    .where(
                        RequiredDocument.form_id == form.id,
                        RequiredDocument.is_required.is_(True),
                    )
                    .order_by(RequiredDocument.order, RequiredDocument.document_type)
                ).scalars().all()
            )

            if not required:
                return _REQUIRED_ORDERED[:]

            result: list[DocumentType] = []
            for rd in required:
                try:
                    result.append(DocumentType(rd.document_type))
                except ValueError:
                    logger.warning("Type document inconnu en base : %s", rd.document_type)
            return result or _REQUIRED_ORDERED[:]

        except Exception:
            logger.warning(
                "Erreur chargement required_documents pour app %s — fallback hardcodé",
                application.id,
                exc_info=True,
            )
            return _REQUIRED_ORDERED[:]
