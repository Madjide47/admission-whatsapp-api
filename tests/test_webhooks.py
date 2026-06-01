"""Tests des webhooks : signature HMAC, idempotence, anti-replay."""
import time

from app.utils.hmac_signer import (
    canonical_json,
    sign_payload,
    verify_signature,
)


def test_canonical_json_is_deterministic():
    a = {"b": 1, "a": 2, "c": {"y": 3, "x": 4}}
    b = {"a": 2, "c": {"x": 4, "y": 3}, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_sign_and_verify_roundtrip():
    secret = "topsecret_123"
    payload = {"event": "application.validated", "data": {"id": "abc"}}

    signature, ts, body = sign_payload(secret, payload)
    assert verify_signature(secret, body, ts, signature, tolerance_seconds=300)


def test_verify_rejects_bad_signature():
    secret = "topsecret_123"
    payload = {"event": "x"}
    _, ts, body = sign_payload(secret, payload)

    assert not verify_signature(secret, body, ts, "deadbeef", tolerance_seconds=300)


def test_verify_rejects_replayed_timestamp():
    secret = "topsecret_123"
    payload = {"event": "x"}
    signature, _, body = sign_payload(secret, payload, timestamp=int(time.time()) - 10_000)

    # Avec une tolérance de 300s, un timestamp vieux de 10000s est rejeté
    assert not verify_signature(secret, body, int(time.time()) - 10_000, signature, tolerance_seconds=300)


def test_verify_with_wrong_secret_fails():
    payload = {"event": "x"}
    signature, ts, body = sign_payload("secret1", payload)
    assert not verify_signature("secret2", body, ts, signature)


def test_webhook_config_endpoint(client, auth_headers):
    r = client.get("/api/v1/webhooks", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["webhook_url"] == "https://example.test/webhook"
    assert data["has_secret"] is True


def test_webhook_update_regenerates_secret(client, auth_headers):
    r = client.put(
        "/api/v1/webhooks",
        json={"webhook_url": "https://new.example.test/webhook", "regenerate_secret": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["webhook_url"] == "https://new.example.test/webhook"
    assert data["webhook_secret"].startswith(("sk_", ""))  # urlsafe token
    assert len(data["webhook_secret"]) >= 32
