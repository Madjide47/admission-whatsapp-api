"""Initialise la base SQLite de développement depuis les modèles SQLAlchemy.

La migration Alembic officielle (0001) contient du SQL PostgreSQL pur
(CREATE TYPE ... AS ENUM) qui ne tourne pas sur SQLite.
Ce script contourne ça en appelant directement Base.metadata.create_all(),
qui gère les différences PostgreSQL/SQLite automatiquement.

Usage :
    python scripts/init_db_sqlite.py
"""
import sys
from pathlib import Path

# Ajouter la racine du projet au sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, text

# Importer tous les modèles pour que Base les connaisse
import app.models  # noqa: F401
from app.database import Base, engine


def init():
    print(f"Base de données : {engine.url}")

    with engine.connect() as conn:
        tables_before = inspect(engine).get_table_names()
        if tables_before and "universities" in tables_before:
            print(f"  Tables déjà existantes : {tables_before}")
            print("  Rien à faire.")
            return

    # Créer toutes les tables
    Base.metadata.create_all(engine)

    # Marquer Alembic comme "à jour" pour éviter qu'il essaie de relancer la migration PG
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
        existing = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        if not existing:
            conn.execute(text("INSERT INTO alembic_version VALUES ('0001')"))
        conn.commit()

    tables = inspect(engine).get_table_names()
    print(f"  Tables créées : {tables}")
    print("  Base SQLite initialisée avec succes.")


if __name__ == "__main__":
    init()
