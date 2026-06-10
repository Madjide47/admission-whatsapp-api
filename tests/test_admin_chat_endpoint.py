"""Test du endpoint POST /api/v1/admin/chat (orchestrateur LLM mocké)."""
import uuid

import pytest

from app.api.v1 import admin_chat as endpoint
from app.models.application import Application, ApplicationStatus
from app.services import admin_chat
from app.services.admin_chat import AdminChatOrchestrator, LLMClient, LLMResult


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(admin_chat, "_redis_client", lambda: None)
    admin_chat._MEMORY_SESSIONS.clear()


class ScriptedLLM(LLMClient):
    def __init__(self, results):
        self.results = list(results)

    def run(self, system_prompt, history, tool_specs):
        return self.results.pop(0)


def test_chat_endpoint_returns_message_and_table(client, auth_headers, db_session, test_university, monkeypatch):
    university, _, _ = test_university
    db_session.add(Application(
        id=uuid.uuid4(), university_id=university.id, student_phone="+228111",
        student_name="Ada", program="Licence Informatique",
        status=ApplicationStatus.VALIDATED, validation_score=0.9, average=17.0,
    ))
    db_session.commit()

    llm = ScriptedLLM([
        LLMResult(tool_calls=[{"name": "search_applications", "args": {}}]),
        LLMResult(text="Voici le candidat."),
    ])
    monkeypatch.setattr(endpoint, "_orchestrator", AdminChatOrchestrator(llm=llm))

    r = client.post(
        "/api/v1/admin/chat",
        headers=auth_headers,
        json={"message": "Liste les candidats"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["message"] == "Voici le candidat."
    assert data["tool_used"] == "search_applications"
    assert data["data_table"][0]["student_name"] == "Ada"
    assert data["session_id"]


def test_chat_endpoint_requires_auth(client):
    r = client.post("/api/v1/admin/chat", json={"message": "Salut"})
    assert r.status_code in (401, 403)
