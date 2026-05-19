"""Service de classification IA des documents via l'API Anthropic Claude.

Prend en entrée le texte extrait par OCR et retourne :
  - le type de document (DIPLOME, RELEVE_NOTES, CARTE_IDENTITE, PHOTO, AUTRE)
  - un score de confiance (0.0 - 1.0)
  - la validité (présence des champs attendus)
  - une liste d'erreurs lisibles si le document est incomplet
  - les champs extraits (nom, date, institution)
"""
import json
import logging
import re

from anthropic import Anthropic, APIError

from app.config import settings
from app.models.document import DocumentType
from app.schemas.document import DocumentClassificationResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Tu es un assistant d'admission universitaire. Ton rôle est de \
classifier des documents fournis par des candidats à partir du texte extrait par OCR \
et d'en vérifier la complétude.

Tu réponds UNIQUEMENT en JSON valide, sans aucun texte avant ou après, selon ce schéma :

{
  "type": "DIPLOME" | "RELEVE_NOTES" | "CARTE_IDENTITE" | "PHOTO" | "AUTRE",
  "confidence": <float entre 0.0 et 1.0>,
  "is_valid": <true|false>,
  "errors": [<liste de chaînes en français décrivant les problèmes>],
  "extracted_fields": {
    "student_name": <chaîne ou null>,
    "date": <chaîne ou null>,
    "institution": <chaîne ou null>,
    "additional": <objet libre ou {}>
  }
}

Règles :
- DIPLOME : doit contenir nom de l'étudiant, intitulé du diplôme, date d'obtention, \
établissement émetteur.
- RELEVE_NOTES : doit contenir nom, période/année, liste de notes, établissement.
- CARTE_IDENTITE : doit contenir nom, prénom, date de naissance, numéro.
- PHOTO : pas d'exigence textuelle (peu de texte attendu).
- AUTRE : si aucun des types ci-dessus ne correspond.

is_valid = true seulement si les champs attendus pour ce type sont tous présents.
Sois strict : un document partiel doit être marqué is_valid=false avec une liste \
d'erreurs claires (ex: "Date d'obtention manquante", "Nom de l'étudiant illisible").
"""


class AIClassifier:
    """Wrapper autour du client Anthropic Claude."""

    def __init__(self) -> None:
        self.client = Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.ANTHROPIC_TIMEOUT,
        )
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.ANTHROPIC_MAX_TOKENS

    def classify(
        self,
        ocr_text: str,
        expected_type: DocumentType | None = None,
    ) -> DocumentClassificationResult:
        """Classifie un document à partir de son texte OCR.

        expected_type : indice fourni par l'étudiant (peut être contredit par l'IA).
        """
        # Si le texte est vide, on conclut sans appeler l'API
        if not ocr_text or not ocr_text.strip():
            return DocumentClassificationResult(
                type=expected_type or DocumentType.AUTRE,
                confidence=0.0,
                is_valid=False,
                errors=["Aucun texte n'a pu être extrait du document."],
            )

        user_content = self._build_user_prompt(ocr_text, expected_type)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except APIError as e:
            logger.exception("Erreur API Anthropic: %s", e)
            return DocumentClassificationResult(
                type=expected_type or DocumentType.AUTRE,
                confidence=0.0,
                is_valid=False,
                errors=[f"Service de classification indisponible: {e}"],
            )

        raw = self._extract_text(response)
        return self._parse_response(raw, expected_type)

    def _build_user_prompt(self, ocr_text: str, expected_type: DocumentType | None) -> str:
        hint = (
            f"\n\nIndice fourni par l'étudiant — type attendu : {expected_type.value}."
            if expected_type
            else ""
        )
        # On limite la taille du texte OCR injecté pour rester sous budget tokens
        truncated = ocr_text[:8000]
        return (
            "Voici le texte extrait par OCR d'un document fourni par un candidat. "
            "Classifie-le et vérifie sa complétude selon les règles données. "
            f"Réponds uniquement en JSON valide.{hint}\n\n"
            f"--- TEXTE OCR ---\n{truncated}\n--- FIN TEXTE OCR ---"
        )

    @staticmethod
    def _extract_text(response) -> str:
        """Concatène les blocs de texte de la réponse Anthropic."""
        parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts).strip()

    @staticmethod
    def _parse_response(
        raw: str, expected_type: DocumentType | None
    ) -> DocumentClassificationResult:
        """Parse le JSON renvoyé par Claude (tolérant aux blocs ```json)."""
        # Nettoyage si Claude entoure de ```json ... ```
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("Réponse IA non parseable: %s | raw=%s", e, raw[:500])
            return DocumentClassificationResult(
                type=expected_type or DocumentType.AUTRE,
                confidence=0.0,
                is_valid=False,
                errors=["La réponse du service de classification est invalide."],
            )

        # Normalisation du type
        type_str = str(data.get("type", "AUTRE")).upper()
        try:
            doc_type = DocumentType(type_str)
        except ValueError:
            doc_type = DocumentType.AUTRE

        return DocumentClassificationResult(
            type=doc_type,
            confidence=float(data.get("confidence", 0.0)),
            is_valid=bool(data.get("is_valid", False)),
            errors=list(data.get("errors", []) or []),
            extracted_fields=dict(data.get("extracted_fields", {}) or {}),
        )


_ai_classifier: AIClassifier | None = None


def get_ai_classifier() -> AIClassifier:
    """Retourne le singleton AIClassifier."""
    global _ai_classifier
    if _ai_classifier is None:
        _ai_classifier = AIClassifier()
    return _ai_classifier
