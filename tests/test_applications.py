"""Tests des endpoints de candidatures."""
import pytest


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "1.0.0"}


def test_create_application_requires_auth(client):
    r = client.post("/api/v1/applications", json={"student_phone": "+22890123456"})
    assert r.status_code == 401
    body = r.json()
    assert body["detail"]["success"] is False
    assert body["detail"]["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_create_application_invalid_credentials(client):
    r = client.post(
        "/api/v1/applications",
        json={"student_phone": "+22890123456"},
        headers={"X-API-Key": "univ_fake", "X-API-Secret": "sk_fake"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_create_application_success(client, auth_headers):
    payload = {
        "student_phone": "+22890123456",
        "student_name": "Kofi Mensah",
        "student_email": "kofi@example.com",
        "program": "Licence Informatique",
    }
    r = client.post("/api/v1/applications", json=payload, headers=auth_headers)
    assert r.status_code == 201
    data = r.json()
    assert data["success"] is True
    assert data["data"]["student_name"] == "Kofi Mensah"
    assert data["data"]["status"] == "COLLECTING"


def test_list_applications_filters(client, auth_headers):
    # On crée 2 candidatures
    for i in range(2):
        client.post(
            "/api/v1/applications",
            json={
                "student_phone": f"+228900000{i:02d}",
                "student_name": f"Etudiant {i}",
                "program": "Master Gestion" if i == 0 else "Licence Informatique",
            },
            headers=auth_headers,
        )

    r = client.get("/api/v1/applications", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 2

    # Filtre par programme
    r = client.get(
        "/api/v1/applications", headers=auth_headers, params={"program": "Master"}
    )
    items = r.json()["data"]["items"]
    assert all("master" in (i["program"] or "").lower() for i in items)


def test_get_application_not_found(client, auth_headers):
    import uuid as _uuid

    r = client.get(f"/api/v1/applications/{_uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "APPLICATION_NOT_FOUND"


def test_patch_application(client, auth_headers):
    created = client.post(
        "/api/v1/applications",
        json={"student_phone": "+22890123456"},
        headers=auth_headers,
    ).json()["data"]
    app_id = created["id"]

    r = client.patch(
        f"/api/v1/applications/{app_id}",
        json={"student_name": "Nom Mis À Jour", "program": "Doctorat"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["student_name"] == "Nom Mis À Jour"
    assert r.json()["data"]["program"] == "Doctorat"


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_submit_decision(client, auth_headers, decision, monkeypatch):
    # Mock Twilio + Celery (Twilio est déjà inactif via env de test, Celery aussi)
    from app.api.v1 import decisions as decisions_module

    monkeypatch.setattr(decisions_module, "_notify_student_decision", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.workers.webhook_tasks.dispatch_decision_acknowledged_task.delay",
        lambda *a, **k: None,
    )

    created = client.post(
        "/api/v1/applications",
        json={"student_phone": "+22890123456", "student_name": "X", "program": "Y"},
        headers=auth_headers,
    ).json()["data"]
    app_id = created["id"]

    r = client.post(
        f"/api/v1/applications/{app_id}/decision",
        json={"decision": decision, "comment": "Bienvenue !" if decision == "ACCEPTED" else "Désolé."},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == decision
