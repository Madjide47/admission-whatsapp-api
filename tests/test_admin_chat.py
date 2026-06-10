"""Tests de l'orchestrateur du chatbot admin (LLM scripté, sans appel Gemini)."""
import uuid
from unittest.mock import MagicMock

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.program import Program
from app.models.university import University
from app.services import admin_chat
from app.services.admin_chat import AdminChatOrchestrator, LLMClient, LLMResult
from app.services.admin_chat_gemini import _parse


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Force le repli mémoire pour des sessions déterministes en test."""
    monkeypatch.setattr(admin_chat, "_redis_client", lambda: None)
    admin_chat._MEMORY_SESSIONS.clear()


class ScriptedLLM(LLMClient):
    """LLM factice : renvoie des LLMResult prédéfinis, dans l'ordre."""

    def __init__(self, results):
        self.results = list(results)
        self.seen_history = []

    def run(self, system_prompt, history, tool_specs):
        self.seen_history.append(list(history))
        return self.results.pop(0)


@pytest.fixture()
def univ(db_session) -> University:
    u = University(
        id=uuid.uuid4(),
        name="Univ Chat",
        email=f"chat-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash=f"h_{uuid.uuid4().hex}",
        api_secret_hash="x",
        api_key_prefix=f"k_{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db_session.add(u)
    db_session.add(Program(id=uuid.uuid4(), university_id=u.id, name="Licence Informatique", is_active=True))
    db_session.commit()
    return u


def _add_app(db, univ_id, name, average=15.0, status=ApplicationStatus.VALIDATED):
    a = Application(
        id=uuid.uuid4(),
        university_id=univ_id,
        student_phone=f"+228{uuid.uuid4().hex[:8]}",
        student_name=name,
        program="Licence Informatique",
        status=status,
        validation_score=0.9,
        average=average,
    )
    db.add(a)
    db.commit()
    return a


# ----------------------------------------------------------------------
# Flux lecture
# ----------------------------------------------------------------------
def test_read_flow_returns_table_and_message(db_session, univ):
    _add_app(db_session, univ.id, "Top", average=18)
    _add_app(db_session, univ.id, "Mid", average=15)

    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "search_applications", "args": {"min_average": 14}}]),
        LLMResult(text="Voici les 2 meilleurs candidats."),
    ])
    orch = AdminChatOrchestrator(llm=llm)
    res = orch.process(db_session, univ, "Montre les candidats avec moyenne >= 14")

    assert res.message == "Voici les 2 meilleurs candidats."
    assert res.tool_used == "search_applications"
    assert res.data_table is not None
    assert [r["student_name"] for r in res.data_table] == ["Top", "Mid"]


# ----------------------------------------------------------------------
# Flux écriture : aperçu + confirmation
# ----------------------------------------------------------------------
def test_write_flow_asks_confirmation_before_executing(db_session, univ):
    app = _add_app(db_session, univ.id, "A", average=16, status=ApplicationStatus.VALIDATED)

    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "bulk_decide", "args": {"decision": "ACCEPTED", "min_average": 14}}]),
    ])
    orch = AdminChatOrchestrator(llm=llm)
    res = orch.process(db_session, univ, "Accepte les candidats au-dessus de 14")

    assert res.pending_action is not None
    assert res.pending_action["type"] == "bulk_decide"
    assert res.pending_action["count"] == 1
    # PAS encore exécuté
    db_session.refresh(app)
    assert app.status == ApplicationStatus.VALIDATED


def test_write_flow_executes_after_confirmation(db_session, univ, monkeypatch):
    app = _add_app(db_session, univ.id, "A", average=16, status=ApplicationStatus.VALIDATED)
    monkeypatch.setattr("app.workers.webhook_tasks.notify_student_decision_task", MagicMock())
    monkeypatch.setattr("app.workers.webhook_tasks.dispatch_decision_acknowledged_task", MagicMock())

    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "bulk_decide", "args": {"decision": "ACCEPTED", "min_average": 14}}]),
    ])
    orch = AdminChatOrchestrator(llm=llm)
    first = orch.process(db_session, univ, "Accepte les candidats au-dessus de 14")

    # Confirmation → exécution réelle (pas besoin de re-solliciter le LLM)
    second = orch.process(
        db_session, univ, "oui", session_id=first.session_id, confirm_action=True
    )
    assert "1" in second.message
    db_session.refresh(app)
    assert app.status == ApplicationStatus.ACCEPTED


# ----------------------------------------------------------------------
# Isolation multi-tenant des sessions
# ----------------------------------------------------------------------
def test_session_not_reused_across_universities(db_session, univ):
    other = University(
        id=uuid.uuid4(), name="Autre", email=f"o-{uuid.uuid4().hex[:6]}@u.test",
        api_key_hash=f"h_{uuid.uuid4().hex}", api_secret_hash="x",
        api_key_prefix=f"k_{uuid.uuid4().hex[:6]}", is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    llm = ScriptedLLM([LLMResult(text="ok"), LLMResult(text="ok2")])
    orch = AdminChatOrchestrator(llm=llm)
    res1 = orch.process(db_session, univ, "salut")
    # L'autre université passe le session_id de la 1re → doit obtenir une NOUVELLE session
    res2 = orch.process(db_session, other, "salut", session_id=res1.session_id)
    assert res2.session_id != res1.session_id


def test_llm_failure_is_graceful(db_session, univ):
    class BoomLLM(LLMClient):
        def run(self, *a, **k):
            raise RuntimeError("API down")

    orch = AdminChatOrchestrator(llm=BoomLLM())
    res = orch.process(db_session, univ, "salut")
    assert "indisponible" in res.message.lower()


# ----------------------------------------------------------------------
# Parsing Gemini (mode JSON)
# ----------------------------------------------------------------------
def test_parse_tool_call():
    r = _parse('{"action": "tool", "tool": "search_applications", "args": {"min_average": 14}}')
    assert r.tool_calls == [{"name": "search_applications", "args": {"min_average": 14}}]


def test_parse_final():
    r = _parse('{"action": "final", "message": "Bonjour"}')
    assert r.text == "Bonjour"
    assert not r.tool_calls


def test_parse_code_fence():
    r = _parse('```json\n{"action": "final", "message": "Salut"}\n```')
    assert r.text == "Salut"


def test_parse_invalid_json_falls_back_to_text():
    r = _parse("pas du json")
    assert r.text == "pas du json"
