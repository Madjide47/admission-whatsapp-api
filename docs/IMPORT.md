# Import Boussole.in — procédure et validation

> **Scripts :** `scripts/consolidate_boussole.py`, `scripts/transform_boussole.py`, `scripts/import_boussole.py`
> **Source des données :** dépôt [TEPE-Togo/boussole](https://github.com/TEPE-Togo/boussole)

---

## 1. Objectif

Pré-remplir la base avec les universités et programmes **réels de Boussole.in**, de façon **idempotente** (relancer sans créer de doublons).

Données importées : **48 universités, 274 programmes**.

---

## 2. Le pipeline en 3 étapes

Le dépôt Boussole stocke ses données dans `public/data/universities/*.json` (un fichier détaillé par établissement) avec un format propre à Boussole. On le transcrit vers notre base via trois scripts enchaînés :

```
Dépôt Boussole (55 fichiers public/data/universities/*.json)
   │
   │  1. scripts/consolidate_boussole.py   → fusionne + déduplique
   ▼
data/boussole_raw.json        (48 universités — snapshot vendoré dans le repo)
   │
   │  2. scripts/transform_boussole.py     → format d'import + domaines + emails
   ▼
data/boussole_export.json     (format consommé par import_boussole.py)
   │
   │  3. scripts/import_boussole.py        → écriture en base (idempotent)
   ▼
Base PostgreSQL : universities + programs + admission_forms (vides)
```

Les fichiers `data/boussole_raw.json` et `data/boussole_export.json` sont **versionnés** : un import est donc reproductible sans re-cloner Boussole. Les étapes 1 et 2 ne servent qu'à **rafraîchir** les données (voir §6).

---

## 3. Formats de données

### 3.1 Format source Boussole (un fichier par université)

```json
{
  "id": "esgis",
  "informations_generales": {
    "nom_court": "École Supérieure de Gestion...",
    "nom_complet": "École Supérieure de Gestion... (ESGIS)",
    "type": "École",
    "ville": "Lomé",
    "annee_creation": "1994",
    "contacts": { "email": "esgis.togo@esgis.org", "site_web": "https://..." }
  },
  "programmes": {
    "diplomes_proposes": ["Licence Informatique", "BTS Réseaux"],
    "domaines_etude": ["Informatique", "Gestion"]
  },
  "accreditations": [...],
  "partenariats": [...]
}
```

### 3.2 Format d'import (produit par `transform_boussole.py`)

```json
{
  "universities": [
    {
      "name": "École Supérieure de Gestion... (ESGIS)",
      "email": "esgis.togo@esgis.org",
      "programs": [
        { "name": "Licence Informatique", "domain": "Informatique", "description": null }
      ]
    }
  ]
}
```

| Champ | Obligatoire | Description |
|-------|-------------|-------------|
| `name` | Oui | `nom_complet` (sinon `nom_court`) de Boussole |
| `email` | Oui | Email administratif — **unique en base** (clé d'idempotence université) |
| `programs[].name` | Oui | Un diplôme de `diplomes_proposes` |
| `programs[].domain` | Non | Domaine déduit — alimente le **filtre par domaine du bot WhatsApp** |
| `programs[].description` | Non | Non fourni par Boussole (null) |

> **Idempotence** : une université est identifiée par son **`email`**, un programme par son **`name`** au sein de l'université. Relancer l'import ignore ce qui existe déjà (aucun `external_id` n'est utilisé).

---

## 4. Règles de transformation

Appliquées par `scripts/transform_boussole.py` :

- **Emails manquants** (26/48) → placeholder déterministe `<id>@import.boussole.tg`.
  L'administrateur corrigera les vrais emails ensuite. Si un fichier liste plusieurs
  emails, le premier est retenu.
- **Programmes** = liste `diplomes_proposes`. Si elle est vide, on crée un programme
  générique « Formation en `<domaine>` » par domaine d'étude.
- **Domaine d'un programme** déduit dans cet ordre :
  1. un `domaines_etude` apparaît littéralement dans l'intitulé du diplôme ;
  2. correspondance par mots-clés (table de synonymes), restreinte aux domaines de l'université ;
  3. repli : si l'université n'a qu'un seul domaine, on le prend ;
  4. sinon `domain = null`.
- **Déduplication** (étape `consolidate`) par nom complet normalisé — gère les variantes
  de fichiers (`don-bosco` / `donbosco` / `don_bosco`). En cas de doublon, la variante
  avec le plus de diplômes est conservée.

Couverture obtenue : **~75 % des programmes ont un domaine** (les 25 % restants sont des
intitulés génériques type « DUT », « Master Professionnel »).

---

## 5. Procédure d'import (cas standard)

Les fichiers étant déjà versionnés, l'import se résume à l'étape 3 :

```bash
# Dry-run (simulation, rollback final, aucune écriture)
python scripts/import_boussole.py --file data/boussole_export.json --dry-run

# Import réel
python scripts/import_boussole.py --file data/boussole_export.json

# Via Docker
docker compose exec api python scripts/import_boussole.py --file data/boussole_export.json
```

**Sortie attendue (1er import) :**

```
Resume :
  Universites crees  : 48
  Universites skip   : 0
  Programmes crees   : 274
  Programmes skip    : 0
```

**Sortie au 2ᵉ passage (idempotence) :**

```
Resume :
  Universites crees  : 0
  Universites skip   : 48
  Programmes crees   : 0
  Programmes skip    : 274
```

> ⚠️ **Credentials** : à chaque université **créée**, le script affiche **une seule fois**
> son `API Key` (`univ_…`) et son `API Secret` (`sk_…`). Conservez-les : ils ne sont
> stockés qu'en hash bcrypt et ne pourront pas être réaffichés.

---

## 6. Rafraîchir les données depuis Boussole

À faire uniquement quand les données Boussole ont changé :

```bash
# 1. Cloner le dépôt source (accès collaborateur requis)
git clone --depth 1 https://github.com/TEPE-Togo/boussole /tmp/boussole

# 2. Consolider les fichiers détaillés -> snapshot vendoré
python scripts/consolidate_boussole.py /tmp/boussole/public/data/universities data/boussole_raw.json

# 3. Régénérer le fichier d'import
python scripts/transform_boussole.py

# 4. Importer (idempotent : seules les nouveautés sont créées)
python scripts/import_boussole.py --file data/boussole_export.json
```

Pensez à committer `data/boussole_raw.json` et `data/boussole_export.json` mis à jour.

---

## 7. Checklist de validation

- [ ] `--dry-run` annonce 48 universités / 274 programmes, aucune écriture
- [ ] Le script refuse un JSON mal formé avec un message clair
- [ ] Relancer l'import ne crée pas de doublons (tout en `skip`)
- [ ] Les programmes sont liés au bon `university_id`
- [ ] Les domaines sont peuplés (`SELECT domain, count(*) FROM programs GROUP BY domain`)
- [ ] Le bot WhatsApp filtre les universités par domaine d'intérêt
- [ ] Logs sans traceback en cas de doublon (comportement normal)

---

## 8. Limites connues

- **Champs non importés** : `ville`, `type`, `annee_creation`, `accreditations`,
  `partenariats` de Boussole n'ont pas de colonne dans notre modèle `University`.
  Les ajouter nécessite une migration Alembic.
- **Emails placeholder** (`@import.boussole.tg`) : à remplacer par les vrais emails
  avant toute communication réelle vers les universités.
- **Programmes sans domaine** (~25 %) : améliorables en enrichissant la table de
  synonymes dans `scripts/transform_boussole.py`.

---

## 9. Jeu de données de démo

`data/boussole_sample.json` (3 universités fictives, domaines Informatique / Droit / Gestion)
reste disponible pour les tests rapides et la démo, indépendamment des données réelles.
