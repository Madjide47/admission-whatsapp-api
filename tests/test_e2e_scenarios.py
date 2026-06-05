"""Tests d'intégration bout en bout des composants Dev 2 (sur modèles Dev 1).

Pilote une conversation complète via WhatsAppBot et exécute le pipeline
worker (classification + complétion) avec tous les services externes mockés :
Twilio, Gemini/Anthropic, Tesseract, GCS.

Les documents requis reposent sur le fallback du validator (4 types hardcodés)
puisqu'on ne configure pas la hiérarchie AdmissionForm → RequiredDocument ici.

Couvre :
- Parcours complet : domaine → université → nom → programme → docs → VALIDATED
- Document invalide → redemande du même document
- Message hors-contexte → guidage
- Commande "statut"
- Pas de doublon de candidature pour un même numéro
- Inscriptions fermées → PENDING_ENROLLMENT → ouverture → dispatch
"""
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import Program
from app.models.university import University
from app.schemas.document import DocumentClassificationResult
from app.services.whatsapp_bot import ConversationState, WhatsAppBot


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Université de Lomé",
        email=f"e2e-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="e2e_test",
        webhook_url="https://example.test/wh",
        webhook_secret="sec",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


def _make_program(db_session, university, name, domain, **dates) -> Program:
    """Crée un Program (les documents requis viennent du fallback validator)."""
    prog = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name=name,
        domain=domain,
        is_active=True,
        enrollment_start=dates.get("enrollment_start"),
        enrollment_end=dates.get("enrollment_end"),
    )
    db_session.add(prog)
    db_session.commit()
    db_session.refresh(prog)
    return prog


@pytest.fixture()
def bot(db_session):
    with patch("app.services.whatsapp_bot.TwilioClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(sid="SM_E2E")
        b = WhatsAppBot(db_session)
        b._mock_twilio = mock_client
        yield b


def _last_msg(bot) -> str:
    return bot._mock_twilio.messages.create.call_args.kwargs["body"]


def _classification(doc_type, is_valid=True, confidence=0.9, errors=None):
    return DocumentClassificationResult(
        type=doc_type, confidence=confidence, is_valid=is_valid,
        errors=errors or [], extracted_fields={},
    )


def _process_document(db_session, monkeypatch, application, doc_type, is_valid=True, errors=None):
    """Simule l'arrivée + traitement complet d'un document (classify → completion)."""
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=doc_type,
        gcs_path=f"gs://bucket/{doc_type.value}-{uuid.uuid4().hex[:6]}",
        mime_type="image/jpeg",
        ocr_text=f"Texte OCR simulé pour {doc_type.value}",
        is_valid=False,
    )
    db_session.add(doc)
    db_session.commit()

    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.workers.ai_tasks.get_ai_classifier",
        lambda: MagicMock(classify=lambda **kw: _classification(doc_type, is_valid, errors=errors)),
    )
    monkeypatch.setattr("app.workers.ai_tasks.send_whatsapp", MagicMock())

    from app.workers.ai_tasks import classify_document_task, check_application_completion_task

    with patch("app.workers.ai_tasks.check_application_completion_task.delay"), \
         patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch:
        classify_document_task.run(str(doc.id))
        check_application_completion_task.run(str(application.id))

    db_session.refresh(application)
    return doc, mock_dispatch


def _all_docs(db_session, monkeypatch, application):
    last_dispatch = None
    for doc_type in [DocumentType.DIPLOME, DocumentType.RELEVE_NOTES,
                     DocumentType.CARTE_IDENTITE, DocumentType.PHOTO]:
        _, last_dispatch = _process_document(db_session, monkeypatch, application, doc_type)
    return last_dispatch


# ---------------------------------------------------------------------------
# 1. Parcours complet (happy path)
# ---------------------------------------------------------------------------


def test_e2e_full_happy_path(bot, db_session, university, monkeypatch):
    _make_program(
        db_session, university, "Licence Informatique", "Informatique",
        enrollment_start=date.today() - timedelta(days=5),
        enrollment_end=date.today() + timedelta(days=30),
    )
    phone = "whatsapp:+22890100100"

    bot.handle_incoming_message(phone, "Bonjour", university=university)
    application = db_session.query(Application).filter_by(student_phone="+22890100100").first()
    assert application.conversation_state == ConversationState.COLLECT_INTEREST.value

    bot.handle_incoming_message(phone, "1", university=university)  # domaine
    db_session.refresh(application)
    assert application.conversation_state == ConversationState.COLLECT_NAME.value  # 1 seule univ

    bot.handle_incoming_message(phone, "Kofi Mensah", university=university)
    db_session.refresh(application)
    assert application.student_name == "Kofi Mensah"
    assert application.conversation_state == ConversationState.COLLECT_DOCS.value  # 1 seul programme

    mock_dispatch = _all_docs(db_session, monkeypatch, application)
    db_session.refresh(application)
    assert application.status == ApplicationStatus.VALIDATED
    assert application.validation_score and application.validation_score > 0
    mock_dispatch.assert_called_once_with(str(application.id))


# ---------------------------------------------------------------------------
# 2. Document invalide → redemande
# ---------------------------------------------------------------------------


def test_e2e_invalid_document_resend(bot, db_session, university, monkeypatch):
    _make_program(db_session, university, "Licence Droit", "Droit")
    application = Application(
        id=uuid.uuid4(), university_id=university.id,
        student_phone="+22890200200", student_name="Ama Koffi",
        program="Licence Droit", status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state=ConversationState.COLLECT_DOCS.value,
    )
    db_session.add(application)
    db_session.commit()

    _, mock_dispatch = _process_document(
        db_session, monkeypatch, application,
        DocumentType.DIPLOME, is_valid=False, errors=["Document illisible"],
    )
    db_session.refresh(application)
    assert application.status == ApplicationStatus.COLLECTING_DOCUMENTS
    mock_dispatch.assert_not_called()

    from app.services.whatsapp_bot import get_next_required_document
    required = bot._get_required_doc_types(application)
    assert get_next_required_document(application, required_types=required) == DocumentType.DIPLOME

    _process_document(db_session, monkeypatch, application, DocumentType.DIPLOME, is_valid=True)
    db_session.refresh(application)
    assert get_next_required_document(application, required_types=required) == DocumentType.RELEVE_NOTES


# ---------------------------------------------------------------------------
# 3. Message hors-contexte
# ---------------------------------------------------------------------------


def test_e2e_out_of_context_message(bot, db_session, university):
    _make_program(db_session, university, "Licence Info", "Informatique")
    phone = "whatsapp:+22890300300"

    bot.handle_incoming_message(phone, "Bonjour", university=university)  # → COLLECT_INTEREST
    r = bot.handle_incoming_message(phone, "n'importe quoi", university=university)
    assert r["action"] == "invalid_domain_choice"
    assert "numéro" in _last_msg(bot).lower()


# ---------------------------------------------------------------------------
# 4. Commande "statut"
# ---------------------------------------------------------------------------


def test_e2e_status_command(bot, db_session, university, monkeypatch):
    _make_program(db_session, university, "Licence Info", "Informatique")
    application = Application(
        id=uuid.uuid4(), university_id=university.id,
        student_phone="+22890400400", student_name="Yao Test",
        program="Licence Info", status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state=ConversationState.COLLECT_DOCS.value,
    )
    db_session.add(application)
    db_session.commit()

    _process_document(db_session, monkeypatch, application, DocumentType.DIPLOME, is_valid=True)

    r = bot.handle_incoming_message("whatsapp:+22890400400", "statut")
    assert r["action"] == "status_sent"
    body = _last_msg(bot)
    assert "✅" in body and "⏳" in body


# ---------------------------------------------------------------------------
# 5. Pas de doublon pour un même numéro
# ---------------------------------------------------------------------------


def test_e2e_no_duplicate_application(bot, db_session, university):
    _make_program(db_session, university, "Licence Info", "Informatique")
    phone = "whatsapp:+22890500500"

    bot.handle_incoming_message(phone, "Bonjour", university=university)
    bot.handle_incoming_message(phone, "1", university=university)
    bot.handle_incoming_message(phone, "Bonjour encore", university=university)

    count = db_session.query(Application).filter_by(student_phone="+22890500500").count()
    assert count == 1


# ---------------------------------------------------------------------------
# 6. Inscriptions fermées → PENDING_ENROLLMENT → ouverture → dispatch
# ---------------------------------------------------------------------------


def test_e2e_enrollment_closed_then_opens(bot, db_session, university, monkeypatch):
    prog = _make_program(
        db_session, university, "Médecine Générale", "Médecine",
        enrollment_start=date.today() + timedelta(days=10),
        enrollment_end=date.today() + timedelta(days=60),
    )
    application = Application(
        id=uuid.uuid4(), university_id=university.id,
        student_phone="+22890600600", student_name="Esi Test",
        program="Médecine Générale", status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state=ConversationState.COLLECT_DOCS.value,
    )
    db_session.add(application)
    db_session.commit()

    mock_dispatch = _all_docs(db_session, monkeypatch, application)
    db_session.refresh(application)
    assert application.status == ApplicationStatus.PENDING_ENROLLMENT
    mock_dispatch.assert_not_called()

    # Les inscriptions ouvrent
    prog.enrollment_start = date.today()
    db_session.add(prog)
    db_session.commit()

    monkeypatch.setattr("app.workers.enrollment_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr("app.workers.enrollment_tasks.send_whatsapp", MagicMock())
    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_dispatch2:
        from app.workers.enrollment_tasks import check_enrollment_periods_task
        result = check_enrollment_periods_task.run()

    db_session.refresh(application)
    assert application.status == ApplicationStatus.VALIDATED
    assert result == "dispatched:1"
    mock_dispatch2.assert_called_once_with(str(application.id))
