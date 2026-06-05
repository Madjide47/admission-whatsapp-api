"""Tests du service de classification IA.

L'API Anthropic est toujours mockée — aucun appel réseau réel.
AI_PROVIDER est forcé à "anthropic" pour les tests (backend contrôlé).
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AI_PROVIDER", "anthropic")

from app.models.document import DocumentType
from app.schemas.document import DocumentClassificationResult
from app.services.ai_classifier import AIClassifier


@pytest.fixture()
def classifier(monkeypatch):
    """AIClassifier avec backend Anthropic mocké."""
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_PROVIDER", "anthropic")
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_MOCK", False)
    monkeypatch.setattr("app.services.ai_classifier.settings.DEMO_MODE", False)
    with patch("app.services.ai_classifier.Anthropic"):
        c = AIClassifier()
        c._backend.client = MagicMock()
        return c


def _make_response(payload: dict) -> MagicMock:
    """Construit une réponse Anthropic fictive contenant du JSON."""
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps(payload)
    resp = MagicMock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# Cas normaux
# ---------------------------------------------------------------------------


def test_classify_diplome(classifier):
    classifier._backend.client.messages.create.return_value = _make_response(
        {
            "type": "DIPLOME",
            "confidence": 0.95,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {"student_name": "Kofi Mensah", "date": "2024-06", "institution": "UL"},
        }
    )
    result = classifier.classify("Diplôme du baccalauréat… Kofi Mensah… 2024")
    assert result.type == DocumentType.DIPLOME
    assert result.is_valid is True
    assert result.confidence == pytest.approx(0.95)
    assert result.errors == []


def test_classify_releve_notes(classifier):
    classifier._backend.client.messages.create.return_value = _make_response(
        {
            "type": "RELEVE_NOTES",
            "confidence": 0.88,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {},
        }
    )
    result = classifier.classify("Relevé de notes 2023-2024…")
    assert result.type == DocumentType.RELEVE_NOTES


def test_classify_invalid_document_with_errors(classifier):
    classifier._backend.client.messages.create.return_value = _make_response(
        {
            "type": "DIPLOME",
            "confidence": 0.4,
            "is_valid": False,
            "errors": ["Date d'obtention manquante", "Nom illisible"],
            "extracted_fields": {},
        }
    )
    result = classifier.classify("Texte partiellement illisible…")
    assert result.is_valid is False
    assert len(result.errors) == 2
    assert "Date d'obtention manquante" in result.errors


# ---------------------------------------------------------------------------
# Court-circuit texte vide
# ---------------------------------------------------------------------------


def test_classify_empty_text_no_api_call(classifier):
    result = classifier.classify("")
    classifier._backend.client.messages.create.assert_not_called()
    assert result.is_valid is False
    assert result.confidence == 0.0
    assert "Aucun texte" in result.errors[0]


def test_classify_whitespace_only_no_api_call(classifier):
    result = classifier.classify("   \n\t  ")
    classifier._backend.client.messages.create.assert_not_called()
    assert result.is_valid is False


# ---------------------------------------------------------------------------
# Gestion des erreurs
# ---------------------------------------------------------------------------


def test_classify_api_down_returns_invalid(classifier):
    from anthropic import APIError

    classifier._backend.client.messages.create.side_effect = APIError(
        message="Service unavailable", request=MagicMock(), body={}
    )
    result = classifier.classify("Texte valide de test")
    assert result.is_valid is False
    assert result.confidence == 0.0
    assert any("classification indisponible" in e for e in result.errors)


def test_classify_bad_json_returns_invalid(classifier):
    block = MagicMock()
    block.type = "text"
    block.text = "not json at all {{"
    resp = MagicMock()
    resp.content = [block]
    classifier._backend.client.messages.create.return_value = resp

    result = classifier.classify("Texte quelconque")
    assert result.is_valid is False
    assert any("invalide" in e.lower() for e in result.errors)


def test_classify_unknown_type_defaults_to_autre(classifier):
    classifier._backend.client.messages.create.return_value = _make_response(
        {
            "type": "INCONNU",
            "confidence": 0.1,
            "is_valid": False,
            "errors": [],
            "extracted_fields": {},
        }
    )
    result = classifier.classify("Type inconnu")
    assert result.type == DocumentType.AUTRE


def test_classify_with_expected_type_hint(classifier):
    """Le hint expected_type est bien envoyé dans le prompt utilisateur."""
    classifier._backend.client.messages.create.return_value = _make_response(
        {
            "type": "CARTE_IDENTITE",
            "confidence": 0.9,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {},
        }
    )
    result = classifier.classify("Carte Nationale d'Identité…", expected_type=DocumentType.CARTE_IDENTITE)
    call_args = classifier._backend.client.messages.create.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert "CARTE_IDENTITE" in user_content
    assert result.type == DocumentType.CARTE_IDENTITE


# ---------------------------------------------------------------------------
# Parsing JSON avec blocs ```json
# ---------------------------------------------------------------------------


def test_parse_response_strips_json_fence(classifier):
    block = MagicMock()
    block.type = "text"
    block.text = '```json\n{"type":"PHOTO","confidence":0.7,"is_valid":true,"errors":[],"extracted_fields":{}}\n```'
    resp = MagicMock()
    resp.content = [block]
    classifier._backend.client.messages.create.return_value = resp

    result = classifier.classify("quelques pixels")
    assert result.type == DocumentType.PHOTO
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# Mode mock (AI_MOCK=True / DEMO_MODE=True)
# ---------------------------------------------------------------------------


def test_ai_mock_mode_returns_without_api_call(monkeypatch):
    """AI_MOCK=True → _mock_classification appelé, pas d'appel API réel."""
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_MOCK", True)
    monkeypatch.setattr("app.services.ai_classifier.settings.DEMO_MODE", False)
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_PROVIDER", "anthropic")

    with patch("app.services.ai_classifier.Anthropic"):
        c = AIClassifier()
        c._backend.client = MagicMock()

    result = c.classify("Texte quelconque", expected_type=DocumentType.DIPLOME)
    assert result.is_valid is True
    assert result.type == DocumentType.DIPLOME
    assert result.confidence == pytest.approx(0.94)
    c._backend.client.messages.create.assert_not_called()


def test_demo_mode_returns_mock_without_api_call(monkeypatch):
    """DEMO_MODE=True → même comportement que AI_MOCK."""
    monkeypatch.setattr("app.services.ai_classifier.settings.DEMO_MODE", True)
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_MOCK", False)
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_PROVIDER", "anthropic")

    with patch("app.services.ai_classifier.Anthropic"):
        c = AIClassifier()
        c._backend.client = MagicMock()

    result = c.classify("Texte quelconque")
    assert result.is_valid is True
    c._backend.client.messages.create.assert_not_called()


def test_mock_classification_all_types(monkeypatch):
    """_mock_classification retourne un résultat valide pour chaque type."""
    from app.services.ai_classifier import _mock_classification

    for doc_type in [
        DocumentType.DIPLOME,
        DocumentType.RELEVE_NOTES,
        DocumentType.CARTE_IDENTITE,
        DocumentType.PHOTO,
        DocumentType.AUTRE,
    ]:
        result = _mock_classification(doc_type)
        assert result.type == doc_type
        assert isinstance(result.confidence, float)


def test_mock_classification_none_defaults_to_diplome():
    """Sans expected_type, _mock_classification retourne DIPLOME par défaut."""
    from app.services.ai_classifier import _mock_classification

    result = _mock_classification(None)
    assert result.type == DocumentType.DIPLOME


# ---------------------------------------------------------------------------
# Backend Gemini Flash
# ---------------------------------------------------------------------------


def test_gemini_classify_happy_path(monkeypatch):
    """GeminiClassifier retourne un résultat parsé depuis le JSON de l'API."""
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_PROVIDER", "gemini")
    monkeypatch.setattr("app.services.ai_classifier.settings.AI_MOCK", False)
    monkeypatch.setattr("app.services.ai_classifier.settings.DEMO_MODE", False)
    monkeypatch.setattr("app.services.ai_classifier.settings.GOOGLE_AI_API_KEY", "fake-key")

    mock_response = MagicMock()
    mock_response.text = '{"type":"RELEVE_NOTES","confidence":0.88,"is_valid":true,"errors":[],"extracted_fields":{}}'

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    with patch("app.services.ai_classifier.GeminiClassifier.__init__", lambda self: None):
        c = AIClassifier.__new__(AIClassifier)
        from app.services.ai_classifier import GeminiClassifier
        backend = GeminiClassifier.__new__(GeminiClassifier)
        backend._genai = mock_genai
        backend._model_name = "gemini-2.0-flash"
        c._backend = backend

    result = c._backend.classify("Relevé de notes 2023-2024…")
    assert result.type == DocumentType.RELEVE_NOTES
    assert result.is_valid is True
    assert result.confidence == pytest.approx(0.88)


def test_gemini_classify_empty_text_no_api_call(monkeypatch):
    """GeminiClassifier court-circuite sur texte vide sans appeler l'API."""
    mock_genai = MagicMock()

    from app.services.ai_classifier import GeminiClassifier
    backend = GeminiClassifier.__new__(GeminiClassifier)
    backend._genai = mock_genai
    backend._model_name = "gemini-2.0-flash"

    result = backend.classify("")
    mock_genai.GenerativeModel.assert_not_called()
    assert result.is_valid is False


def test_gemini_classify_api_error_returns_invalid(monkeypatch):
    """Exception API Gemini → résultat invalide, pas de crash."""
    mock_model = MagicMock()
    mock_model.generate_content.side_effect = RuntimeError("API Gemini down")

    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    from app.services.ai_classifier import GeminiClassifier
    backend = GeminiClassifier.__new__(GeminiClassifier)
    backend._genai = mock_genai
    backend._model_name = "gemini-2.0-flash"

    result = backend.classify("Texte quelconque")
    assert result.is_valid is False
    assert any("indisponible" in e for e in result.errors)


# ---------------------------------------------------------------------------
# AnthropicClassifier — exception générale (non APIError)
# ---------------------------------------------------------------------------


def test_anthropic_unexpected_exception_returns_invalid(classifier):
    """Exception non-APIError (ex: réseau) → résultat invalide, pas de crash."""
    classifier._backend.client.messages.create.side_effect = ConnectionError("Réseau coupé")

    result = classifier.classify("Texte valide")
    assert result.is_valid is False
    assert any("indisponible" in e for e in result.errors)


# ---------------------------------------------------------------------------
# _build_user_prompt — branche avec expected_type
# ---------------------------------------------------------------------------


def test_build_user_prompt_includes_hint():
    """Le prompt inclut le type attendu quand expected_type est fourni."""
    from app.services.ai_classifier import _build_user_prompt

    prompt = _build_user_prompt("Texte OCR", expected_type=DocumentType.DIPLOME)
    assert "DIPLOME" in prompt
    assert "Texte OCR" in prompt


def test_build_user_prompt_no_hint():
    """Sans expected_type, le prompt ne mentionne pas de type attendu."""
    from app.services.ai_classifier import _build_user_prompt

    prompt = _build_user_prompt("Texte OCR", expected_type=None)
    assert "type attendu" not in prompt.lower()
    assert "Texte OCR" in prompt
