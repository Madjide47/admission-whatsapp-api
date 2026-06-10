"""Tests d'intégration bout-en-bout du module Admin Chatbot (Phase 6).

Couvre la chaîne inter-composants (outil critères → bot WhatsApp) et la
robustesse de l'orchestrateur face à un outil inconnu.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.program import Program
from app.models.university import University
from app.services import admin_chat, admin_chat_tools as tools
from app.services.admin_chat import AdminChatOrchestrator, LLMClient, LLMResult
from app.services.whatsapp_bot import ConversationState, WhatsAppBot


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(admin_chat, "_redis_client", lambda: None)
    admin_chat._MEMORY_SESSIONS.clear()


@pytest.fixture()
def university(db_session) -> University:
    u = University(
        id=uuid.uuid4(), name="Univ E2E",
        email=f"e2e-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash=f"h_{uuid.uuid4().hex}", api_secret_hash="x",
        api_key_prefix=f"k_{uuid.uuid4().hex[:6]}", is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


class ScriptedLLM(LLMClient):
    def __init__(self, results):
        self.results = list(results)

    def run(self, system_prompt, history, tool_specs):
        return self.results.pop(0)


def test_criteria_set_by_tool_shown_on_whatsapp(db_session, university):
    """Critères définis via l'outil admin → affichés par le bot au choix du programme."""
    program = Program(
        id=uuid.uuid4(), university_id=university.id, name="Master Finance", is_active=True
    )
    db_session.add(program)
    db_session.commit()

    # 1) Outil admin : configure les critères
    res = tools.set_program_criteria(
        db_session, university.id, program_name="Master Finance",
        min_average=14, prerequisites=["Bac+3"], accepted_specialties=["Économie"],
    )
    assert res.get("updated") is True

    # 2) Bot WhatsApp : l'étudiant choisit ce programme → prérequis affichés
    with patch("app.services.whatsapp_bot.TwilioClient") as cls:
        cls.return_value = MagicMock()
        bot = WhatsAppBot(db_session)
        bot.send_message = MagicMock()

        app = Application(
            id=uuid.uuid4(), university_id=university.id, student_phone="+22890000001",
            student_name="Ada", program="Master Finance", program_id=program.id,
            status=ApplicationStatus.CHOOSING_PROGRAM,
            conversation_state=ConversationState.CHOOSE_PROGRAM.value,
        )
        db_session.add(app)
        db_session.commit()

        bot._maybe_show_prerequisites(app, "✅ Programme sélectionné !")

    assert app.conversation_state == ConversationState.CONFIRM_PREREQUISITES.value
    msg = bot.send_message.call_args[0][1]
    assert "≥ 14/20" in msg
    assert "Bac+3" in msg


def test_orchestrator_handles_unknown_tool(db_session, university):
    """Un nom d'outil inconnu renvoyé par le LLM n'interrompt pas la conversation."""
    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "drop_table", "args": {}}]),
        LLMResult(text="Je ne peux pas faire cela."),
    ])
    orch = AdminChatOrchestrator(llm=llm)
    res = orch.process(db_session, university, "Supprime tout")
    assert res.message == "Je ne peux pas faire cela."


def test_orchestrator_injects_tenant_on_every_call(db_session, university, monkeypatch):
    """university_id est toujours injecté : un outil ne reçoit jamais d'autre tenant."""
    captured = {}

    def fake_search(db, university_id, **kw):
        captured["university_id"] = university_id
        return {"count": 0, "applications": []}

    monkeypatch.setitem(admin_chat.TOOL_SPECS["search_applications"], "func", fake_search)
    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "search_applications", "args": {"university_id": "HACKED"}}]),
        LLMResult(text="ok"),
    ])
    orch = AdminChatOrchestrator(llm=llm)
    orch.process(db_session, university, "liste")
    # Même si le LLM tente de passer un university_id, il est filtré (hors allowlist)
    # et c'est bien celui de l'université authentifiée qui est utilisé.
    assert captured["university_id"] == university.id
