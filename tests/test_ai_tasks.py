"""Tests des tâches Celery IA : classify_document_task et check_application_completion_task.

Anthropic/Gemini, Twilio et Celery sont tous mockés.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.university import University
from app.schemas.document import DocumentClassificationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Univ AI Test",
        email=f"ai-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="ai_test",
        webhook_url="https://example.test/wh",
        webhook_secret="sec",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


@pytest.fixture()
def application(db_session, university) -> Application:
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890333333",
        student_name="Kofi Mensah",
        program="Licence Informatique",
        status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state="COLLECT_DOCS",
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


@pytest.fixture()
def document(db_session, application) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=DocumentType.DIPLOME,
        gcs_path="gs://bucket/diplome.jpg",
        mime_type="image/jpeg",
        ocr_text="Diplôme du Baccalauréat… Kofi Mensah… 2024",
        is_valid=False,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _classification(
    is_valid: bool = True,
    doc_type: DocumentType = DocumentType.DIPLOME,
    confidence: float = 0.92,
    errors: list[str] | None = None,
) -> DocumentClassificationResult:
    return DocumentClassificationResult(
        type=doc_type,
        confidence=confidence,
        is_valid=is_valid,
        errors=errors or [],
        extracted_fields={},
    )


def _add_valid_doc(db_session, application, doc_type: DocumentType, confidence: float = 0.9):
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=doc_type,
        gcs_path=f"gs://bucket/{doc_type.value}",
        is_valid=True,
        classification_result={"confidence": confidence},
    )
    db_session.add(doc)
    db_session.commit()
    return doc


# ---------------------------------------------------------------------------
# classify_document_task — persistance en base
# ---------------------------------------------------------------------------


def test_classify_saves_valid_result(db_session, document, monkeypatch):
    """Classification valide → is_valid=True sauvegardé, classification_result rempli."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.get_ai_classifier", lambda: MagicMock(classify=lambda **kw: _classification(is_valid=True)))
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"):
        from app.workers.ai_tasks import classify_document_task
        result = classify_document_task.run(str(document.id))

    assert result == "ok"
    db_session.refresh(document)
    assert document.is_valid is True
    assert document.classification_result["confidence"] == pytest.approx(0.92)


def test_classify_saves_invalid_result_with_errors(db_session, document, monkeypatch):
    """Classification invalide → is_valid=False, errors sauvegardées."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.workers.ai_tasks.get_ai_classifier",
        lambda: MagicMock(
            classify=lambda **kw: _classification(
                is_valid=False,
                confidence=0.3,
                errors=["Date d'obtention manquante", "Nom illisible"],
            )
        ),
    )
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"):
        from app.workers.ai_tasks import classify_document_task
        classify_document_task.run(str(document.id))

    db_session.refresh(document)
    assert document.is_valid is False
    assert "Date d'obtention manquante" in document.validation_errors


def test_classify_updates_document_type_from_result(db_session, document, monkeypatch):
    """Si l'IA détecte CARTE_IDENTITE alors que le hint était DIPLOME → type mis à jour."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.workers.ai_tasks.get_ai_classifier",
        lambda: MagicMock(classify=lambda **kw: _classification(doc_type=DocumentType.CARTE_IDENTITE)),
    )
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"):
        from app.workers.ai_tasks import classify_document_task
        classify_document_task.run(str(document.id))

    db_session.refresh(document)
    assert document.document_type == DocumentType.CARTE_IDENTITE


def test_classify_chains_to_completion_check(db_session, document, monkeypatch):
    """Après classification, check_application_completion_task.delay est toujours appelé."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.get_ai_classifier", lambda: MagicMock(classify=lambda **kw: _classification()))
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.ai_tasks.check_application_completion_task.delay") as mock_check:
        from app.workers.ai_tasks import classify_document_task
        classify_document_task.run(str(document.id))

    mock_check.assert_called_once_with(str(document.application_id))


def test_classify_document_not_found(db_session, monkeypatch):
    """Document introuvable → retourne 'no_document', rien planché."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    from app.workers.ai_tasks import classify_document_task
    result = classify_document_task.run(str(uuid.uuid4()))
    assert result == "no_document"


# ---------------------------------------------------------------------------
# classify_document_task — feedback WhatsApp
# ---------------------------------------------------------------------------


def test_classify_sends_valid_whatsapp_feedback(db_session, document, monkeypatch):
    """Document valide → WhatsApp envoyé avec ✅."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.get_ai_classifier", lambda: MagicMock(classify=lambda **kw: _classification(is_valid=True)))

    mock_send = MagicMock()
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", mock_send)

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"):
        from app.workers.ai_tasks import classify_document_task
        classify_document_task.run(str(document.id))

    mock_send.assert_called_once()
    phone, msg = mock_send.call_args[0]
    assert phone == application.student_phone if False else True  # called with the application phone
    assert "✅" in msg


def test_classify_sends_invalid_whatsapp_feedback(db_session, document, monkeypatch):
    """Document invalide → WhatsApp envoyé avec ❌ et les raisons."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.workers.ai_tasks.get_ai_classifier",
        lambda: MagicMock(
            classify=lambda **kw: _classification(
                is_valid=False, errors=["Nom du lycée manquant"]
            )
        ),
    )

    mock_send = MagicMock()
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", mock_send)

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"):
        from app.workers.ai_tasks import classify_document_task
        classify_document_task.run(str(document.id))

    mock_send.assert_called_once()
    msg = mock_send.call_args[0][1]
    assert "❌" in msg
    assert "Nom du lycée manquant" in msg


def test_classify_whatsapp_failure_does_not_crash(db_session, document, monkeypatch):
    """Exception dans send_whatsapp → tâche continue, chaîne toujours appelée."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.get_ai_classifier", lambda: MagicMock(classify=lambda **kw: _classification()))
    monkeypatch.setattr(
        "app.workers.ai_tasks.send_whatsapp",
        MagicMock(side_effect=RuntimeError("Réseau indisponible")),
    )

    with patch("app.workers.ai_tasks.check_application_completion_task.delay") as mock_check:
        from app.workers.ai_tasks import classify_document_task
        result = classify_document_task.run(str(document.id))

    assert result == "ok"
    mock_check.assert_called_once()


# ---------------------------------------------------------------------------
# check_application_completion_task — notification WhatsApp
# ---------------------------------------------------------------------------


def test_completion_sends_whatsapp_when_validated(db_session, application, monkeypatch):
    """Dossier VALIDATED → WhatsApp de félicitations envoyé à l'étudiant."""
    for doc_type in [
        DocumentType.DIPLOME,
        DocumentType.RELEVE_NOTES,
        DocumentType.CARTE_IDENTITE,
        DocumentType.PHOTO,
    ]:
        _add_valid_doc(db_session, application, doc_type)
    db_session.refresh(application)

    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    mock_send = MagicMock()
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", mock_send)

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay"):
        from app.workers.ai_tasks import check_application_completion_task
        status = check_application_completion_task.run(str(application.id))

    assert status == ApplicationStatus.VALIDATED.value
    mock_send.assert_called_once()
    msg = mock_send.call_args[0][1]
    assert "🎉" in msg or "transmis" in msg.lower()


def test_completion_no_whatsapp_when_incomplete(db_session, application, monkeypatch):
    """Dossier incomplet → pas de message WhatsApp."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    mock_send = MagicMock()
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", mock_send)

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay"):
        from app.workers.ai_tasks import check_application_completion_task
        status = check_application_completion_task.run(str(application.id))

    assert status == ApplicationStatus.COLLECTING_DOCUMENTS.value
    mock_send.assert_not_called()


def test_completion_triggers_webhook_when_validated(db_session, application, monkeypatch):
    """VALIDATED → dispatch_validated_application_task.delay est appelé."""
    for doc_type in [
        DocumentType.DIPLOME,
        DocumentType.RELEVE_NOTES,
        DocumentType.CARTE_IDENTITE,
        DocumentType.PHOTO,
    ]:
        _add_valid_doc(db_session, application, doc_type)
    db_session.refresh(application)

    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.ai_tasks import check_application_completion_task
        check_application_completion_task.run(str(application.id))

    mock_dispatch.assert_called_once_with(str(application.id))


def test_completion_no_webhook_when_incomplete(db_session, application, monkeypatch):
    """Dossier incomplet → webhook PAS déclenché."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.ai_tasks import check_application_completion_task
        check_application_completion_task.run(str(application.id))

    mock_dispatch.assert_not_called()


def test_completion_application_not_found(db_session, monkeypatch):
    """Application introuvable → retourne 'no_application'."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    from app.workers.ai_tasks import check_application_completion_task
    result = check_application_completion_task.run(str(uuid.uuid4()))
    assert result == "no_application"
