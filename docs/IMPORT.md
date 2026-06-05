# Import Boussole.in — procédure et validation

> **Responsable script :** Dev 1 (`scripts/import_boussole.py`)  
> **Responsable validation :** Dev 3 (ce document)

---

## 1. Objectif

Pré-remplir la base avec les universités et programmes depuis le fichier JSON **Boussole.in**, de façon **idempotente** (relancer sans doublons).

---

## 2. Structure JSON attendue

```json
{
  "universities": [
    {
      "external_id": "boussole-univ-001",
      "name": "Université de Lomé",
      "email": "contact@univ-lome.tg",
      "programs": [
        {
          "external_id": "boussole-prog-001",
          "name": "Licence Informatique",
          "description": "Formation en informatique",
          "domain": "Informatique"
        }
      ]
    }
  ]
}
```

| Champ | Obligatoire | Description |
|-------|-------------|-------------|
| `external_id` | Oui | Identifiant stable Boussole — clé d'idempotence |
| `name` | Oui | Nom affiché |
| `email` | Oui | Email administratif (unique en base) |
| `programs` | Non | Liste des programmes à créer |

---

## 3. Procédure d'import (à valider quand Dev 1 livre)

```bash
# Copier l'échantillon
cp data/boussole_sample.json data/boussole.json

# Lancer l'import
make import-boussole
# ou :
docker compose exec api python scripts/import_boussole.py --file data/boussole.json
```

**Sortie attendue :**

```
Import Boussole.in terminé
  Universités créées : 12
  Universités ignorées (déjà présentes) : 3
  Programmes créés : 45
  Programmes ignorés : 8
```

---

## 4. Checklist de validation (Dev 3)

- [ ] Le script refuse un JSON mal formé avec message clair
- [ ] Relancer l'import ne crée pas de doublons (`external_id` respecté)
- [ ] Chaque université a au moins un programme si fourni dans le JSON
- [ ] Les programmes sont liés au bon `university_id`
- [ ] Le bot WhatsApp liste les universités importées
- [ ] Logs sans traceback en cas de doublon (comportement normal)

---

## 5. Jeu de données de démo

Fichier à préparer : `data/boussole_sample.json` (3 universités, 2-3 programmes chacune).

Domaines couverts pour tester le flow bot :

- Informatique
- Droit
- Gestion

---

## 6. Dépendances

| Bloquant | Responsable |
|----------|-------------|
| Tables `programs` + migration Alembic | Dev 1 |
| Script `import_boussole.py` | Dev 1 |
| Validation bout en bout | Dev 3 |
