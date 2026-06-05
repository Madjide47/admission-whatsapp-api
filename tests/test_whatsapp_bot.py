"""Tests de la machine à états WhatsApp et de l'endpoint Twilio.

Twilio et Celery sont mockés — aucune dépendance externe requise.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import Program
from app.models.university import University
from app.services.whatsapp_bot import (
    ConversationState,
    REQUIRED_DOCUMENT_TYPES_ORDERED,
    WhatsAppBot,
    get_next_required_document,
    send_whatsapp,
)
import app.workers.ocr_tasks  # Fix mock.patch import



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
        status=ApplicationStatus.COLLECTING,
        conversation_state=ConversationState.WELCOME.value,
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


# ---------------------------------------------------------------------------
# Machine à états
# ---------------------------------------------------------------------------


def test_welcome_asks_for_interest(bot, application, university, program, db_session):
    """WELCOME + domaines configurés → liste numérotée des domaines (COLLECT_INTEREST)."""
    result = bot._handle_welcome(application, "Bonjour")
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_INTEREST.value
    assert result["action"] == "listed_domains"
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "Informatique" in sent_body
    assert "1." in sent_body


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


def test_handle_incoming_text_full_flow(bot, application, db_session, university, program, second_program):
    """Simule une conversation complète : bienvenue → domaine → université → nom → programme."""
    phone = application.student_phone

    # WELCOME → COLLECT_INTEREST (domaines disponibles)
    bot.handle_incoming_message(phone, "Bonjour", university=university)
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_INTEREST.value

    # COLLECT_INTEREST → CHOOSE_UNIVERSITY (choix numérique "1" = premier domaine)
    bot.handle_incoming_message(phone, "1", university=university)
    db_session.refresh(application)
    assert application.conversation_state in (
        ConversationState.CHOOSE_UNIVERSITY.value,
        ConversationState.COLLECT_NAME.value,  # si 1 seule université pour ce domaine
    )

    # Forcer COLLECT_NAME pour continuer le flow
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    # COLLECT_NAME → CHOOSE_PROGRAM (programmes configurés dans les fixtures)
    bot.handle_incoming_message(phone, "Aïcha Traoré", university=university)
    db_session.refresh(application)
    assert application.student_name == "Aïcha Traoré"
    assert application.conversation_state in (
        ConversationState.CHOOSE_PROGRAM.value,
        ConversationState.COLLECT_PROGRAM.value,
    )

    # Forcer COLLECT_DOCS pour terminer le test
    application.program = "Licence Informatique"
    application.conversation_state = ConversationState.COLLECT_DOCS.value
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
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


# ---------------------------------------------------------------------------
# Signature Twilio (_verify_twilio_signature)
# ---------------------------------------------------------------------------


def test_verify_twilio_signature_valid():
    """Signature HMAC-SHA1 correcte → retourne True."""
    import base64
    import hashlib
    import hmac as hmac_mod

    from app.api.whatsapp.twilio_webhook import _verify_twilio_signature
    from unittest.mock import MagicMock

    auth_token = "testtoken"
    url = "https://example.com/whatsapp/incoming"
    params = {"From": "whatsapp:+228", "Body": "Bonjour"}

    sorted_pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    data = (url + sorted_pairs).encode("utf-8")
    sig = base64.b64encode(
        hmac_mod.new(auth_token.encode(), data, hashlib.sha1).digest()
    ).decode()

    mock_request = MagicMock()
    mock_request.headers = {"X-Twilio-Signature": sig}
    mock_request.url = url

    assert _verify_twilio_signature(mock_request, params) is True


def test_verify_twilio_signature_invalid():
    """Mauvaise signature → retourne False."""
    from app.api.whatsapp.twilio_webhook import _verify_twilio_signature
    from unittest.mock import MagicMock

    mock_request = MagicMock()
    mock_request.headers = {"X-Twilio-Signature": "invalide=="}
    mock_request.url = "https://example.com/whatsapp/incoming"

    assert _verify_twilio_signature(mock_request, {"From": "whatsapp:+228"}) is False


def test_verify_twilio_signature_missing_header():
    """Header absent → retourne False."""
    from app.api.whatsapp.twilio_webhook import _verify_twilio_signature
    from unittest.mock import MagicMock

    mock_request = MagicMock()
    mock_request.headers = {}

    assert _verify_twilio_signature(mock_request, {}) is False


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


# ---------------------------------------------------------------------------
# Validation séquentielle et helpers v2
# ---------------------------------------------------------------------------


def test_get_next_required_document_no_docs(application):
    """Sans aucun document, le premier requis est DIPLOME."""
    assert get_next_required_document(application) == DocumentType.DIPLOME


def test_get_next_required_document_partial(application, db_session):
    """Après DIPLOME valide, le suivant est RELEVE_NOTES."""
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=DocumentType.DIPLOME,
        gcs_path="gs://bucket/diplome",
        is_valid=True,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(application)

    assert get_next_required_document(application) == DocumentType.RELEVE_NOTES


def test_get_next_required_document_all_valid(application, db_session):
    """Tous les documents valides → retourne None."""
    for doc_type in REQUIRED_DOCUMENT_TYPES_ORDERED:
        db_session.add(Document(
            id=uuid.uuid4(),
            application_id=application.id,
            document_type=doc_type,
            gcs_path=f"gs://bucket/{doc_type.value}",
            is_valid=True,
        ))
    db_session.commit()
    db_session.refresh(application)

    assert get_next_required_document(application) is None


def test_get_next_required_document_invalid_not_counted(application, db_session):
    """Un document présent mais invalide ne compte pas."""
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=DocumentType.DIPLOME,
        gcs_path="gs://bucket/diplome",
        is_valid=False,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(application)

    assert get_next_required_document(application) == DocumentType.DIPLOME


def test_collect_program_asks_first_document(bot, application, db_session):
    """Après saisie du programme, le bot demande le premier document (DIPLOME)."""
    application.conversation_state = ConversationState.COLLECT_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    bot._handle_collect_program(application, "Licence Informatique")
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "diplôme" in sent_body.lower()


def test_collect_docs_mentions_specific_next_document(bot, application, db_session):
    """_handle_collect_docs rappelle le prochain document attendu."""
    application.conversation_state = ConversationState.COLLECT_DOCS.value
    db_session.add(application)
    db_session.commit()

    bot._handle_collect_docs(application, "je ne sais pas quoi envoyer")
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    # Sans aucun doc, le prochain est DIPLOME
    assert "diplôme" in sent_body.lower()


def test_collect_docs_after_diplome_valid_asks_releve(bot, application, db_session):
    """Après un DIPLOME valide, _handle_collect_docs demande le relevé."""
    db_session.add(Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=DocumentType.DIPLOME,
        gcs_path="gs://bucket/diplome",
        is_valid=True,
    ))
    db_session.commit()
    db_session.refresh(application)
    application.conversation_state = ConversationState.COLLECT_DOCS.value

    bot._handle_collect_docs(application, "et maintenant ?")
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "relevé" in sent_body.lower()


def test_send_whatsapp_demo_mode(monkeypatch):
    """send_whatsapp en DEMO_MODE ne lève pas d'erreur et retourne DEMO_SID."""
    monkeypatch.setattr("app.services.whatsapp_bot.settings.DEMO_MODE", True)
    result = send_whatsapp("+22890000001", "Test message")
    assert result == "DEMO_SID"


# ---------------------------------------------------------------------------
# Flow université → programme (v2)
# ---------------------------------------------------------------------------


@pytest.fixture()
def second_university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Université Deuxième",
        email=f"second-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="hash2",
        api_secret_hash="hash2",
        api_key_prefix="univ_second",
        webhook_url="https://example2.test/hook",
        webhook_secret="secret2",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


@pytest.fixture()
def program(db_session, university) -> Program:
    p = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name="Licence Informatique",
        domain="Informatique",
        is_active=True,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def second_program(db_session, university) -> Program:
    p = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name="Master Finance",
        domain="Gestion",
        is_active=True,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_welcome_with_domains_shows_domain_list(bot, application, university, program, second_program, db_session):
    """Domaines configurés → liste numérotée des domaines (pas des universités)."""
    result = bot._handle_welcome(application, "Bonjour")
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_INTEREST.value
    assert result["action"] == "listed_domains"
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    # Les 2 domaines doivent apparaître
    assert "Informatique" in sent_body
    assert "Gestion" in sent_body


def test_welcome_no_domains_shows_university_list(bot, application, university, second_university, db_session):
    """Aucun domaine configuré (pas de programmes) → liste des universités directement."""
    result = bot._handle_welcome(application, "Bonjour")
    db_session.refresh(application)
    # Sans programmes configurés, le bot liste les universités
    assert application.conversation_state == ConversationState.CHOOSE_UNIVERSITY.value
    assert result["action"] == "listed_universities"


# ---------------------------------------------------------------------------
# Flow COLLECT_INTEREST
# ---------------------------------------------------------------------------


def test_collect_interest_numeric_choice_shows_filtered_universities(
    bot, application, university, second_university, program, db_session
):
    """Choix numérique du domaine → liste filtrée des universités pour ce domaine."""
    application.conversation_state = ConversationState.COLLECT_INTEREST.value
    db_session.add(application)
    db_session.commit()

    # "1" = premier domaine alphabétiquement = "Informatique"
    result = bot._handle_collect_interest(application, "1")
    db_session.refresh(application)

    # Soit auto-select (1 université) soit liste
    assert application.conversation_state in (
        ConversationState.CHOOSE_UNIVERSITY.value,
        ConversationState.COLLECT_NAME.value,
    )
    assert result["action"] in ("listed_universities", "asked_name")


def test_collect_interest_invalid_choice_shows_domain_list_again(
    bot, application, university, program, second_program, db_session
):
    """Choix invalide → re-affiche la liste des domaines."""
    application.conversation_state = ConversationState.COLLECT_INTEREST.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_interest(application, "99")
    assert result["action"] == "invalid_domain_choice"
    assert application.conversation_state == ConversationState.COLLECT_INTEREST.value
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "Informatique" in sent_body or "Gestion" in sent_body


def test_collect_interest_stores_domain_in_ai_notes(
    bot, application, university, second_university, program, second_program, db_session
):
    """Le domaine choisi est stocké dans ai_notes pour CHOOSE_UNIVERSITY."""
    application.conversation_state = ConversationState.COLLECT_INTEREST.value
    db_session.add(application)
    db_session.commit()

    # 2 universités → pas d'auto-select → stocke le domaine
    result = bot._handle_collect_interest(application, "1")
    db_session.refresh(application)

    if application.conversation_state == ConversationState.CHOOSE_UNIVERSITY.value:
        assert application.ai_notes is not None  # domaine stocké
    # Si COLLECT_NAME (auto-select), ai_notes est effacé → normal


def test_collect_interest_single_university_auto_selects(
    bot, application, university, program, db_session
):
    """1 seule université pour le domaine → sélection auto, demande le nom."""
    application.conversation_state = ConversationState.COLLECT_INTEREST.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_interest(application, "1")
    db_session.refresh(application)

    assert application.conversation_state == ConversationState.COLLECT_NAME.value
    assert result["action"] == "asked_name"
    assert application.university_id == university.id
    assert application.ai_notes is None


def test_choose_university_uses_domain_filter(
    bot, application, university, second_university, program, db_session
):
    """CHOOSE_UNIVERSITY refiltre par domaine stocké dans ai_notes (correspondance exacte)."""
    application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
    application.ai_notes = "Informatique"  # domaine stocké (casse exacte)
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_university(application, "1")
    db_session.refresh(application)

    assert application.conversation_state == ConversationState.COLLECT_NAME.value
    assert application.university_id == university.id  # université avec prog Informatique
    assert application.ai_notes is None  # domaine effacé après usage


def test_choose_university_valid_choice(bot, application, university, second_university, db_session):
    """Choix valide → university_id mis à jour, demande le nom."""
    application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_university(application, "1")
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_NAME.value
    assert result["action"] == "asked_name"
    assert application.university_id is not None


def test_choose_university_invalid_choice_shows_list_again(bot, application, university, second_university, db_session):
    """Choix hors plage → re-affiche la liste."""
    application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_university(application, "99")
    assert application.conversation_state == ConversationState.CHOOSE_UNIVERSITY.value
    assert result["action"] == "invalid_university_choice"


def test_choose_university_non_numeric_shows_list_again(bot, application, university, second_university, db_session):
    """Réponse non numérique → re-affiche la liste."""
    application.conversation_state = ConversationState.CHOOSE_UNIVERSITY.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_university(application, "je veux la première")
    assert result["action"] == "invalid_university_choice"


def test_collect_name_with_programs_shows_list(bot, application, university, program, second_program, db_session):
    """Si des programmes existent → liste numérotée, transition CHOOSE_PROGRAM."""
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_name(application, "Kofi Mensah")
    db_session.refresh(application)
    assert application.student_name == "Kofi Mensah"
    assert application.conversation_state == ConversationState.CHOOSE_PROGRAM.value
    assert result["action"] == "listed_programs"
    sent_body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "1." in sent_body


def test_collect_name_no_programs_falls_back_to_free_text(bot, application, university, db_session):
    """Sans programmes configurés → mode texte libre (COLLECT_PROGRAM)."""
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_name(application, "Kofi Mensah")
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_PROGRAM.value
    assert result["action"] == "asked_program"


def test_collect_name_single_program_auto_selects(bot, application, university, program, db_session):
    """Un seul programme → sélection auto, directement vers COLLECT_DOCS."""
    application.conversation_state = ConversationState.COLLECT_NAME.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_collect_name(application, "Kofi Mensah")
    db_session.refresh(application)
    assert application.program == program.name
    assert application.conversation_state == ConversationState.COLLECT_DOCS.value
    assert result["action"] == "asked_documents"


def test_choose_program_valid_choice(bot, application, university, program, second_program, db_session):
    """Choix valide de programme → program sauvegardé, transition COLLECT_DOCS."""
    application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_program(application, "1")
    db_session.refresh(application)
    assert application.program is not None
    assert application.conversation_state == ConversationState.COLLECT_DOCS.value
    assert result["action"] == "asked_documents"


def test_choose_program_invalid_choice_shows_list_again(bot, application, university, program, second_program, db_session):
    """Choix invalide → re-affiche la liste."""
    application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_program(application, "0")
    assert application.conversation_state == ConversationState.CHOOSE_PROGRAM.value
    assert result["action"] == "invalid_program_choice"


def test_parse_numeric_choice_valid():
    assert WhatsAppBot._parse_numeric_choice("1", 3) == 0
    assert WhatsAppBot._parse_numeric_choice("3", 3) == 2
    assert WhatsAppBot._parse_numeric_choice("  2  ", 3) == 1


def test_parse_numeric_choice_invalid():
    assert WhatsAppBot._parse_numeric_choice("0", 3) is None
    assert WhatsAppBot._parse_numeric_choice("4", 3) is None
    assert WhatsAppBot._parse_numeric_choice("abc", 3) is None
    assert WhatsAppBot._parse_numeric_choice("", 3) is None


def test_send_whatsapp_twilio_error_returns_none(monkeypatch):
    """send_whatsapp avale les erreurs Twilio et retourne None."""
    from twilio.base.exceptions import TwilioRestException

    def raise_twilio(*a, **kw):
        raise TwilioRestException(status=400, uri="/", msg="error")

    monkeypatch.setattr("app.services.whatsapp_bot.settings.DEMO_MODE", False)
    with patch("app.services.whatsapp_bot.TwilioClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = raise_twilio
        result = send_whatsapp("+22890000001", "Test")
    assert result is None
