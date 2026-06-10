"""Extraction d'une moyenne académique normalisée (sur 20) depuis du texte libre.

La moyenne peut venir de l'IA (champs extraits du relevé : "14.5/20", "14,5")
ou d'un champ de formulaire déclaré par l'étudiant. On la ramène toujours sur 20.
"""
import re

_NUMBER_RE = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:/\s*(\d{1,3}))?")


def parse_average(value) -> float | None:
    """Normalise une moyenne sur 20 à partir d'une chaîne/un nombre.

    Exemples : "14.5/20" → 14.5 ; "14,5" → 14.5 ; "70/100" → 14.0 ;
    "3.5/4" → 17.5 ; "" / None / texte sans nombre → None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        num, scale = float(value), None
    else:
        match = _NUMBER_RE.search(str(value))
        if not match:
            return None
        num = float(match.group(1).replace(",", "."))
        scale = float(match.group(2)) if match.group(2) else None

    if scale and scale > 0:
        normalized = num / scale * 20.0
    elif num > 20:
        # Pas d'échelle explicite mais valeur > 20 → suppose une note sur 100.
        normalized = num / 100.0 * 20.0
    else:
        normalized = num

    if normalized < 0 or normalized > 20:
        return None
    return round(normalized, 2)


def extract_average_from_fields(extracted_fields: dict | None) -> float | None:
    """Cherche une moyenne dans les champs extraits par l'IA (clé 'moyenne'/'average')."""
    if not extracted_fields:
        return None

    candidates: list = []
    additional = extracted_fields.get("additional")
    if isinstance(additional, dict):
        for key, val in additional.items():
            if re.search(r"moyenne|average|gpa|note", str(key), re.IGNORECASE):
                candidates.append(val)
    for key, val in extracted_fields.items():
        if key == "additional":
            continue
        if re.search(r"moyenne|average|gpa", str(key), re.IGNORECASE):
            candidates.append(val)

    for cand in candidates:
        avg = parse_average(cand)
        if avg is not None:
            return avg
    return None
