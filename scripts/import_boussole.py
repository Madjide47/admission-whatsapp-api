"""Import des universités et programmes depuis un fichier JSON Boussole.in.

Ce script est idempotent : relancer l'import ne crée pas de doublons.
Il insère (ou ignore) les universités et leurs programmes dans la base.

Aucun formulaire n'est créé automatiquement ici — l'admin le configure
ensuite via POST/PUT /api/v1/admin/forms/{program_id}.

Usage :
    python scripts/import_boussole.py --file data/boussole_export.json
    python scripts/import_boussole.py --file data/boussole_export.json --dry-run

Format JSON attendu :
    {
      "universities": [
        {
          "name": "Université de Lomé",
          "email": "contact@ul.tg",
          "programs": [
            {
              "name": "Licence Informatique",
              "description": "Formation de 3 ans en informatique."
            }
          ]
        }
      ]
    }
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.models  # noqa: F401 — enregistre tous les modèles dans Base.metadata
from app.auth.api_key import (
    api_key_prefix,
    generate_api_key,
    generate_api_secret,
    generate_webhook_secret,
    hash_credential,
)
from app.database import Base, engine, get_db_session
from app.models.program import AdmissionForm, Program
from app.models.university import University
from sqlalchemy import select


def _ensure_tables() -> None:
    Base.metadata.create_all(engine)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importe Boussole.in dans la base de données.")
    parser.add_argument("--file", required=True, help="Chemin vers le fichier JSON Boussole.in")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule l'import sans écrire en base (rollback final)",
    )
    return parser.parse_args()


def _load_json(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        print(f"[ERREUR] Fichier introuvable : {path}", file=sys.stderr)
        sys.exit(1)
    with open(file, encoding="utf-8") as fh:
        return json.load(fh)


def run_import(data: dict, dry_run: bool = False) -> None:
    _ensure_tables()
    db = get_db_session()

    universities_entry = data.get("universities", [])
    if not isinstance(universities_entry, list):
        print("[ERREUR] La clé 'universities' doit être une liste.", file=sys.stderr)
        sys.exit(1)

    created_universities = 0
    skipped_universities = 0
    created_programs = 0
    skipped_programs = 0

    try:
        for univ_data in universities_entry:
            name = univ_data.get("name", "").strip()
            email = univ_data.get("email", "").strip().lower()
            programs_data = univ_data.get("programs", [])

            if not name or not email:
                print(f"[AVERTISSEMENT] Université ignorée (name/email manquant) : {univ_data}")
                continue

            # Idempotence : vérifier si l'université existe déjà
            existing_univ = db.execute(
                select(University).where(University.email == email)
            ).scalar_one_or_none()

            if existing_univ is not None:
                print(f"[SKIP] Université déjà existante : {name} <{email}>")
                skipped_universities += 1
                univ = existing_univ
            else:
                raw_key = generate_api_key()
                raw_secret = generate_api_secret()
                univ = University(
                    id=uuid.uuid4(),
                    name=name,
                    email=email,
                    api_key_hash=hash_credential(raw_key),
                    api_secret_hash=hash_credential(raw_secret),
                    api_key_prefix=api_key_prefix(raw_key),
                    webhook_secret=generate_webhook_secret(),
                    is_active=True,
                )
                db.add(univ)
                db.flush()
                print(f"[CREE] Université : {name} <{email}>")
                print(f"       API Key    : {raw_key}")
                print(f"       API Secret : {raw_secret}")
                print(f"       (Conservez ces credentials — ils ne seront plus affichés)")
                created_universities += 1

            # Programmes
            for prog_data in programs_data:
                prog_name = prog_data.get("name", "").strip()
                prog_desc = prog_data.get("description", None)

                if not prog_name:
                    print(f"  [AVERTISSEMENT] Programme ignoré (name manquant) pour {name}")
                    continue

                existing_prog = db.execute(
                    select(Program)
                    .where(Program.university_id == univ.id)
                    .where(Program.name == prog_name)
                ).scalar_one_or_none()

                if existing_prog is not None:
                    print(f"  [SKIP] Programme déjà existant : {prog_name}")
                    skipped_programs += 1
                else:
                    prog = Program(
                        university_id=univ.id,
                        name=prog_name,
                        description=prog_desc,
                        is_active=True,
                    )
                    db.add(prog)
                    db.flush()
                    # Crée un formulaire vide (non publié) automatiquement
                    db.add(AdmissionForm(program_id=prog.id, is_published=False))
                    print(f"  [CREE] Programme : {prog_name}")
                    created_programs += 1

        if dry_run:
            db.rollback()
            print("\n[DRY-RUN] Aucun changement écrit en base.")
        else:
            db.commit()
            print("\n[OK] Import termine.")

        print(
            f"\nResume :\n"
            f"  Universites crees  : {created_universities}\n"
            f"  Universites skip   : {skipped_universities}\n"
            f"  Programmes crees   : {created_programs}\n"
            f"  Programmes skip    : {skipped_programs}"
        )

    except Exception as exc:
        db.rollback()
        print(f"[ERREUR] Import echoue : {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    args = _parse_args()
    data = _load_json(args.file)
    run_import(data, dry_run=args.dry_run)
