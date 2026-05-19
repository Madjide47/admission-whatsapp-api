"""Fixtures pytest partagées pour les tests.

Stratégie :
 - DB SQLite en mémoire pour les tests unitaires (rapide, isolé).
 - Override de la dependency get_db pour utiliser la session de test.
 - Tous les appels externes (Twilio, Anthropic, GCS) sont monkey-patchés.
"""
import os
import uuid
from collections.abc import Generator

# Variables d'env minimales — chargées AVANT l'import de app.config
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-test-secret-key-test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "testtoken")
os.environ.setdefault("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("GCS_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GCS_PROJECT_ID", "test-project")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.api_key import (
    api_key_prefix,
    generate_api_key,
    generate_api_secret,
    generate_webhook_secret,
    hash_credential,
)
from app.database import Base, get_db
from app.main import app
from app.models.university import University

# DB SQLite — un fichier en mémoire partagé par les sessions du test
TEST_DB_URL = "sqlite+pysqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
)
TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="session", autouse=True)
def setup_db() -> Generator[None, None, None]:
    """Crée les tables une fois pour toute la session de test."""
    Base.metadata.create_all(test_engine)
    yield
    Base.metadata.drop_all(test_engine)


@pytest.fixture()
def db_session() -> Generator:
    """Session DB par test, avec rollback final pour isolation."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSession(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session) -> Generator[TestClient, None, None]:
    """Client HTTP FastAPI avec override de la dependency get_db."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def test_university(db_session) -> tuple[University, str, str]:
    """Crée une université de test et retourne (modèle, api_key_clair, api_secret_clair)."""
    api_key = generate_api_key()
    api_secret = generate_api_secret()
    webhook_secret = generate_webhook_secret()
    university = University(
        id=uuid.uuid4(),
        name="Université Test",
        email=f"test-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash=hash_credential(api_key),
        api_secret_hash=hash_credential(api_secret),
        api_key_prefix=api_key_prefix(api_key),
        webhook_url="https://example.test/webhook",
        webhook_secret=webhook_secret,
        is_active=True,
    )
    db_session.add(university)
    db_session.commit()
    db_session.refresh(university)
    return university, api_key, api_secret


@pytest.fixture()
def auth_headers(test_university) -> dict[str, str]:
    """Headers d'auth pour les requêtes API."""
    _, key, secret = test_university
    return {"X-API-Key": key, "X-API-Secret": secret}
