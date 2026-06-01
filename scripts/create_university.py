"""Script de création d'une université cliente — à exécuter une fois pour la démo.

Usage (dans le conteneur API ou localement avec .env chargé) :
    python scripts/create_university.py --name "Université de Lomé" --email "admin@univ-lome.tg"

Retourne en console l'API Key et l'API Secret en clair (affichés UNE SEULE FOIS).
"""
import argparse
import sys
import uuid

def main():
    parser = argparse.ArgumentParser(description="Créer une université cliente dans la DB")
    parser.add_argument("--name", required=True, help="Nom de l'université")
    parser.add_argument("--email", required=True, help="Email de contact")
    parser.add_argument(
        "--webhook-url",
        default=None,
        help="URL webhook de l'université (peut être configuré plus tard)",
    )
    args = parser.parse_args()

    # Import après chargement de l'env
    from app.auth.api_key import (
        generate_api_key,
        generate_api_secret,
        generate_webhook_secret,
        hash_credential,
        api_key_prefix,
    )
    from app.database import SessionLocal
    from app.models.university import University

    api_key = generate_api_key()
    api_secret = generate_api_secret()
    webhook_secret = generate_webhook_secret()

    university = University(
        id=uuid.uuid4(),
        name=args.name,
        email=args.email,
        api_key_hash=hash_credential(api_key),
        api_secret_hash=hash_credential(api_secret),
        api_key_prefix=api_key_prefix(api_key),
        webhook_url=args.webhook_url,
        webhook_secret=webhook_secret,
        is_active=True,
    )

    db = SessionLocal()
    try:
        db.add(university)
        db.commit()
        db.refresh(university)
    except Exception as e:
        db.rollback()
        print(f"Erreur : {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    print("\n" + "=" * 60)
    print(f"Université créée : {university.name}")
    print(f"ID               : {university.id}")
    print("=" * 60)
    print(f"API Key    : {api_key}")
    print(f"API Secret : {api_secret}")
    print(f"Webhook Secret : {webhook_secret}")
    print("=" * 60)
    print("ATTENTION : Ces valeurs ne seront plus affichées. Conservez-les.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
