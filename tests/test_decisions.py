"""Tests de l'endpoint POST /api/v1/applications/{id}/decision.

Couvre les cas critiques d'idempotence absents de test_applications.py :
  - Même décision envoyée deux fois → 200 idempotent
  - Décision différente après une première → 409 DECISION_ALREADY_TAKEN
  - Valeur de décision invalide → 422 INVALID_DECISION
"""
import uuid
from unittest.mock import MagicMock

import pytest

from app.models.application import Application, ApplicationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_app(client, auth_headers, phone="+22891000000"):
    """Crée une candidature via l'API et retourne son id."""
    r = client.post(
        "/api/v1/applications",
        json={"student_phone": phone, "student_name": "Test", "program": "Master"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    return r.json()["data"]["id"]


def _post_decision(client, auth_headers, app_id, decision, comment=None):
    return client.post(
        f"/api/v1/applications/{app_id}/decision",
        json={"decision": decision, "comment": comment},
        headers=auth_headers,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_first_decision_accepted(client, auth_headers, monkeypatch):
    """Une première décision ACCEPTED est enregistrée et retourne 200."""
    monkeypatch.setattr(
        "app.workers.webhook_tasks.notify_student_decision_task.delay",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task.delay",
        lambda *a, **k: None,
    )

    app_id = _create_app(client, auth_headers)
    r = _post_decision(client, auth_headers, app_id, "ACCEPTED", "Excellent dossier")

    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["data"]["status"] == "ACCEPTED"


def test_first_decision_rejected(client, auth_headers, monkeypatch):
    """Une première décision REJECTED est enregistrée et retourne 200."""
    monkeypatch.setattr(
        "app.workers.webhook_tasks.notify_student_decision_task.delay",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task.delay",
        lambda *a, **k: None,
    )

    app_id = _create_app(client, auth_headers, phone="+22891000001")
    r = _post_decision(client, auth_headers, app_id, "REJECTED", "Dossier incomplet")

    assert r.status_code == 200
    assert r.json()["data"]["status"] == "REJECTED"


def test_same_decision_is_idempotent(client, auth_headers, monkeypatch):
    """Envoyer la même décision deux fois retourne 200 avec idempotent=True."""
    monkeypatch.setattr(
        "app.workers.webhook_tasks.notify_student_decision_task.delay",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task.delay",
        lambda *a, **k: None,
    )

    app_id = _create_app(client, auth_headers, phone="+22891000002")

    # Première décision
    _post_decision(client, auth_headers, app_id, "REJECTED")

    # Même décision — doit être idempotent
    r = _post_decision(client, auth_headers, app_id, "REJECTED")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["data"].get("idempotent") is True


def test_conflicting_decision_returns_409(client, auth_headers, monkeypatch):
    """Changer de décision après coup retourne 409 DECISION_ALREADY_TAKEN."""
    monkeypatch.setattr(
        "app.workers.webhook_tasks.notify_student_decision_task.delay",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task.delay",
        lambda *a, **k: None,
    )

    app_id = _create_app(client, auth_headers, phone="+22891000003")

    # Première décision : ACCEPTED
    _post_decision(client, auth_headers, app_id, "ACCEPTED")

    # Tentative de changement : REJECTED → conflit
    r = _post_decision(client, auth_headers, app_id, "REJECTED")
    assert r.status_code == 409
    error = r.json()["detail"]["error"]
    assert error["code"] == "DECISION_ALREADY_TAKEN"


def test_invalid_decision_returns_422(client, auth_headers):
    """Une valeur de décision non autorisée retourne 422."""
    app_id = _create_app(client, auth_headers, phone="+22891000004")
    r = _post_decision(client, auth_headers, app_id, "MAYBE")

    # Pydantic rejette la valeur hors enum avant le code métier → 422 standard
    assert r.status_code == 422


def test_decision_enqueues_notification_task(client, auth_headers):
    """L'endpoint /decision met bien la notification étudiant en file (asynchrone)."""
    from unittest.mock import patch

    # On remplace tout l'objet tâche dans le module (l'endpoint le réimporte à
    # chaque appel) — plus fiable que patcher .delay sur le proxy shared_task.
    with patch("app.workers.webhook_tasks.notify_student_decision_task") as mock_task, patch(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task"
    ):
        app_id = _create_app(client, auth_headers, phone="+22891000005")
        _post_decision(client, auth_headers, app_id, "ACCEPTED", "Bravo")

    mock_task.delay.assert_called_once_with(app_id)


# ---------------------------------------------------------------------------
# Tâche notify_student_decision_task (retry)
# ---------------------------------------------------------------------------

def _make_decided_app(db_session, test_university, status=ApplicationStatus.ACCEPTED):
    university, _, _ = test_university
    app = Application(
        id=uuid.uuid4(), university_id=university.id, student_phone="+22897000000",
        student_name="Étudiant Notif", program="Master", status=status,
        conversation_state="DONE", decision_comment="Félicitations",
    )
    db_session.add(app)
    db_session.commit()
    return app


def test_notify_task_returns_sent_on_success(db_session, test_university, monkeypatch):
    """Envoi réussi (SID retourné) → la tâche retourne 'sent', aucun retry."""
    from app.workers import webhook_tasks

    app = _make_decided_app(db_session, test_university)
    monkeypatch.setattr(webhook_tasks, "get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    fake_bot = MagicMock()
    fake_bot.notify_decision.return_value = "SM_OK"
    monkeypatch.setattr("app.services.whatsapp_bot.WhatsAppBot", lambda db: fake_bot)

    result = webhook_tasks.notify_student_decision_task(str(app.id))
    assert result == "sent"
    fake_bot.notify_decision.assert_called_once()


def test_notify_task_retries_on_twilio_failure(db_session, test_university, monkeypatch):
    """Échec d'envoi (None) → la tâche déclenche un retry (message non perdu)."""
    from app.workers import webhook_tasks

    app = _make_decided_app(db_session, test_university)
    monkeypatch.setattr(webhook_tasks, "get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    fake_bot = MagicMock()
    fake_bot.notify_decision.return_value = None  # Twilio a échoué (ex: 429)
    monkeypatch.setattr("app.services.whatsapp_bot.WhatsAppBot", lambda db: fake_bot)

    # On capture l'appel à self.retry (qui, en prod, replanifie la tâche).
    class _RetryCalled(Exception):
        pass

    def fake_retry(*a, **k):
        raise _RetryCalled()

    monkeypatch.setattr(webhook_tasks.notify_student_decision_task, "retry", fake_retry)

    with pytest.raises(_RetryCalled):
        webhook_tasks.notify_student_decision_task(str(app.id))


def test_notify_task_skips_undecided_application(db_session, test_university, monkeypatch):
    """Une candidature non décidée n'envoie aucune notification."""
    from app.workers import webhook_tasks

    app = _make_decided_app(
        db_session, test_university, status=ApplicationStatus.COLLECTING_DOCUMENTS
    )
    monkeypatch.setattr(webhook_tasks, "get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    fake_bot = MagicMock()
    monkeypatch.setattr("app.services.whatsapp_bot.WhatsAppBot", lambda db: fake_bot)

    result = webhook_tasks.notify_student_decision_task(str(app.id))
    assert result == "skipped"
    fake_bot.notify_decision.assert_not_called()
