"""Authentification session pour l'interface admin.

Utilise un cookie signé (itsdangerous) — pas de JWT, pas de base de données.
Les credentials sont définis dans les variables d'environnement.
"""
import hashlib
import hmac

from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_COOKIE_NAME = "admin_session"
_MAX_AGE = 8 * 3600  # 8 heures


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="admin-session")


def create_session_cookie(username: str) -> str:
    return _serializer().dumps({"u": username})


def verify_session_cookie(token: str) -> str | None:
    """Retourne le username si le cookie est valide, None sinon."""
    try:
        data = _serializer().loads(token, max_age=_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


def check_credentials(username: str, password: str) -> bool:
    """Compare de manière sécurisée (timing-safe) les credentials."""
    expected_user = getattr(settings, "ADMIN_USERNAME", "admin")
    expected_pass = getattr(settings, "ADMIN_PASSWORD", "")
    ok_user = hmac.compare_digest(username.encode(), expected_user.encode())
    ok_pass = hmac.compare_digest(
        hashlib.sha256(password.encode()).digest(),
        hashlib.sha256(expected_pass.encode()).digest(),
    )
    return ok_user and ok_pass


def get_admin_user(request: Request) -> str:
    """Dependency FastAPI — retourne le username ou redirige vers /admin/login."""
    token = request.cookies.get(_COOKIE_NAME, "")
    username = verify_session_cookie(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )
    return username


COOKIE_NAME = _COOKIE_NAME
