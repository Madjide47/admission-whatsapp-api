"""One-shot : consolide les fichiers détaillés Boussole en un seul JSON vendoré.

Lit le dossier `public/data/universities/*.json` d'un clone du dépôt Boussole
et produit `data/boussole_raw.json` (liste dédupliquée des objets détaillés).
Déduplication par nom complet normalisé (gère don-bosco / donbosco / don_bosco).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "", s.lower())
    return s


def main(src_dir: str, out_file: str) -> None:
    files = sorted(Path(src_dir).glob("*.json"))
    by_key: dict[str, dict] = {}
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[SKIP] {f.name} illisible : {e}", file=sys.stderr)
            continue
        ig = d.get("informations_generales", {})
        name = ig.get("nom_complet") or ig.get("nom_court") or d.get("id", "")
        key = _norm(name) or _norm(d.get("id", ""))
        if not key:
            continue
        # En cas de doublon, on garde la variante la plus riche (plus de diplômes)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = d
        else:
            dip_new = len(d.get("programmes", {}).get("diplomes_proposes", []))
            dip_old = len(existing.get("programmes", {}).get("diplomes_proposes", []))
            if dip_new > dip_old:
                by_key[key] = d

    result = list(by_key.values())
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {len(files)} fichiers -> {len(result)} universités -> {out_file}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
