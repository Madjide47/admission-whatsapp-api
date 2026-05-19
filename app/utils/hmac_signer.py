"""Signature HMAC-SHA256 pour les webhooks sortants.

Le payload est sérialisé en JSON canonique (clés triées, séparateurs compacts)
pour garantir qu'une même structure produit toujours la même signature.
"""
import hashlib
import hmac
import json
import time
from typing import Any


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Sérialise un dict en JSON canonique (déterministe).

    - clés triées
    - séparateurs sans espaces
    - encodage UTF-8
    - ensure_ascii=False pour conserver les accents (cohérent côté client)
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_payload(secret: str, payload: dict[str, Any], timestamp: int | None = None) -> tuple[str, int, bytes]:
    """Signe un payload avec HMAC-SHA256.

    Retourne (signature_hex, timestamp, body_bytes).

    Le timestamp est inclus dans la chaîne signée :
        message = "<timestamp>.<body_json>"

    Cela permet au receveur de rejeter les requêtes trop anciennes
    (protection contre le replay).
    """
    if timestamp is None:
        timestamp = int(time.time())

    body = canonical_json(payload)
    message = f"{timestamp}.".encode("utf-8") + body

    digest = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    return digest, timestamp, body


def verify_signature(
    secret: str,
    body: bytes,
    timestamp: int,
    signature: str,
    tolerance_seconds: int = 300,
) -> bool:
    """Vérifie la signature d'un webhook reçu.

    - Compare le timestamp à l'heure actuelle (fenêtre tolerance_seconds)
    - Recalcule le HMAC et compare en temps constant
    """
    now = int(time.time())
    if abs(now - timestamp) > tolerance_seconds:
        return False

    message = f"{timestamp}.".encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, signature)
