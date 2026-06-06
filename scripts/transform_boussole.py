"""Transforme le JSON Boussole.in vers le format attendu par import_boussole.py.

Entrée  : data/boussole_raw.json — liste d'objets détaillés Boussole
          (informations_generales, programmes.diplomes_proposes, domaines_etude…).
Sortie  : data/boussole_export.json — format consommé par scripts/import_boussole.py :

    {
      "universities": [
        {
          "name": "...",
          "email": "...",            # vrai email si dispo, sinon placeholder
          "programs": [
            {"name": "...", "domain": "Informatique", "description": "..."}
          ]
        }
      ]
    }

Règles :
  - email manquant → placeholder déterministe "<id>@import.boussole.tg"
  - chaque diplôme de `diplomes_proposes` devient un Program
  - le `domain` d'un programme est déduit par mots-clés depuis `domaines_etude`
  - si aucune liste de diplômes, on crée un programme générique par domaine d'étude

Usage :
    python scripts/transform_boussole.py
    python scripts/transform_boussole.py --in data/boussole_raw.json --out data/boussole_export.json
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path

PLACEHOLDER_DOMAIN = "import.boussole.tg"

# Synonymes mot-clé -> domaine canonique (pour rattacher un diplôme à un domaine)
_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "Informatique": ("informatique", "info", "réseau", "reseau", "logiciel", "digital", "numérique", "numerique", "data", "système", "systeme"),
    "Gestion": ("gestion", "management", "administration", "rh", "ressources humaines", "logistique", "transport"),
    "Comptabilité": ("comptab", "compta", "audit", "fiscal"),
    "Finance": ("finance", "banque", "bancaire", "assurance"),
    "Droit": ("droit", "juridique", "juriste", "notariat"),
    "Communication": ("communication", "marketing", "journalisme", "publicité", "publicite", "média", "media"),
    "Santé": ("santé", "sante", "infirmier", "médical", "medical", "pharma", "soins", "biomédical", "biomedical", "sage-femme"),
    "Tourisme": ("tourisme", "hôtellerie", "hotellerie", "restauration", "cuisine"),
    "Génie": ("génie", "genie", "btp", "bâtiment", "batiment", "électr", "electr", "mécanique", "mecanique", "industriel"),
    "Agronomie": ("agro", "agricole", "agronomie", "élevage", "elevage", "environnement"),
    "Éducation": ("éducation", "education", "enseignement", "pédagog", "pedagog", "normale"),
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def _slug(s: str) -> str:
    s = _norm(s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _guess_domain(program_name: str, domaines_etude: list[str]) -> str | None:
    """Déduit le domaine d'un programme à partir de son intitulé et des domaines de l'univ."""
    pn = _norm(program_name)

    # 1) Un domaine de l'université apparaît littéralement dans l'intitulé
    for dom in domaines_etude:
        if _norm(dom) and _norm(dom) in pn:
            return dom

    # 2) Correspondance par synonymes, restreinte aux domaines de l'université
    univ_norm = {_norm(d): d for d in domaines_etude}
    for canonical, keywords in _DOMAIN_SYNONYMS.items():
        if any(kw in pn for kw in keywords):
            # privilégier un domaine réellement listé par l'université
            if _norm(canonical) in univ_norm:
                return univ_norm[_norm(canonical)]
            return canonical

    # 3) Repli : si l'université n'a qu'un seul domaine, on le prend
    if len(domaines_etude) == 1:
        return domaines_etude[0]

    return None


def transform(raw: list[dict]) -> dict:
    universities = []
    for u in raw:
        ig = u.get("informations_generales", {})
        uid = u.get("id") or _slug(ig.get("nom_court", ""))
        name = (ig.get("nom_complet") or ig.get("nom_court") or "").strip()
        if not name:
            continue

        raw_email = ig.get("contacts", {}).get("email") or ""
        if isinstance(raw_email, list):  # certains fichiers listent plusieurs emails
            raw_email = raw_email[0] if raw_email else ""
        email = str(raw_email).strip().lower()
        if not email:
            email = f"{uid}@{PLACEHOLDER_DOMAIN}"

        prog_block = u.get("programmes", {})
        diplomes = [d.strip() for d in prog_block.get("diplomes_proposes", []) if d and d.strip()]
        domaines = [d.strip() for d in prog_block.get("domaines_etude", []) if d and d.strip()]

        programs = []
        if diplomes:
            for dip in diplomes:
                programs.append(
                    {"name": dip, "domain": _guess_domain(dip, domaines), "description": None}
                )
        else:
            # Pas de diplôme listé : on crée un programme générique par domaine
            for dom in domaines:
                programs.append(
                    {"name": f"Formation en {dom}", "domain": dom, "description": None}
                )

        universities.append({"name": name, "email": email, "programs": programs})

    return {"universities": universities}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Transforme Boussole.in vers le format d'import.")
    p.add_argument("--in", dest="src", default="data/boussole_raw.json")
    p.add_argument("--out", dest="dst", default="data/boussole_export.json")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    raw = json.loads(Path(args.src).read_text(encoding="utf-8"))
    result = transform(raw)
    Path(args.dst).parent.mkdir(parents=True, exist_ok=True)
    Path(args.dst).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_univ = len(result["universities"])
    n_prog = sum(len(u["programs"]) for u in result["universities"])
    n_ph = sum(1 for u in result["universities"] if u["email"].endswith(PLACEHOLDER_DOMAIN))
    print(f"[OK] {n_univ} universités, {n_prog} programmes -> {args.dst}")
    print(f"     dont {n_ph} emails placeholder (@{PLACEHOLDER_DOMAIN})")


if __name__ == "__main__":
    main()
