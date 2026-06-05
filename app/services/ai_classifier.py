"""Service de classification IA des documents.

Supporte deux fournisseurs configurables via AI_PROVIDER :
  - "gemini"    : Google Gemini Flash (quota gratuit, JSON natif)
  - "anthropic" : Anthropic Claude (fallback)

Retourne toujours un DocumentClassificationResult avec la même structure.
"""
import json
import logging
import re

from anthropic import Anthropic, APIError as AnthropicAPIError

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


def _invalid_result(
    expected_type: DocumentType | None,
    errors: list[str],
) -> DocumentClassificationResult:
    return DocumentClassificationResult(
        type=expected_type or DocumentType.AUTRE,
        confidence=0.0,
        is_valid=False,
        errors=errors,
    )


def _mock_classification(expected_type: DocumentType | None) -> DocumentClassificationResult:
    """Résultat simulé réaliste par type — utilisé quand AI_MOCK=true."""
    doc_type = expected_type or DocumentType.DIPLOME

    mock_data = {
        DocumentType.DIPLOME: {
            "confidence": 0.94,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {
                "student_name": "Étudiant Demo",
                "date": "2024-06-15",
                "institution": "Lycée Général Demo",
                "additional": {"mention": "Bien", "serie": "D"},
            },
        },
        DocumentType.RELEVE_NOTES: {
            "confidence": 0.91,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {
                "student_name": "Étudiant Demo",
                "date": "2023-2024",
                "institution": "Université Demo",
                "additional": {"moyenne": "14.5/20"},
            },
        },
        DocumentType.CARTE_IDENTITE: {
            "confidence": 0.96,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {
                "student_name": "Étudiant Demo",
                "date": "1999-03-10",
                "institution": None,
                "additional": {"numero": "TG123456789"},
            },
        },
        DocumentType.PHOTO: {
            "confidence": 0.88,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {
                "student_name": None,
                "date": None,
                "institution": None,
            },
        },
        DocumentType.AUTRE: {
            "confidence": 0.45,
            "is_valid": False,
            "errors": ["Type de document non reconnu"],
            "extracted_fields": {},
        },
    }

    data = mock_data.get(doc_type, mock_data[DocumentType.AUTRE])
    return DocumentClassificationResult(
        type=doc_type,
        confidence=data["confidence"],
        is_valid=data["is_valid"],
        errors=data["errors"],
        extracted_fields=data["extracted_fields"],
    )


def _parse_json_response(
    raw: str,
    expected_type: DocumentType | None,
) -> DocumentClassificationResult:
    """Parse le JSON renvoyé par le modèle (tolérant aux blocs ```json)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Réponse IA non parseable: %s | raw=%s", e, raw[:500])
        return _invalid_result(expected_type, ["La réponse du service de classification est invalide."])

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


def _build_user_prompt(ocr_text: str, expected_type: DocumentType | None) -> str:
    hint = (
        f"\n\nIndice fourni par l'étudiant — type attendu : {expected_type.value}."
        if expected_type
        else ""
    )
    truncated = ocr_text[:8000]
    return (
        "Voici le texte extrait par OCR d'un document fourni par un candidat. "
        "Classifie-le et vérifie sa complétude selon les règles données. "
        f"Réponds uniquement en JSON valide.{hint}\n\n"
        f"--- TEXTE OCR ---\n{truncated}\n--- FIN TEXTE OCR ---"
    )


class GeminiClassifier:
    """Classificateur basé sur Google Gemini Flash."""

    def __init__(self) -> None:
        import google.generativeai as genai

        genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
        self._genai = genai
        self._model_name = settings.GOOGLE_AI_MODEL

    def classify(
        self,
        ocr_text: str,
        expected_type: DocumentType | None = None,
    ) -> DocumentClassificationResult:
        if not ocr_text or not ocr_text.strip():
            return _invalid_result(expected_type, ["Aucun texte n'a pu être extrait du document."])

        user_content = _build_user_prompt(ocr_text, expected_type)

        try:
            model = self._genai.GenerativeModel(
                model_name=self._model_name,
                system_instruction=SYSTEM_PROMPT,
                generation_config=self._genai.GenerationConfig(
                    response_mime_type="application/json",
                    max_output_tokens=1000,
                ),
            )
            response = model.generate_content(user_content)
            return _parse_json_response(response.text, expected_type)
        except Exception as e:
            logger.exception("Erreur API Gemini: %s", e)
            return _invalid_result(expected_type, [f"Service de classification indisponible: {e}"])


class AnthropicClassifier:
    """Classificateur basé sur Anthropic Claude."""

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
        if not ocr_text or not ocr_text.strip():
            return _invalid_result(expected_type, ["Aucun texte n'a pu être extrait du document."])

        user_content = _build_user_prompt(ocr_text, expected_type)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except AnthropicAPIError as e:
            logger.exception("Erreur API Anthropic: %s", e)
            return _invalid_result(expected_type, [f"Service de classification indisponible: {e}"])
        except Exception as e:
            logger.exception("Erreur inattendue Anthropic: %s", e)
            return _invalid_result(expected_type, [f"Service de classification indisponible: {e}"])

        parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        raw = "".join(parts).strip()
        return _parse_json_response(raw, expected_type)


class AIClassifier:
    """Facade unique — dispatche vers Gemini ou Anthropic selon AI_PROVIDER."""

    def __init__(self) -> None:
        if settings.AI_PROVIDER == "gemini":
            self._backend = GeminiClassifier()
        else:
            self._backend = AnthropicClassifier()

    def classify(
        self,
        ocr_text: str,
        expected_type: DocumentType | None = None,
    ) -> DocumentClassificationResult:
        if settings.DEMO_MODE or settings.AI_MOCK:
            logger.info("[MOCK] Classification simulée pour type=%s", expected_type)
            return _mock_classification(expected_type)

        return self._backend.classify(ocr_text, expected_type)


_ai_classifier: AIClassifier | None = None


def get_ai_classifier() -> AIClassifier:
    """Retourne le singleton AIClassifier."""
    global _ai_classifier
    if _ai_classifier is None:
        _ai_classifier = AIClassifier()
    return _ai_classifier
