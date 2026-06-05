"""Tests des tâches Celery OCR : process_incoming_media et run_ocr_task.

Twilio, GCS, Tesseract et Celery sont tous mockés.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.university import University


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Univ OCR Test",
        email=f"ocr-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="ocr_test",
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
        student_phone="+22890222222",
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
        gcs_path="applications/test/diplome.jpg",
        mime_type="image/jpeg",
        is_valid=False,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _mock_httpx_client(content: bytes = b"fake bytes", content_type: str = "image/jpeg"):
    """Construit un mock httpx.Client simulant un téléchargement Twilio."""
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.headers = {"Content-Type": content_type}
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# process_incoming_media
# ---------------------------------------------------------------------------


def test_process_incoming_media_creates_document_and_chains(db_session, application, monkeypatch):
    """Télécharge le fichier, crée un Document en base, enchaîne run_ocr_task."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ocr_tasks.httpx.Client", lambda **kw: _mock_httpx_client())

    mock_storage = MagicMock()
    mock_storage.upload_document.return_value = "applications/test/file.jpg"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    with patch("app.workers.ocr_tasks.run_ocr_task.delay") as mock_run_ocr:
        from app.workers.ocr_tasks import process_incoming_media
        result = process_incoming_media.run(
            application_id=str(application.id),
            media_url="https://api.twilio.com/media/abc",
            media_content_type="image/jpeg",
            hint_document_type=None,
        )

    assert result != "no_application"
    mock_storage.upload_document.assert_called_once()
    mock_run_ocr.assert_called_once()

    doc = db_session.query(Document).filter_by(application_id=application.id).first()
    assert doc is not None
    assert doc.gcs_path == "applications/test/file.jpg"


def test_process_incoming_media_hint_sets_document_type(db_session, application, monkeypatch):
    """hint_document_type='DIPLOME' → Document.document_type = DIPLOME."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ocr_tasks.httpx.Client", lambda **kw: _mock_httpx_client())

    mock_storage = MagicMock()
    mock_storage.upload_document.return_value = "gs://test/path.jpg"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    with patch("app.workers.ocr_tasks.run_ocr_task.delay"):
        from app.workers.ocr_tasks import process_incoming_media
        process_incoming_media.run(
            application_id=str(application.id),
            media_url="https://api.twilio.com/media/abc",
            hint_document_type="DIPLOME",
        )

    doc = db_session.query(Document).filter_by(application_id=application.id).first()
    assert doc.document_type == DocumentType.DIPLOME


def test_process_incoming_media_no_hint_defaults_to_autre(db_session, application, monkeypatch):
    """Sans hint → Document.document_type = AUTRE."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ocr_tasks.httpx.Client", lambda **kw: _mock_httpx_client())

    mock_storage = MagicMock()
    mock_storage.upload_document.return_value = "gs://test/path.jpg"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    with patch("app.workers.ocr_tasks.run_ocr_task.delay"):
        from app.workers.ocr_tasks import process_incoming_media
        process_incoming_media.run(
            application_id=str(application.id),
            media_url="https://api.twilio.com/media/abc",
            hint_document_type=None,
        )

    doc = db_session.query(Document).filter_by(application_id=application.id).first()
    assert doc.document_type == DocumentType.AUTRE


def test_process_incoming_media_application_not_found(db_session, monkeypatch):
    """Application introuvable → retourne 'no_application', rien créé."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    from app.workers.ocr_tasks import process_incoming_media
    result = process_incoming_media.run(
        application_id=str(uuid.uuid4()),
        media_url="https://api.twilio.com/media/abc",
    )
    assert result == "no_application"


def test_process_incoming_media_stores_correct_mime(db_session, application, monkeypatch):
    """Le mime_type retourné par Twilio est sauvegardé sur le Document."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.workers.ocr_tasks.httpx.Client",
        lambda **kw: _mock_httpx_client(content_type="application/pdf"),
    )

    mock_storage = MagicMock()
    mock_storage.upload_document.return_value = "gs://test/doc.pdf"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    with patch("app.workers.ocr_tasks.run_ocr_task.delay"):
        from app.workers.ocr_tasks import process_incoming_media
        process_incoming_media.run(
            application_id=str(application.id),
            media_url="https://api.twilio.com/media/abc",
        )

    doc = db_session.query(Document).filter_by(application_id=application.id).first()
    assert doc.mime_type == "application/pdf"


# ---------------------------------------------------------------------------
# run_ocr_task
# ---------------------------------------------------------------------------


def test_run_ocr_task_saves_text_and_chains(db_session, document, monkeypatch):
    """Télécharge depuis GCS, OCR, sauvegarde ocr_text, enchaîne classify."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    mock_storage = MagicMock()
    mock_storage.download_to_bytes.return_value = b"fake image bytes"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    mock_ocr = MagicMock()
    mock_ocr.extract_text.return_value = "Diplôme de Baccalauréat — Kofi Mensah — 2024"
    monkeypatch.setattr("app.workers.ocr_tasks.get_ocr_service", lambda: mock_ocr)

    with patch("app.workers.ai_tasks.classify_document_task.delay") as mock_classify:
        from app.workers.ocr_tasks import run_ocr_task
        result = run_ocr_task.run(str(document.id))

    assert result == "ok"
    db_session.refresh(document)
    assert document.ocr_text == "Diplôme de Baccalauréat — Kofi Mensah — 2024"
    mock_classify.assert_called_once_with(str(document.id))


def test_run_ocr_task_empty_text_still_chains(db_session, document, monkeypatch):
    """OCR retourne '' (fichier illisible) → sauvegarde '' et enchaîne quand même."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    mock_storage = MagicMock()
    mock_storage.download_to_bytes.return_value = b"corrupted"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    mock_ocr = MagicMock()
    mock_ocr.extract_text.return_value = ""
    monkeypatch.setattr("app.workers.ocr_tasks.get_ocr_service", lambda: mock_ocr)

    with patch("app.workers.ai_tasks.classify_document_task.delay") as mock_classify:
        from app.workers.ocr_tasks import run_ocr_task
        result = run_ocr_task.run(str(document.id))

    assert result == "ok"
    db_session.refresh(document)
    assert document.ocr_text == ""
    mock_classify.assert_called_once()


def test_run_ocr_task_document_not_found(db_session, monkeypatch):
    """Document introuvable → retourne 'no_document'."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    from app.workers.ocr_tasks import run_ocr_task
    result = run_ocr_task.run(str(uuid.uuid4()))
    assert result == "no_document"


def test_run_ocr_task_passes_mime_to_ocr(db_session, document, monkeypatch):
    """Le mime_type du Document est bien transmis au service OCR."""
    monkeypatch.setattr("app.workers.ocr_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    mock_storage = MagicMock()
    mock_storage.download_to_bytes.return_value = b"data"
    monkeypatch.setattr("app.workers.ocr_tasks.get_storage_service", lambda: mock_storage)

    captured = {}
    mock_ocr = MagicMock()

    def fake_extract(content, mime_type=None):
        captured["mime"] = mime_type
        return "texte"

    mock_ocr.extract_text.side_effect = fake_extract
    monkeypatch.setattr("app.workers.ocr_tasks.get_ocr_service", lambda: mock_ocr)

    with patch("app.workers.ai_tasks.classify_document_task.delay"):
        from app.workers.ocr_tasks import run_ocr_task
        run_ocr_task.run(str(document.id))

    assert captured["mime"] == "image/jpeg"
