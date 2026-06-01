"""Tests du service de classification IA.

L'API Anthropic est toujours mockée — aucun appel réseau réel.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.document import DocumentType
from app.schemas.document import DocumentClassificationResult
from app.services.ai_classifier import AIClassifier


@pytest.fixture()
def classifier():
    with patch("app.services.ai_classifier.Anthropic"):
        c = AIClassifier()
        c.client = MagicMock()
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
    classifier.client.messages.create.return_value = _make_response(
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
    classifier.client.messages.create.return_value = _make_response(
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
    classifier.client.messages.create.return_value = _make_response(
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
    classifier.client.messages.create.assert_not_called()
    assert result.is_valid is False
    assert result.confidence == 0.0
    assert "Aucun texte" in result.errors[0]


def test_classify_whitespace_only_no_api_call(classifier):
    result = classifier.classify("   \n\t  ")
    classifier.client.messages.create.assert_not_called()
    assert result.is_valid is False


# ---------------------------------------------------------------------------
# Gestion des erreurs
# ---------------------------------------------------------------------------


def test_classify_api_down_returns_invalid(classifier):
    from anthropic import APIError

    classifier.client.messages.create.side_effect = APIError(
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
    classifier.client.messages.create.return_value = resp

    result = classifier.classify("Texte quelconque")
    assert result.is_valid is False
    assert any("invalide" in e.lower() for e in result.errors)


def test_classify_unknown_type_defaults_to_autre(classifier):
    classifier.client.messages.create.return_value = _make_response(
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
    classifier.client.messages.create.return_value = _make_response(
        {
            "type": "CARTE_IDENTITE",
            "confidence": 0.9,
            "is_valid": True,
            "errors": [],
            "extracted_fields": {},
        }
    )
    result = classifier.classify("Carte Nationale d'Identité…", expected_type=DocumentType.CARTE_IDENTITE)
    call_args = classifier.client.messages.create.call_args
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
    classifier.client.messages.create.return_value = resp

    result = classifier.classify("quelques pixels")
    assert result.type == DocumentType.PHOTO
    assert result.is_valid is True
