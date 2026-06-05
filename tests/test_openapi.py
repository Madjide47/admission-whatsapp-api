"""Tests OpenAPI / Swagger — périmètre Dev 3."""
from fastapi.testclient import TestClient


def test_openapi_has_security_schemes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert "ApiKeyAuth" in schemes
    assert "ApiSecretAuth" in schemes
    assert schemes["ApiKeyAuth"]["name"] == "X-API-Key"
    assert schemes["ApiSecretAuth"]["name"] == "X-API-Secret"


def test_protected_routes_require_both_headers(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    apps_get = schema["paths"]["/api/v1/applications"]["get"]
    assert apps_get["security"] == [{"ApiKeyAuth": [], "ApiSecretAuth": []}]


def test_health_is_public(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    health = schema["paths"]["/health"]["get"]
    assert "security" not in health


def test_swagger_ui_available(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text
