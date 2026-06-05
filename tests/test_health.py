"""Tests dédiés au endpoint /health et à l'authentification de base.

Complète test_applications.py qui contient déjà test_health() minimal.
Ces tests vérifient les chemins d'erreur d'authentification.
"""


def test_health_returns_ok_and_version(client):
    """Le health check retourne status=ok et un champ version."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_no_auth_headers_returns_401(client):
    """/api/v1/applications sans headers d'auth retourne 401."""
    r = client.get("/api/v1/applications")
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_wrong_credentials_return_401(client):
    """Des credentials incorrects retournent 401 AUTH_INVALID_CREDENTIALS."""
    r = client.get(
        "/api/v1/applications",
        headers={"X-API-Key": "univ_wrongkey123", "X-API-Secret": "sk_wrongsecret"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_valid_auth_reaches_endpoint(client, auth_headers):
    """Des credentials valides permettent d'accéder à l'endpoint."""
    r = client.get("/api/v1/applications", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["success"] is True
