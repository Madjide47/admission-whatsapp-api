"""Authentification par API Key + API Secret pour les universités clientes.

Format :
  - API Key    : univ_<32 caractères alphanumériques>      → identifiant public
  - API Secret : sk_<64 caractères alphanumériques>        → ne sort qu'une fois

Les deux sont hashés en bcrypt (cost 12) avant stockage.
À chaque requête, le préfixe (12 caractères) permet une recherche indexée,
puis bcrypt vérifie l'égalité en temps constant.
"""
import logging
import secrets
import string

from fastapi import Depends, Header, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.university import University

logger = logging.getLogger(__name__)

# bcrypt cost 12 — bon équilibre sécurité / performance
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

ALPHABET = string.ascii_letters + string.digits


# ----------------------------------------------------------------------
# Génération
# ----------------------------------------------------------------------
def generate_api_key() -> str:
    """Génère une API Key : univ_<32 caractères>."""
    body = "".join(secrets.choice(ALPHABET) for _ in range(32))
    return f"univ_{body}"


def generate_api_secret() -> str:
    """Génère une API Secret : sk_<64 caractères>."""
    body = "".join(secrets.choice(ALPHABET) for _ in range(64))
    return f"sk_{body}"


def generate_webhook_secret() -> str:
    """Génère un secret HMAC pour signer les webhooks (32 octets aléatoires)."""
    return secrets.token_urlsafe(32)


# ----------------------------------------------------------------------
# Hashage
# ----------------------------------------------------------------------
def hash_credential(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_credential(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def api_key_prefix(api_key: str) -> str:
    """Préfixe stocké en clair pour recherche rapide (12 premiers caractères)."""
    return api_key[:12]


# ----------------------------------------------------------------------
# Dependency FastAPI
# ----------------------------------------------------------------------
async def get_current_university(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_api_secret: str | None = Header(default=None, alias="X-API-Secret"),
    db: Session = Depends(get_db),
) -> University:
    """Authentifie une université via headers X-API-Key et X-API-Secret."""
    if not x_api_key or not x_api_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": "AUTH_MISSING_CREDENTIALS",
                    "message": "Headers X-API-Key et X-API-Secret requis.",
                },
            },
            headers={"WWW-Authenticate": "ApiKey"},
        )

    prefix = api_key_prefix(x_api_key)
    stmt = (
        select(University)
        .where(University.api_key_prefix == prefix)
        .where(University.is_active.is_(True))
    )
    candidates = db.execute(stmt).scalars().all()

    # On vérifie bcrypt sur les candidats (généralement 1 seul)
    for university in candidates:
        if verify_credential(x_api_key, university.api_key_hash) and verify_credential(
            x_api_secret, university.api_secret_hash
        ):
            return university

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "success": False,
            "error": {
                "code": "AUTH_INVALID_CREDENTIALS",
                "message": "Clé API ou secret invalide.",
            },
        },
        headers={"WWW-Authenticate": "ApiKey"},
    )
