"""Tests de l'injection des prérequis WhatsApp au choix du programme (Phase 4)."""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.program import Program
from app.models.program_criteria import ProgramCriteria
from app.models.university import University
from app.services.whatsapp_bot import ConversationState, WhatsAppBot


@pytest.fixture()
def university(db_session) -> University:
    u = University(
        id=uuid.uuid4(), name="Univ Prereq",
        email=f"pr-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash=f"h_{uuid.uuid4().hex}", api_secret_hash="x",
        api_key_prefix=f"k_{uuid.uuid4().hex[:6]}", is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def bot(db_session):
    with patch("app.services.whatsapp_bot.TwilioClient") as cls:
        cls.return_value = MagicMock()
        b = WhatsAppBot(db_session)
        b.send_message = MagicMock()
        yield b


def _program(db, univ_id, name="Master Finance"):
    p = Program(id=uuid.uuid4(), university_id=univ_id, name=name, is_active=True)
    db.add(p)
    db.commit()
    return p


def _criteria(db, program_id, *, display=True, **kw):
    c = ProgramCriteria(
        program_id=program_id,
        prerequisites=kw.get("prerequisites", ["Lettre de motivation"]),
        min_average=kw.get("min_average", 14),
        required_degree=kw.get("required_degree", "Bac+3"),
        accepted_specialties=kw.get("accepted_specialties", ["Économie", "Gestion"]),
        whatsapp_display=display,
    )
    db.add(c)
    db.commit()
    return c


def _app(db, univ_id):
    a = Application(
        id=uuid.uuid4(), university_id=univ_id, student_phone="+22890000009",
        student_name="Kofi", status=ApplicationStatus.CHOOSING_PROGRAM,
        conversation_state=ConversationState.CHOOSE_PROGRAM.value,
    )
    db.add(a)
    db.commit()
    return a


def test_prerequisites_shown_when_configured(db_session, bot, university):
    p = _program(db_session, university.id)
    _criteria(db_session, p.id)
    app = _app(db_session, university.id)

    bot._handle_choose_program(app, "1")

    assert app.conversation_state == ConversationState.CONFIRM_PREREQUISITES.value
    msg = bot.send_message.call_args[0][1]
    assert "Prérequis" in msg
    assert "Bac+3" in msg
    assert "≥ 14/20" in msg
    assert "Économie" in msg
    assert "oui" in msg.lower()


def test_no_prerequisites_goes_straight_to_collection(db_session, bot, university):
    p = _program(db_session, university.id)  # aucun ProgramCriteria
    app = _app(db_session, university.id)

    bot._handle_choose_program(app, "1")

    assert app.conversation_state != ConversationState.CONFIRM_PREREQUISITES.value


def test_display_disabled_skips_prerequisites(db_session, bot, university):
    p = _program(db_session, university.id)
    _criteria(db_session, p.id, display=False)
    app = _app(db_session, university.id)

    bot._handle_choose_program(app, "1")

    assert app.conversation_state != ConversationState.CONFIRM_PREREQUISITES.value


def test_confirm_yes_starts_collection(db_session, bot, university):
    p = _program(db_session, university.id)
    _criteria(db_session, p.id)
    app = _app(db_session, university.id)
    bot._handle_choose_program(app, "1")
    assert app.conversation_state == ConversationState.CONFIRM_PREREQUISITES.value

    bot._handle_confirm_prerequisites(app, "oui")
    # On a quitté l'état de confirmation pour démarrer la collecte
    assert app.conversation_state in (
        ConversationState.COLLECT_FIELDS.value,
        ConversationState.COLLECT_DOCS.value,
    )


def test_confirm_no_returns_to_choose_program(db_session, bot, university):
    p = _program(db_session, university.id)
    _criteria(db_session, p.id)
    app = _app(db_session, university.id)
    bot._handle_choose_program(app, "1")

    bot._handle_confirm_prerequisites(app, "non")
    assert app.conversation_state == ConversationState.CHOOSE_PROGRAM.value
    assert app.program_id is None


def test_confirm_invalid_reasks(db_session, bot, university):
    p = _program(db_session, university.id)
    _criteria(db_session, p.id)
    app = _app(db_session, university.id)
    bot._handle_choose_program(app, "1")
    bot.send_message.reset_mock()

    bot._handle_confirm_prerequisites(app, "peut-être")
    assert app.conversation_state == ConversationState.CONFIRM_PREREQUISITES.value
    msg = bot.send_message.call_args[0][1]
    assert "oui" in msg.lower() and "non" in msg.lower()
