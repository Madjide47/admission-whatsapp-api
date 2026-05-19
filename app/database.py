"""Configuration de la connexion SQLAlchemy 2.0 (mode synchrone).

On utilise le mode synchrone pour que la même couche d'accès aux données
puisse être réutilisée par FastAPI (via run_in_threadpool) et par les
workers Celery (qui sont nativement synchrones).
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Moteur SQLAlchemy avec pool de connexions
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,  # Détecte les connexions mortes avant utilisation
    echo=settings.DB_ECHO,
    future=True,
)

# Factory de sessions — autoflush désactivé pour garder le contrôle explicite
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Classe de base pour tous les modèles SQLAlchemy 2.0."""


def get_db() -> Generator[Session, None, None]:
    """Dependency FastAPI : fournit une session DB par requête.

    Garantit que la session est fermée même en cas d'exception.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Crée une session DB pour usage en dehors d'une requête HTTP
    (ex : tâches Celery, scripts)."""
    return SessionLocal()
