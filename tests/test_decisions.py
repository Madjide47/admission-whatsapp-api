"""Tests de l'endpoint POST /api/v1/applications/{id}/decision.

Couvre les cas critiques d'idempotence absents de test_applications.py :
  - Même décision envoyée deux fois → 200 idempotent
  - Décision différente après une première → 409 DECISION_ALREADY_TAKEN
  - Valeur de décision invalide → 422 INVALID_DECISION
"""
import pytest

from app.api.v1 import decisions as decisions_module


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
    monkeypatch.setattr(decisions_module, "_notify_student_decision", lambda *a, **k: None)
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
    monkeypatch.setattr(decisions_module, "_notify_student_decision", lambda *a, **k: None)
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
    monkeypatch.setattr(decisions_module, "_notify_student_decision", lambda *a, **k: None)
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
    monkeypatch.setattr(decisions_module, "_notify_student_decision", lambda *a, **k: None)
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
