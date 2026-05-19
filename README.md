# Admission WhatsApp API

API SaaS d'admission universitaire pilotée par WhatsApp. Les universités branchent leur plateforme existante sur cette API : elles reçoivent des dossiers de candidature **déjà collectés, OCR-isés, classifiés par IA et validés** via des webhooks signés HMAC. Aucun frontend, aucun dashboard — uniquement une API et un bot WhatsApp.

---

## Flux fonctionnel

```
 Étudiant                  API SaaS                       Université
   │                          │                                │
   │ ─── WhatsApp msg ────────▶│                                │
   │                          │ Bot conversationnel            │
   │ ◀── « envoyez diplôme » ─│                                │
   │ ─── PDF/photo ───────────▶│                                │
   │                          │ ┌─ OCR (Tesseract) ──┐         │
   │                          │ ├─ IA classification ┤         │
   │                          │ └─ Validation       ─┘         │
   │                          │                                │
   │                          │ ── application.validated ─────▶│
   │                          │      (webhook signé HMAC)      │
   │                          │                                │
   │                          │ ◀──── POST /decision ─────────│
   │                          │       (ACCEPTED / REJECTED)    │
   │ ◀── notification ────────│                                │
```

---

## Stack

- **Python 3.11 + FastAPI 0.110** (Uvicorn / Gunicorn)
- **SQLAlchemy 2.0** + **Alembic** sur **PostgreSQL 15**
- **Redis 7** + **Celery 5** (3 queues : `ocr`, `ai`, `webhooks`)
- **Google Cloud Storage** pour les documents
- **Tesseract 5** (OCR FR + EN) via `pytesseract`
- **Anthropic Claude** (`claude-sonnet-4-20250514`) pour la classification IA
- **Twilio WhatsApp API**
- **Docker Compose** (dev + prod), Nginx en reverse proxy
- **Sentry** + Google Cloud Logging

---

## Démarrage local

### 1. Prérequis
- Docker & Docker Compose
- Un compte Twilio avec un numéro WhatsApp sandbox/approuvé
- Une clé API Anthropic
- Un bucket Google Cloud Storage + service account JSON

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env et renseigner les valeurs réelles
```

Placez votre clé GCS dans `./credentials/gcs-key.json` (chemin référencé par `GOOGLE_APPLICATION_CREDENTIALS`).

### 3. Lancement

```bash
docker compose up -d --build
```

L'API est disponible sur `http://localhost:8000`.
- Swagger : `http://localhost:8000/docs`
- Healthcheck : `http://localhost:8000/health`

### 4. Migrations

Les migrations sont exécutées automatiquement au démarrage du conteneur `api`. Pour en créer une nouvelle :

```bash
docker compose exec api alembic revision --autogenerate -m "votre message"
docker compose exec api alembic upgrade head
```

### 5. Webhook Twilio

Pointez le webhook WhatsApp Twilio vers :
```
POST https://votre-domaine.com/whatsapp/incoming
```

---

## Obtenir une clé API (université cliente)

Un endpoint d'administration n'est pas encore exposé via HTTP — pour l'instant, l'inscription d'une université se fait par script :

```bash
docker compose exec api python -c "
from app.database import get_db_session
from app.auth.api_key import (
    generate_api_key, generate_api_secret, generate_webhook_secret,
    hash_credential, api_key_prefix,
)
from app.models.university import University
import uuid

key = generate_api_key()
secret = generate_api_secret()
wh_secret = generate_webhook_secret()

db = get_db_session()
u = University(
    id=uuid.uuid4(),
    name='Université d\\'Exemple',
    email='admin@univ-exemple.tg',
    api_key_hash=hash_credential(key),
    api_secret_hash=hash_credential(secret),
    api_key_prefix=api_key_prefix(key),
    webhook_url='https://univ-exemple.tg/webhooks/admission',
    webhook_secret=wh_secret,
    is_active=True,
)
db.add(u); db.commit()
print('API_KEY     :', key)
print('API_SECRET  :', secret)
print('WEBHOOK_SECRET (à stocker côté université) :', wh_secret)
"
```

⚠️ Les `API_SECRET` et `WEBHOOK_SECRET` ne sont **affichés qu'une seule fois** — stockez-les immédiatement de manière sécurisée.

---

## Endpoints API

Toutes les requêtes sous `/api/v1` exigent les headers :
```
X-API-Key: univ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
X-API-Secret: sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

| Méthode | Route                                              | Description                                |
|---------|----------------------------------------------------|--------------------------------------------|
| GET     | `/health`                                          | Healthcheck (public)                       |
| POST    | `/whatsapp/incoming`                               | Webhook Twilio (signé)                     |
| POST    | `/api/v1/applications`                             | Créer une candidature manuellement         |
| GET     | `/api/v1/applications`                             | Lister (filtres: status, program, dates)   |
| GET     | `/api/v1/applications/{id}`                        | Détail complet + documents                 |
| PATCH   | `/api/v1/applications/{id}`                        | Mettre à jour partiellement                |
| POST    | `/api/v1/applications/{id}/documents`              | Uploader un document (multipart)           |
| POST    | `/api/v1/applications/{id}/decision`               | Envoyer décision (ACCEPTED/REJECTED)       |
| GET     | `/api/v1/webhooks`                                 | Lire la configuration webhook              |
| PUT     | `/api/v1/webhooks`                                 | Mettre à jour URL / régénérer secret       |
| GET     | `/api/v1/webhooks/deliveries`                      | Historique des livraisons webhook          |

**Format de réponse uniforme** :

```json
// Succès
{ "success": true, "data": { ... } }

// Erreur
{ "success": false, "error": { "code": "APPLICATION_NOT_FOUND", "message": "..." } }
```

---

## Webhooks — événements et payloads

Chaque webhook envoyé est signé HMAC-SHA256 et inclut un `idempotency_key` unique. Une université peut recevoir deux fois le même événement sans risque (même `idempotency_key` → ignorer côté receveur).

### Headers envoyés
```
Content-Type: application/json; charset=utf-8
User-Agent: AdmissionWhatsApp-Webhook/1.0
X-Webhook-Signature: sha256=<hex_hmac>
X-Webhook-Timestamp: <unix_seconds>
X-Webhook-Event: application.validated
X-Webhook-Event-Id: <uuid_hex>          ← idempotency_key
```

### Événement `application.validated`

```json
{
  "event": "application.validated",
  "timestamp": 1716134400,
  "idempotency_key": "8a3f4b9c7d1e2f0a8c6b5d4e3f2a1098",
  "data": {
    "application_id": "f3a1b2c4-d5e6-7890-1234-56789abcdef0",
    "student": {
      "phone": "+22890123456",
      "name": "Kofi Mensah",
      "email": "kofi@example.com"
    },
    "program": "Licence Informatique",
    "documents": [
      {
        "id": "...",
        "type": "DIPLOME",
        "gcs_url": "https://storage.googleapis.com/...?X-Goog-Signature=...",
        "ocr_text": "République Togolaise — Ministère de l'Enseignement Supérieur...",
        "is_valid": true,
        "classification": { "confidence": 0.95, "extracted_fields": { ... } }
      }
    ],
    "validation_score": 0.97,
    "ai_notes": "Dossier validé automatiquement.",
    "submitted_at": "2026-05-19T10:00:00Z"
  }
}
```

### Événement `application.decision.acknowledged`

Émis lorsqu'une université enregistre une décision via `POST /applications/{id}/decision`.

```json
{
  "event": "application.decision.acknowledged",
  "timestamp": 1716200000,
  "idempotency_key": "...",
  "data": {
    "application_id": "...",
    "decision": "ACCEPTED",
    "comment": "Bienvenue à l'université !",
    "decided_at": "2026-05-20T14:30:00Z"
  }
}
```

### Politique de retry
Si l'université ne répond pas en 2xx, le webhook est rejoué selon ce calendrier :
**1 min → 5 min → 15 min → 1 h → 6 h** (5 tentatives max). Chaque tentative est tracée dans `webhook_deliveries`.

---

## Vérifier la signature côté université

### Python (FastAPI / Django / Flask)

```python
import hmac, hashlib, json, time

WEBHOOK_SECRET = "votre_secret_recu_a_la_configuration"
TOLERANCE_SECONDS = 300

def verify_webhook(body: bytes, signature_header: str, timestamp_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    signature = signature_header.split("=", 1)[1]

    timestamp = int(timestamp_header)
    if abs(time.time() - timestamp) > TOLERANCE_SECONDS:
        return False  # Anti-replay

    message = f"{timestamp}.".encode("utf-8") + body
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# Usage (FastAPI) :
# @app.post("/webhooks/admission")
# async def receive(request: Request):
#     body = await request.body()
#     ok = verify_webhook(
#         body,
#         request.headers["X-Webhook-Signature"],
#         request.headers["X-Webhook-Timestamp"],
#     )
#     if not ok:
#         raise HTTPException(403, "Signature invalide")
#     # Idempotence : ignorer si on a déjà vu cet event-id
#     event_id = request.headers["X-Webhook-Event-Id"]
#     ...
```

### Node.js (Express)

```js
const crypto = require('crypto');

const WEBHOOK_SECRET = process.env.ADMISSION_WEBHOOK_SECRET;
const TOLERANCE_SECONDS = 300;

function verifyWebhook(rawBody, signatureHeader, timestampHeader) {
  if (!signatureHeader || !signatureHeader.startsWith('sha256=')) return false;
  const signature = signatureHeader.slice(7);

  const ts = parseInt(timestampHeader, 10);
  if (Math.abs(Date.now() / 1000 - ts) > TOLERANCE_SECONDS) return false;

  const message = Buffer.concat([Buffer.from(`${ts}.`), rawBody]);
  const expected = crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(message)
    .digest('hex');

  return crypto.timingSafeEqual(
    Buffer.from(expected, 'hex'),
    Buffer.from(signature, 'hex'),
  );
}

// Express : utiliser express.raw({ type: 'application/json' })
app.post('/webhooks/admission', express.raw({ type: '*/*' }), (req, res) => {
  const ok = verifyWebhook(
    req.body,                                     // Buffer brut
    req.header('X-Webhook-Signature'),
    req.header('X-Webhook-Timestamp'),
  );
  if (!ok) return res.status(403).send('invalid signature');

  const eventId = req.header('X-Webhook-Event-Id');
  // → idempotence : skip si déjà traité

  res.status(200).send('ok');
});
```

---

## Architecture des dossiers

```
admission-whatsapp-api/
├── app/
│   ├── main.py                FastAPI entry point
│   ├── config.py              Pydantic settings
│   ├── database.py            SQLAlchemy engine + session
│   ├── models/                Modèles ORM (University, Application, ...)
│   ├── schemas/               Pydantic IO schemas
│   ├── api/
│   │   ├── v1/                Endpoints REST authentifiés par API Key
│   │   └── whatsapp/          Webhook Twilio entrant
│   ├── services/              Logique métier (bot, OCR, IA, webhooks, GCS)
│   ├── workers/               Tâches Celery (ocr_tasks, ai_tasks, webhook_tasks)
│   ├── auth/                  API Key + Secret (bcrypt)
│   └── utils/                 HMAC signer
├── alembic/                   Migrations
├── tests/                     Pytest (DB SQLite en mémoire)
├── docker/                    Dockerfiles + nginx.conf
├── docker-compose.yml         Dev
├── docker-compose.prod.yml    Surcharge prod
└── requirements.txt
```

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

Les tests utilisent SQLite en mémoire et mockent Twilio/Anthropic/GCS. Aucun service externe nécessaire.

---

## Sécurité

- API Keys et Secrets hashés en **bcrypt cost 12**.
- Webhooks signés **HMAC-SHA256** avec timestamp et fenêtre anti-replay configurable.
- Documents stockés uniquement sur GCS — URLs signées à durée limitée transmises aux universités.
- Vérification de la signature Twilio sur le webhook entrant (en production).
- CORS strict en production.
- Rate limiting global et par numéro de téléphone.

---

## Licence

Propriétaire — tous droits réservés.
