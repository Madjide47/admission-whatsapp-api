"""Tests du Tool Executor du chatbot admin (les 7 outils)."""
import uuid
from unittest.mock import MagicMock

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import Program
from app.models.program_criteria import ProgramCriteria
from app.models.university import University
from app.services import admin_chat_tools as tools


@pytest.fixture()
def univ(db_session) -> University:
    u = University(
        id=uuid.uuid4(),
        name="Univ Tools",
        email=f"tools-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix=f"k_{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _add_app(db, univ_id, *, name, program="Licence Informatique",
             status=ApplicationStatus.VALIDATED, score=0.9, average=None):
    a = Application(
        id=uuid.uuid4(),
        university_id=univ_id,
        student_phone=f"+228{uuid.uuid4().hex[:8]}",
        student_name=name,
        program=program,
        status=status,
        validation_score=score,
        average=average,
    )
    db.add(a)
    db.commit()
    return a


# ----------------------------------------------------------------------
# search_applications
# ----------------------------------------------------------------------
def test_search_orders_best_first(db_session, univ):
    _add_app(db_session, univ.id, name="A", average=12)
    _add_app(db_session, univ.id, name="B", average=18)
    _add_app(db_session, univ.id, name="C", average=None)
    _add_app(db_session, univ.id, name="D", average=15)

    res = tools.search_applications(db_session, univ.id)
    names = [a["student_name"] for a in res["applications"]]
    assert names[:3] == ["B", "D", "A"]  # 18, 15, 12 ; None en dernier
    assert names[-1] == "C"
    assert res["count"] == 4


def test_search_min_average_filter(db_session, univ):
    _add_app(db_session, univ.id, name="Low", average=11)
    _add_app(db_session, univ.id, name="High", average=16)
    res = tools.search_applications(db_session, univ.id, min_average=14)
    assert [a["student_name"] for a in res["applications"]] == ["High"]


def test_search_status_and_score_filter(db_session, univ):
    _add_app(db_session, univ.id, name="Strong", score=0.95)
    _add_app(db_session, univ.id, name="Weak", score=0.40)
    res = tools.search_applications(db_session, univ.id, min_score=0.85)
    assert [a["student_name"] for a in res["applications"]] == ["Strong"]


def test_search_program_filter(db_session, univ):
    _add_app(db_session, univ.id, name="Info", program="Licence Informatique")
    _add_app(db_session, univ.id, name="Droit", program="Licence Droit")
    res = tools.search_applications(db_session, univ.id, program_name="Droit")
    assert [a["student_name"] for a in res["applications"]] == ["Droit"]


def test_search_tenant_isolation(db_session, univ):
    _add_app(db_session, univ.id, name="Mine")
    other_id = uuid.uuid4()
    _add_app(db_session, other_id, name="Theirs")
    res = tools.search_applications(db_session, univ.id)
    assert [a["student_name"] for a in res["applications"]] == ["Mine"]


def test_search_limit_capped(db_session, univ):
    for i in range(5):
        _add_app(db_session, univ.id, name=f"S{i}")
    res = tools.search_applications(db_session, univ.id, limit=2)
    assert res["count"] == 2


# ----------------------------------------------------------------------
# get_application_detail
# ----------------------------------------------------------------------
def test_detail_returns_documents(db_session, univ):
    app = _add_app(db_session, univ.id, name="Kofi")
    db_session.add(Document(
        id=uuid.uuid4(), application_id=app.id,
        document_type=DocumentType.DIPLOME, gcs_path="gs://x", is_valid=True,
    ))
    db_session.commit()
    res = tools.get_application_detail(db_session, univ.id, application_id=str(app.id))
    assert res["student_name"] == "Kofi"
    assert res["documents"][0]["type"] == "DIPLOME"


def test_detail_not_found(db_session, univ):
    res = tools.get_application_detail(db_session, univ.id, application_id=str(uuid.uuid4()))
    assert "error" in res


def test_detail_tenant_isolation(db_session, univ):
    other_id = uuid.uuid4()
    app = _add_app(db_session, other_id, name="Theirs")
    res = tools.get_application_detail(db_session, univ.id, application_id=str(app.id))
    assert "error" in res


# ----------------------------------------------------------------------
# get_stats
# ----------------------------------------------------------------------
def test_stats_by_status(db_session, univ):
    _add_app(db_session, univ.id, name="A", status=ApplicationStatus.VALIDATED)
    _add_app(db_session, univ.id, name="B", status=ApplicationStatus.VALIDATED)
    _add_app(db_session, univ.id, name="C", status=ApplicationStatus.REJECTED)
    res = tools.get_stats(db_session, univ.id, group_by="status")
    assert res["total"] == 3
    assert res["by_status"]["VALIDATED"] == 2
    assert res["by_status"]["REJECTED"] == 1


def test_stats_by_program(db_session, univ):
    _add_app(db_session, univ.id, name="A", program="Licence Informatique")
    _add_app(db_session, univ.id, name="B", program="Licence Droit")
    res = tools.get_stats(db_session, univ.id, group_by="program")
    assert res["by_program"]["Licence Informatique"] == 1
    assert res["by_program"]["Licence Droit"] == 1


# ----------------------------------------------------------------------
# get_program_criteria / set_program_criteria
# ----------------------------------------------------------------------
@pytest.fixture()
def program(db_session, univ) -> Program:
    p = Program(id=uuid.uuid4(), university_id=univ.id, name="Master Finance", is_active=True)
    db_session.add(p)
    db_session.commit()
    return p


def test_get_criteria_none(db_session, univ, program):
    res = tools.get_program_criteria(db_session, univ.id, program_name="Master Finance")
    assert res["criteria"] is None


def test_set_criteria_dry_run_does_not_persist(db_session, univ, program):
    res = tools.set_program_criteria(
        db_session, univ.id, program_name="Master Finance",
        min_average=14, prerequisites=["Bac+3"], dry_run=True,
    )
    assert res["dry_run"] is True
    got = tools.get_program_criteria(db_session, univ.id, program_name="Master Finance")
    assert got["criteria"] is None


def test_set_criteria_persists(db_session, univ, program):
    tools.set_program_criteria(
        db_session, univ.id, program_name="Master Finance",
        min_average=14, prerequisites=["Bac+3"], accepted_specialties=["Économie"],
    )
    got = tools.get_program_criteria(db_session, univ.id, program_name="Master Finance")
    assert got["criteria"]["min_average"] == 14
    assert got["criteria"]["prerequisites"] == ["Bac+3"]


def test_set_criteria_program_not_found(db_session, univ):
    res = tools.set_program_criteria(db_session, univ.id, program_name="Inexistant")
    assert "error" in res


# ----------------------------------------------------------------------
# bulk_decide
# ----------------------------------------------------------------------
def test_bulk_decide_dry_run_counts(db_session, univ):
    _add_app(db_session, univ.id, name="A", average=16, status=ApplicationStatus.VALIDATED)
    _add_app(db_session, univ.id, name="B", average=15, status=ApplicationStatus.VALIDATED)
    _add_app(db_session, univ.id, name="C", average=10, status=ApplicationStatus.REJECTED)
    res = tools.bulk_decide(
        db_session, univ.id, decision="ACCEPTED", min_average=14, dry_run=True
    )
    assert res["dry_run"] is True
    assert res["count"] == 2  # C est déjà décidée → exclue


def test_bulk_decide_applies(db_session, univ, monkeypatch):
    a = _add_app(db_session, univ.id, name="A", average=16, status=ApplicationStatus.VALIDATED)
    notify = MagicMock()
    dispatch = MagicMock()
    monkeypatch.setattr("app.workers.webhook_tasks.notify_student_decision_task", notify)
    monkeypatch.setattr("app.workers.webhook_tasks.dispatch_decision_acknowledged_task", dispatch)

    res = tools.bulk_decide(db_session, univ.id, decision="ACCEPTED", min_average=14)
    assert res["decided"] == 1
    db_session.refresh(a)
    assert a.status == ApplicationStatus.ACCEPTED
    notify.delay.assert_called_once_with(str(a.id))


def test_bulk_decide_invalid_decision(db_session, univ):
    res = tools.bulk_decide(db_session, univ.id, decision="MAYBE")
    assert "error" in res


# ----------------------------------------------------------------------
# send_whatsapp_notification
# ----------------------------------------------------------------------
def test_notification_dry_run(db_session, univ):
    _add_app(db_session, univ.id, name="A")
    _add_app(db_session, univ.id, name="B")
    res = tools.send_whatsapp_notification(
        db_session, univ.id, message_template="Bonjour {student_name}", dry_run=True
    )
    assert res["count"] == 2


def test_notification_sends_with_substitution(db_session, univ, monkeypatch):
    _add_app(db_session, univ.id, name="Kofi", program="Licence Informatique")
    sent_msgs = []
    monkeypatch.setattr(
        "app.services.whatsapp_bot.send_whatsapp",
        lambda phone, msg: sent_msgs.append(msg),
    )
    res = tools.send_whatsapp_notification(
        db_session, univ.id, message_template="Bonjour {student_name}, programme {program_name}"
    )
    assert res["sent"] == 1
    assert "Kofi" in sent_msgs[0]
    assert "Licence Informatique" in sent_msgs[0]
