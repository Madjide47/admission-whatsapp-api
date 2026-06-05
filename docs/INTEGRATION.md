# Guide d'intégration — API Admission WhatsApp v2

> Document maintenu par **Dev 3**. Source de vérité pour les universités clientes et l'équipe.

---

## 1. Vue d'ensemble

L'API permet aux universités de :

1. **Configurer** leurs programmes et formulaires d'admission (admin v2)
2. **Recevoir** des candidatures validées via webhooks signés HMAC
3. **Décider** (ACCEPTED / REJECTED) et notifier l'étudiant sur WhatsApp

**Principe v2 :** l'administrateur configure, la base stocke, le bot s'adapte — sans modifier le code.

---

## 2. Authentification

Toutes les requêtes `/api/v1/*` (sauf seed) exigent deux headers :

```http
X-API-Key: univ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
X-API-Secret: sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Obtenir des credentials

| Environnement | Méthode |
|---------------|---------|
| Dev / test | `POST /api/v1/admin/seed-university` *(Dev 1 — à venir)* |
| Dev / test | `python scripts/create_university.py --name "..." --email "..."` |
| Production | Processus d'onboarding manuel |

### Swagger UI

1. Ouvrir `http://localhost:8000/docs`
2. Cliquer **Authorize**
3. Renseigner `X-API-Key` et `X-API-Secret`

En dev, pré-remplissage automatique si `.env` contient :

```env
SWAGGER_DEMO_API_KEY=univ_...
SWAGGER_DEMO_API_SECRET=sk_...
```

---

## 3. Administration — formulaires dynamiques (v2)

> **Statut :** endpoints en cours de livraison par Dev 1. Payloads documentés ici pour alignement équipe.

### Flux de configuration

```
seed-university → créer programme → configurer formulaire → publier → bot actif
```

### 3.1 Seed université de test

```http
POST /api/v1/admin/seed-university
```

- **Auth :** aucune (dev/staging uniquement)
- **Protection :** refusé si `ENVIRONMENT=production`
- **Réponse :** credentials en clair + programme + formulaire publié par défaut

```json
{
  "success": true,
  "data": {
    "university_id": "uuid",
    "name": "Université Démo",
    "api_key": "univ_...",
    "api_secret": "sk_...",
    "webhook_secret": "...",
    "program_id": "uuid",
    "form_id": "uuid"
  }
}
```

### 3.2 Programmes

```http
GET  /api/v1/admin/programs
POST /api/v1/admin/programs
```

**Créer un programme :**

```json
{
  "name": "Licence Informatique",
  "description": "Formation en développement logiciel",
  "is_active": true
}
```

### 3.3 Formulaire d'admission

```http
GET /api/v1/admin/forms/{program_id}
PUT /api/v1/admin/forms/{program_id}
POST /api/v1/admin/forms/{program_id}/publish
```

**Mettre à jour un formulaire :**

```json
{
  "fields": [
    {
      "label": "Date de naissance",
      "type": "date",
      "order": 1,
      "is_required": true,
      "validation_regex": "^\\d{4}-\\d{2}-\\d{2}$"
    },
    {
      "label": "Email",
      "type": "email",
      "order": 2,
      "is_required": true,
      "validation_regex": null
    }
  ],
  "required_documents": [
    {
      "document_type": "DIPLOME",
      "label": "Diplôme du baccalauréat",
      "order": 1,
      "is_required": true
    },
    {
      "document_type": "CARTE_IDENTITE",
      "label": "Carte nationale d'identité",
      "order": 2,
      "is_required": true
    }
  ]
}
```

**Publier :**

```http
POST /api/v1/admin/forms/{program_id}/publish
```

Réponse : `{ "success": true, "data": { "is_published": true, "published_at": "..." } }`

### 3.4 Format lu par le bot WhatsApp

Une fois publié, le bot charge :

```json
{
  "program_id": "uuid",
  "program_name": "Licence Informatique",
  "fields": [
    { "id": "uuid", "label": "Date de naissance", "type": "date", "order": 1, "is_required": true, "validation_regex": "..." }
  ],
  "required_documents": [
    { "document_type": "DIPLOME", "label": "Diplôme du baccalauréat", "order": 1, "is_required": true }
  ]
}
```

Le bot pose les `fields` **un par un** dans l'ordre `order`, puis demande les documents **un par un**.

---

## 4. Candidatures (université cliente)

### Lister

```http
GET /api/v1/applications?status=VALIDATED&limit=20&offset=0
```

### Détail

```http
GET /api/v1/applications/{id}
```

### Décision

```http
POST /api/v1/applications/{id}/decision
```

```json
{
  "decision": "ACCEPTED",
  "comment": "Bienvenue à l'université !"
}
```

---

## 5. Webhooks

Voir [webhooks.md](./webhooks.md) pour la vérification HMAC côté université.

| Événement | Déclencheur |
|-----------|-------------|
| `application.validated` | Dossier complet et validé |
| `application.decision.acknowledged` | Décision enregistrée via l'API |

---

## 6. Format de réponse uniforme

```json
{ "success": true, "data": { ... } }
```

```json
{
  "success": false,
  "error": {
    "code": "APPLICATION_NOT_FOUND",
    "message": "Candidature introuvable."
  }
}
```

---

## 7. Scénario de test complet (Postman)

1. `POST /api/v1/admin/seed-university` → copier `api_key`, `api_secret`, `program_id`
2. `GET /api/v1/applications` → vérifier auth
3. Configurer formulaire → `PUT` + `POST publish`
4. Simuler candidature WhatsApp (voir `DEV2_DEMO_SCRIPT.md`)
5. `GET /api/v1/applications/{id}` → dossier validé
6. `POST /api/v1/applications/{id}/decision` → notification étudiant

Collection Postman : [docs/postman/](./postman/)
