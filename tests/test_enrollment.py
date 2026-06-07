"""Tests de la gestion des périodes d'inscription (greffé sur les modèles Dev 1).

Couvre :
- Program.is_enrollment_open()
- Bot AWAITING_ENROLLMENT_CHOICE (option 1 : reporter, option 2 : déposer maintenant)
- check_application_completion_task avec inscriptions fermées → PENDING_ENROLLMENT
- check_enrollment_periods_task (tâche quotidienne d'ouverture)
"""
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import Program
from app.models.university import University
from app.services.whatsapp_bot import ConversationState, WhatsAppBot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Université Enrolment Test",
        email=f"enr-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="enr_test",
        webhook_url="https://example.test/wh",
        webhook_secret="sec",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


@pytest.fixture()
def bot(db_session):
    with patch("app.services.whatsapp_bot.TwilioClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(sid="SM123")
        b = WhatsAppBot(db_session)
        b._mock_twilio = mock_client
        yield b


@pytest.fixture()
def application(db_session, university) -> Application:
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890777777",
        student_name="Test Étudiant",
        status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state=ConversationState.COLLECT_DOCS.value,
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


def _make_program(db_session, university, name="Licence Test", domain="Informatique",
                  enrollment_start=None, enrollment_end=None) -> Program:
    p = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name=name,
        domain=domain,
        is_active=True,
        enrollment_start=enrollment_start,
        enrollment_end=enrollment_end,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _add_valid_doc(db_session, application, doc_type):
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=doc_type,
        gcs_path=f"gs://bucket/{doc_type.value}",
        is_valid=True,
        classification_result={"confidence": 0.9},
    )
    db_session.add(doc)
    db_session.commit()
    return doc


# ---------------------------------------------------------------------------
# Program.is_enrollment_open()
# ---------------------------------------------------------------------------


def test_program_open_when_no_dates():
    p = Program(id=uuid.uuid4(), university_id=uuid.uuid4(), name="Test")
    assert p.is_enrollment_open() is True


def test_program_open_within_period():
    p = Program(
        id=uuid.uuid4(), university_id=uuid.uuid4(), name="Test",
        enrollment_start=date.today() - timedelta(days=5),
        enrollment_end=date.today() + timedelta(days=10),
    )
    assert p.is_enrollment_open() is True


def test_program_closed_before_start():
    p = Program(
        id=uuid.uuid4(), university_id=uuid.uuid4(), name="Test",
        enrollment_start=date.today() + timedelta(days=3),
    )
    assert p.is_enrollment_open() is False


def test_program_closed_after_end():
    p = Program(
        id=uuid.uuid4(), university_id=uuid.uuid4(), name="Test",
        enrollment_end=date.today() - timedelta(days=1),
    )
    assert p.is_enrollment_open() is False


def test_program_open_on_exact_boundaries():
    p_start = Program(id=uuid.uuid4(), university_id=uuid.uuid4(), name="T",
                      enrollment_start=date.today())
    p_end = Program(id=uuid.uuid4(), university_id=uuid.uuid4(), name="T",
                    enrollment_end=date.today())
    assert p_start.is_enrollment_open() is True
    assert p_end.is_enrollment_open() is True


def test_program_reference_date_override():
    p = Program(
        id=uuid.uuid4(), university_id=uuid.uuid4(), name="Test",
        enrollment_start=date(2026, 9, 1), enrollment_end=date(2026, 10, 31),
    )
    assert p.is_enrollment_open(date(2026, 9, 15)) is True
    assert p.is_enrollment_open(date(2026, 8, 31)) is False
    assert p.is_enrollment_open(date(2026, 11, 1)) is False


# ---------------------------------------------------------------------------
# Bot — AWAITING_ENROLLMENT_CHOICE
# ---------------------------------------------------------------------------


def test_choose_program_enrollment_closed_shows_choice(bot, application, university, db_session):
    """Sélection d'un programme hors période → propose 2 options numérotées."""
    _make_program(
        db_session, university, name="Médecine",
        enrollment_start=date.today() + timedelta(days=30),
    )
    application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_program(application, "1")
    db_session.refresh(application)

    assert application.conversation_state == ConversationState.AWAITING_ENROLLMENT_CHOICE.value
    assert result["action"] == "enrollment_closed"
    body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "1️⃣" in body and "2️⃣" in body


def test_choose_program_enrollment_open_proceeds(bot, application, university, db_session):
    """Programme avec inscriptions ouvertes → flux normal COLLECT_DOCS."""
    _make_program(
        db_session, university, name="Droit",
        enrollment_start=date.today() - timedelta(days=5),
        enrollment_end=date.today() + timedelta(days=30),
    )
    application.conversation_state = ConversationState.CHOOSE_PROGRAM.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_choose_program(application, "1")
    db_session.refresh(application)

    assert application.conversation_state == ConversationState.COLLECT_DOCS.value
    assert result["action"] == "asked_documents"


def test_enrollment_choice_1_defers_and_deletes(bot, application, university, db_session):
    """Option 1 (revenir plus tard) → application supprimée, message d'au revoir."""
    _make_program(
        db_session, university, name="Pharmacie",
        enrollment_start=date.today() + timedelta(days=15),
    )
    application.program = "Pharmacie"
    application.conversation_state = ConversationState.AWAITING_ENROLLMENT_CHOICE.value
    db_session.add(application)
    db_session.commit()
    app_id = application.id

    result = bot._handle_awaiting_enrollment_choice(application, "1")

    assert db_session.get(Application, app_id) is None
    assert result["action"] == "enrollment_deferred"
    body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "Bonjour" in body
    assert "15" in body or "/" in body  # date d'ouverture mentionnée


def test_enrollment_choice_2_accepts(bot, application, university, db_session):
    """Option 2 (déposer maintenant) → COLLECT_DOCS, candidature conservée."""
    _make_program(
        db_session, university, name="Économie",
        enrollment_start=date.today() + timedelta(days=10),
    )
    application.program = "Économie"
    application.conversation_state = ConversationState.AWAITING_ENROLLMENT_CHOICE.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_awaiting_enrollment_choice(application, "2")
    db_session.refresh(application)

    assert application.conversation_state == ConversationState.COLLECT_DOCS.value
    # Le dépôt différé passe désormais par _begin_collection (champs → documents).
    # Sans formulaire publié pour ce programme, on va directement aux documents.
    assert result["action"] in ("asked_documents", "asked_field")
    body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "ouverture" in body.lower()


def test_enrollment_choice_invalid(bot, application, db_session):
    """Choix invalide → re-affiche les 2 options."""
    application.conversation_state = ConversationState.AWAITING_ENROLLMENT_CHOICE.value
    db_session.add(application)
    db_session.commit()

    result = bot._handle_awaiting_enrollment_choice(application, "99")
    assert result["action"] == "invalid_enrollment_choice"
    body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "1️⃣" in body


def test_pending_enrollment_intercept(bot, application, db_session):
    """Statut PENDING_ENROLLMENT → tout message reçoit une réponse d'attente."""
    application.status = ApplicationStatus.PENDING_ENROLLMENT
    db_session.add(application)
    db_session.commit()

    result = bot.handle_incoming_message(application.student_phone, "bonjour")
    assert result["action"] == "pending_enrollment_notice"
    body = bot._mock_twilio.messages.create.call_args.kwargs["body"]
    assert "attente" in body.lower() or "inscription" in body.lower()


# ---------------------------------------------------------------------------
# ai_tasks — completion avec inscriptions fermées
# ---------------------------------------------------------------------------


def test_completion_sets_pending_when_closed(db_session, application, university, monkeypatch):
    """Dossier complet + inscriptions fermées → PENDING_ENROLLMENT (pas de dispatch)."""
    _make_program(
        db_session, university, name="Test Fermé",
        enrollment_start=date.today() + timedelta(days=10),
    )
    application.program = "Test Fermé"
    db_session.add(application)
    for doc_type in [DocumentType.DIPLOME, DocumentType.RELEVE_NOTES,
                     DocumentType.CARTE_IDENTITE, DocumentType.PHOTO]:
        _add_valid_doc(db_session, application, doc_type)
    db_session.refresh(application)

    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.ai_tasks import check_application_completion_task
        status = check_application_completion_task.run(str(application.id))

    db_session.refresh(application)
    assert application.status == ApplicationStatus.PENDING_ENROLLMENT
    assert status == ApplicationStatus.PENDING_ENROLLMENT.value
    mock_dispatch.assert_not_called()


def test_completion_dispatches_when_open(db_session, application, university, monkeypatch):
    """Dossier complet + inscriptions ouvertes → VALIDATED + dispatch."""
    _make_program(
        db_session, university, name="Test Ouvert",
        enrollment_start=date.today() - timedelta(days=5),
    )
    application.program = "Test Ouvert"
    db_session.add(application)
    for doc_type in [DocumentType.DIPLOME, DocumentType.RELEVE_NOTES,
                     DocumentType.CARTE_IDENTITE, DocumentType.PHOTO]:
        _add_valid_doc(db_session, application, doc_type)
    db_session.refresh(application)

    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.ai_tasks import check_application_completion_task
        check_application_completion_task.run(str(application.id))

    db_session.refresh(application)
    assert application.status == ApplicationStatus.VALIDATED
    mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# check_enrollment_periods_task
# ---------------------------------------------------------------------------


def test_enrollment_task_dispatches_when_open(db_session, application, university, monkeypatch):
    _make_program(db_session, university, name="Ouvert", enrollment_start=date.today())
    application.program = "Ouvert"
    application.status = ApplicationStatus.PENDING_ENROLLMENT
    db_session.add(application)
    db_session.commit()

    monkeypatch.setattr("app.workers.enrollment_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.enrollment_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.enrollment_tasks import check_enrollment_periods_task
        result = check_enrollment_periods_task.run()

    db_session.refresh(application)
    assert application.status == ApplicationStatus.VALIDATED
    assert result == "dispatched:1"
    mock_dispatch.assert_called_once_with(str(application.id))


def test_enrollment_task_skips_when_closed(db_session, application, university, monkeypatch):
    _make_program(db_session, university, name="Fermé",
                  enrollment_start=date.today() + timedelta(days=5))
    application.program = "Fermé"
    application.status = ApplicationStatus.PENDING_ENROLLMENT
    db_session.add(application)
    db_session.commit()

    monkeypatch.setattr("app.workers.enrollment_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.workers.enrollment_tasks.send_whatsapp", MagicMock())

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        from app.workers.enrollment_tasks import check_enrollment_periods_task
        result = check_enrollment_periods_task.run()

    db_session.refresh(application)
    assert application.status == ApplicationStatus.PENDING_ENROLLMENT
    assert result == "dispatched:0"
    mock_dispatch.assert_not_called()
