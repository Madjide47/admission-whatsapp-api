"""Tests de la machine à états WhatsApp et de l'endpoint Twilio.

Twilio et Celery sont mockés — aucune dépendance externe requise.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.university import University
from app.services.whatsapp_bot import ConversationState, WhatsAppBot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Université de Test",
        email=f"test-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="hash",
        api_secret_hash="hash",
        api_key_prefix="univ_test",
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


@pytest.fixture()
def bot(db_session):
    """WhatsAppBot avec Twilio mocké."""
    with patch("app.services.whatsapp_bot.TwilioClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(sid="SM123")
        b = WhatsAppBot(db_session)
        b._mock_twilio = mock_client
        yield b


@pytest.fixture()
def application(db_session, university) -> Application:
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890000001",
        status=ApplicationStatus.CHOOSING_UNIVERSITY,
        conversation_state=ConversationState.WELCOME.value,
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


# ---------------------------------------------------------------------------
# Machine à états
# ---------------------------------------------------------------------------


def test_welcome_transitions_to_collect_name(bot, application, db_session):
    result = bot._handle_welcome(application, "Bonjour")
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_NAME.value
    assert result["action"] == "asked_name"
    bot._mock_twilio.messages.create.assert_called_once()


def test_collect_name_rejects_short_name(bot, application, db_session):
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_name(application, "A")
    assert result["action"] == "name_too_short"
    assert application.conversation_state == ConversationState.COLLECT_NAME.value


def test_collect_name_transitions_to_collect_program(bot, application, db_session):
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_name(application, "Kofi Mensah")
    db_session.refresh(application)
    assert application.student_name == "Kofi Mensah"
    assert application.conversation_state == ConversationState.COLLECT_PROGRAM.value
    assert result["action"] == "asked_program"


def test_collect_program_rejects_short_program(bot, application, db_session):
    application.conversation_state = ConversationState.COLLECT_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_program(application, "Li")
    assert result["action"] == "program_too_short"


def test_collect_program_transitions_to_collect_docs(bot, application, db_session):
    application.conversation_state = ConversationState.COLLECT_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_program(application, "Licence Informatique")
    db_session.refresh(application)
    assert application.program == "Licence Informatique"
    assert application.conversation_state == ConversationState.COLLECT_DOCS.value
    assert result["action"] == "asked_documents"


def test_handle_incoming_text_full_flow(bot, application, db_session, university):
    """Simule une conversation complète : bienvenue → nom → programme."""
    phone = application.student_phone

    # WELCOME
    bot.handle_incoming_message(phone, "Bonjour", university=university)
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_NAME.value

    # COLLECT_NAME
    bot.handle_incoming_message(phone, "Aïcha Traoré", university=university)
    db_session.refresh(application)
    assert application.student_name == "Aïcha Traoré"
    assert application.conversation_state == ConversationState.COLLECT_PROGRAM.value

    # COLLECT_PROGRAM
    bot.handle_incoming_message(phone, "Master Finance", university=university)
    db_session.refresh(application)
    assert application.program == "Master Finance"
    assert application.conversation_state == ConversationState.COLLECT_DOCS.value


def test_media_upload_queues_celery_task(bot, application, db_session, university):
    """Un envoi de média doit déclencher process_incoming_media.delay."""
    with patch("app.workers.ocr_tasks.process_incoming_media.delay") as mock_delay:
        result = bot._handle_media(
            application,
            "https://twilio.com/media/123",
            "image/jpeg",
            "diplome",
        )
    mock_delay.assert_called_once()
    assert result["action"] == "media_queued"
    assert result["hint"] == "DIPLOME"


def test_guess_document_type_from_caption():
    assert WhatsAppBot._guess_document_type("mon diplome bac") == DocumentType.DIPLOME
    assert WhatsAppBot._guess_document_type("relevé de notes") == DocumentType.RELEVE_NOTES
    assert WhatsAppBot._guess_document_type("carte identité") == DocumentType.CARTE_IDENTITE
    assert WhatsAppBot._guess_document_type("photo id") == DocumentType.PHOTO
    assert WhatsAppBot._guess_document_type("document inconnu") is None


def test_status_message_shows_missing_docs(bot, application, db_session):
    """_send_status liste les documents manquants quand aucun n'est fourni."""
    result = bot._send_status(application)
    assert result["action"] == "status_sent"
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "⏳" in sent_body  # Au moins un document manquant


def test_status_message_complete_when_all_docs_valid(bot, application, db_session):
    """_send_status indique dossier complet quand tous les docs sont valides."""
    for doc_type in [
        DocumentType.DIPLOME,
        DocumentType.RELEVE_NOTES,
        DocumentType.CARTE_IDENTITE,
        DocumentType.PHOTO,
    ]:
        doc = Document(
            id=uuid.uuid4(),
            application_id=application.id,
            document_type=doc_type,
            gcs_path=f"gs://bucket/{doc_type.value}",
            is_valid=True,
        )
        db_session.add(doc)
    db_session.commit()
    db_session.refresh(application)

    result = bot._send_status(application)
    assert result["action"] == "status_sent"
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "complet" in sent_body.lower()


def test_notify_decision_accepted(bot):
    bot.notify_decision("+22890000001", ApplicationStatus.ACCEPTED, "Bienvenue !")
    call_kwargs = bot._mock_twilio.messages.create.call_args.kwargs
    assert "Félicitations" in call_kwargs["body"]
    assert "Bienvenue !" in call_kwargs["body"]


def test_notify_decision_rejected(bot):
    bot.notify_decision("+22890000001", ApplicationStatus.REJECTED, None)
    call_kwargs = bot._mock_twilio.messages.create.call_args.kwargs
    assert "Félicitations" not in call_kwargs["body"]


# ---------------------------------------------------------------------------
# Endpoint Twilio /whatsapp/incoming
# ---------------------------------------------------------------------------


def test_twilio_endpoint_returns_twiml(client, db_session, monkeypatch):
    """POST /whatsapp/incoming retourne un TwiML vide (200)."""
    monkeypatch.setattr(
        "app.services.whatsapp_bot.WhatsAppBot.handle_incoming_message",
        lambda *a, **k: {"state": "WELCOME", "action": "asked_name"},
    )
    r = client.post(
        "/whatsapp/incoming",
        data={
            "From": "whatsapp:+22890000001",
            "To": "whatsapp:+14155238886",
            "Body": "Bonjour",
            "NumMedia": "0",
        },
    )
    assert r.status_code == 200
    assert "<Response>" in r.text


def test_twilio_endpoint_with_media(client, monkeypatch):
    """POST avec NumMedia=1 transmet bien le media_url au bot."""
    captured = {}

    def fake_handle(self, from_number, message_body, media_url, media_content_type):
        captured["media_url"] = media_url
        return {}

    monkeypatch.setattr(
        "app.services.whatsapp_bot.WhatsAppBot.handle_incoming_message",
        fake_handle,
    )
    client.post(
        "/whatsapp/incoming",
        data={
            "From": "whatsapp:+22890000002",
            "To": "whatsapp:+14155238886",
            "Body": "diplome",
            "NumMedia": "1",
            "MediaUrl0": "https://twilio.com/media/abc123",
            "MediaContentType0": "image/jpeg",
        },
    )
    assert captured.get("media_url") == "https://twilio.com/media/abc123"
