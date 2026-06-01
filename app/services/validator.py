"""Service de validation globale d'une candidature.

Détermine si un dossier est complet et calcule un score de validation global.
"""
import logging

from sqlalchemy.orm import Session

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType

logger = logging.getLogger(__name__)


# Documents obligatoires pour qu'un dossier soit considéré complet
REQUIRED_DOCUMENT_TYPES: set[DocumentType] = {
    DocumentType.DIPLOME,
    DocumentType.RELEVE_NOTES,
    DocumentType.CARTE_IDENTITE,
    DocumentType.PHOTO,
}


class ApplicationValidator:
    """Évalue la complétude et la qualité d'une candidature."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def validate(self, application: Application) -> tuple[bool, float, list[str]]:
        """Retourne (is_complete, score, raisons).

        - is_complete : tous les documents requis sont présents et valides
        - score : moyenne pondérée des confidences IA
        - raisons : liste lisible des problèmes restants
        """
        reasons: list[str] = []

        # Champs étudiant obligatoires
        if not application.student_name:
            reasons.append("Nom de l'étudiant manquant.")
        if not application.program:
            reasons.append("Programme universitaire non précisé.")

        # Index par type
        docs_by_type: dict[DocumentType, list[Document]] = {}
        for doc in application.documents:
            docs_by_type.setdefault(doc.document_type, []).append(doc)

        # Vérification des types requis
        for required in REQUIRED_DOCUMENT_TYPES:
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

        # Score = moyenne des confidences extraites
        confidences: list[float] = []
        for doc in application.documents:
            if doc.classification_result and "confidence" in doc.classification_result:
                confidences.append(float(doc.classification_result["confidence"]))
        score = sum(confidences) / len(confidences) if confidences else 0.0

        is_complete = len(reasons) == 0
        return is_complete, round(score, 3), reasons

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
            # On reste en COLLECTING tant que tout n'est pas bon
            application.status = ApplicationStatus.COLLECTING

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
