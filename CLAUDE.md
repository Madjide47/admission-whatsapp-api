# CLAUDE.md — Contexte complet du projet

> Ce fichier contient TOUTES les spécifications techniques du projet. Il est la source de vérité.
> Ne jamais improviser une architecture, un modèle de données ou un flux qui n'est pas décrit ici.
> En cas de doute, demander plutôt que deviner.

---

## 1. IDENTITÉ ET RÔLE

Je suis **Dev 3 — Documentation et Intégration** dans une équipe de 3 développeurs.

**Mon périmètre :**
- Configuration Swagger / OpenAPI (security schemes, tags, pré-remplissage credentials)
- Documentation d'intégration (`docs/INTEGRATION.md`, `docs/IMPORT.md`, `docs/webhooks.md`)
- Collection Postman v2 (`docs/postman/`)
- Validation import Boussole.in (tests + procédure)
- Mise à jour du cahier des charges et diagrammes v2

**Ce qui n'est PAS mon périmètre :**
- Tables, migrations Alembic, endpoints admin REST (Dev 1 — Backend)
- Bot WhatsApp, OCR, classification IA, workers Celery (Dev 2 — IA/WhatsApp)
- Infrastructure Docker, GCS, Nginx (Dev 1)
- Script `import_boussole.py` (Dev 1 — je valide et documente)

---

## 2. DESCRIPTION DU PROJET

### Ce que c'est
Une **API SaaS pure** permettant aux universités d'automatiser leurs admissions via WhatsApp avec validation automatique des documents par OCR et IA.

### Ce que ce n'est PAS
- Pas de dashboard web (les universités ont déjà leurs plateformes)
- Pas d'interface graphique
- Pas d'application mobile

### Comment ça marche
Les universités intègrent notre API dans leur plateforme existante. Les étudiants postulent via WhatsApp. Notre système collecte, valide automatiquement les documents, puis transmet les dossiers validés aux universités via webhooks signés. L'université décide dans son propre système et nous rappelle via l'API pour notifier l'étudiant.

---

## 3. STACK TECHNOLOGIQUE

| Catégorie | Technologie | Justification |
|-----------|-------------|---------------|
| Langage | Python 3.11+ | Écosystème IA/ML, compatibilité Celery |
| Framework API | FastAPI 0.110+ | Performant, typage natif, Swagger auto |
| Serveur ASGI | Uvicorn (dev) / Gunicorn (prod) | |
| ORM | SQLAlchemy 2.0+ **synchrone** | Synchrone car Celery est synchrone. Un seul mode d'accès aux données |
| Validation | Pydantic V2 | Intégré à FastAPI |
| Migrations | Alembic | Génération auto depuis les modèles |
| File d'attente | Celery 5+ | 3 files séparées, retry natif |
| Broker / Cache | Redis 7+ | Broker Celery + rate limiting |
| OCR | Tesseract 5+ (pytesseract) | Gratuit, supporte fra+eng |
| Classification IA | Anthropic Claude | Prompt structuré, sortie JSON |
| WhatsApp | Twilio WhatsApp API | Fiable, bac à sable gratuit |
| Stockage fichiers | Google Cloud Storage | URLs signées temporaires |
| Base de données | PostgreSQL 15+ | JSONB, UUID, enum natifs |
| Conteneurisation | Docker + Docker Compose | 2 images distinctes (api + worker) |
| Supervision | Sentry + Google Cloud Logging | |

### Dépendances Python principales
```
fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings
celery, redis
twilio
pytesseract, Pillow, pdf2image
anthropic, langchain
google-cloud-storage
httpx, python-jose, passlib[bcrypt]
sentry-sdk[fastapi]
pytest, pytest-cov, pytest-asyncio
```

---

## 4. ARCHITECTURE — FLUX COMPLET

```
Étudiant (WhatsApp)
       │
       ▼
[Twilio] ──POST──▶ /whatsapp/incoming       ← twilio_webhook.py
                          │
                          ▼
                   WhatsAppBot               ← services/whatsapp_bot.py
                   • reconnaît le state
                   • répond via Twilio
                   • si média → enqueue Celery
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        Worker OCR   Worker IA   Worker Webhook
       ocr_tasks.py  ai_tasks.py  webhook_tasks.py
              │           │           │
              ▼           ▼           ▼
         Tesseract    Claude     POST université
         fra+eng      classify   HMAC-SHA256
                                 retry 1m/5m/15m/1h/6h
                                      │
                                      ▼
                              Université reçoit
                              dans SA plateforme
                                      │
                                      ▼
Université ──POST /api/v1/applications/{id}/decision──▶ Notre API
                                                          │
                                                          ▼
                                              WhatsApp notification étudiant
```

### Principe clé : PIPELINE
Chaque worker déclenche le suivant automatiquement :
1. `process_incoming_media` → 2. `run_ocr_task` → 3. `classify_document_task` → 4. `check_application_completion_task` → 5. `dispatch_validated_application_task` → 6. `send_webhook_task`

---

## 5. STRUCTURE DU CODE SOURCE

```
university-admission-api/
├── CLAUDE.md
├── app/
│   ├── __init__.py                    # __version__ = "1.0.0"
│   ├── main.py                        # Point d'entrée FastAPI
│   ├── config.py                      # Variables d'env (pydantic-settings, @lru_cache)
│   ├── database.py                    # Connexion PostgreSQL (engine, SessionLocal, Base, get_db)
│   │
│   ├── auth/
│   │   └── api_key.py                 # Génération/hashage/vérification API Key (bcrypt, préfixe)
│   │
│   ├── api/
│   │   ├── v1/
│   │   │   ├── router.py              # Regroupe les sous-routeurs sous /api/v1
│   │   │   ├── applications.py        # GET/POST/PATCH candidatures
│   │   │   ├── documents.py           # POST upload documents
│   │   │   ├── decisions.py           # POST décision (idempotent)
│   │   │   └── webhooks.py            # GET/PUT config webhooks, GET deliveries
│   │   └── whatsapp/
│   │       └── twilio_webhook.py      # POST /whatsapp/incoming  ← MON FICHIER
│   │
│   ├── models/                        # Modèles SQLAlchemy = tables SQL
│   │   ├── university.py              # Table universities
│   │   ├── application.py             # Table applications
│   │   ├── document.py                # Table documents
│   │   └── webhook_log.py             # Table webhook_deliveries
│   │
│   ├── schemas/                       # Schémas Pydantic = validation JSON (≠ modèles)
│   │   ├── application.py
│   │   ├── document.py
│   │   └── webhook.py
│   │
│   ├── services/                      # Logique métier
│   │   ├── whatsapp_bot.py            # Machine à états  ← MON FICHIER
│   │   ├── ocr_service.py             # Tesseract OCR    ← MON FICHIER
│   │   ├── ai_classifier.py           # Claude classify  ← MON FICHIER
│   │   ├── validator.py               # Validation dossier ← MON FICHIER
│   │   ├── webhook_service.py         # Signature HMAC + envoi (Dev 1)
│   │   └── storage_service.py         # Google Cloud Storage (Dev 1)
│   │
│   ├── workers/                       # Tâches asynchrones Celery
│   │   ├── celery_app.py              # Config Celery (Dev 1)
│   │   ├── ocr_tasks.py               # Tâches OCR       ← MON FICHIER
│   │   ├── ai_tasks.py                # Tâches IA        ← MON FICHIER
│   │   └── webhook_tasks.py           # Tâches webhooks (Dev 1)
│   │
│   └── utils/
│       └── hmac_signer.py             # Signature HMAC-SHA256 (Dev 1)
│
├── alembic/                           # Migrations base de données
├── tests/
│   ├── conftest.py                    # Fixtures (SQLite mémoire, mocks Twilio/Claude)
│   ├── test_applications.py
│   ├── test_ocr.py
│   └── test_webhooks.py
├── docker/
│   ├── Dockerfile.api                 # Image légère (~120 Mo)
│   ├── Dockerfile.worker              # Image lourde avec Tesseract + poppler
│   └── nginx.conf
├── docs/
├── docker-compose.yml
├── docker-compose.prod.yml
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## 6. MODÈLE DE DONNÉES COMPLET

### 6.1 Table `universities`
```
id                  UUID PK         Non devinable
name                VARCHAR(255)    Nom de l'université
api_key_hash        VARCHAR(255)    Hash bcrypt de l'API Key
api_key_prefix      VARCHAR(12)     12 premiers chars en clair (indexé, lookup rapide)
api_secret_hash     VARCHAR(255)    Hash bcrypt de l'API Secret
webhook_url         VARCHAR(500)    URL destination des webhooks
webhook_secret      VARCHAR(255)    Secret HMAC pour signer les webhooks
is_active           BOOLEAN         Interrupteur on/off
created_at          TIMESTAMP       server_default=func.now()
updated_at          TIMESTAMP       onupdate=func.now()
```
Relations : 1 University → N Applications, 1 University → N WebhookDeliveries

### 6.2 Table `applications`
```
id                  UUID PK
university_id       UUID FK → universities
student_phone       VARCHAR(20)     Numéro WhatsApp (format international +228...)
student_name        VARCHAR(255)    Nom complet
program             VARCHAR(255)    Programme visé
status              ENUM            COLLECTING | VALIDATING | VALIDATED | SENT_TO_UNIVERSITY | ACCEPTED | REJECTED
conversation_state  VARCHAR(50)     État machine WhatsApp : WELCOME | COLLECT_NAME | COLLECT_PROGRAM | COLLECT_DOCS | WAITING_VALIDATION | DONE
validation_score    FLOAT           Score moyen confiance IA (0.0 à 1.0)
ai_notes            TEXT            Résumé IA du dossier
decision_comment    TEXT            Commentaire de l'université
decided_at          TIMESTAMP       Date de la décision
created_at          TIMESTAMP
updated_at          TIMESTAMP
```
Relations : 1 Application → N Documents, 1 Application → N WebhookDeliveries

### 6.3 Table `documents`
```
id                      UUID PK
application_id          UUID FK → applications
original_filename       VARCHAR(255)
mime_type               VARCHAR(50)     application/pdf | image/jpeg | image/png | image/webp | image/heic
gcs_path                VARCHAR(500)    Chemin dans Google Cloud Storage (JAMAIS sur disque local)
document_type           ENUM            DIPLOME | RELEVE_NOTES | CARTE_IDENTITE | PHOTO | AUTRE
ocr_text                TEXT            Sortie brute Tesseract
classification_result   JSONB           Réponse complète de Claude : { detected_type, is_valid, confidence, errors, summary }
is_valid                BOOLEAN         Verdict final
validation_errors       JSONB           Liste des problèmes : ["Date manquante", "Nom illisible"]
created_at              TIMESTAMP
updated_at              TIMESTAMP
```

### 6.4 Table `webhook_deliveries`
```
id                      UUID PK
university_id           UUID FK → universities
application_id          UUID FK → applications
event_type              VARCHAR(100)    application.validated | application.decision.acknowledged
idempotency_key         VARCHAR(100)    Clé unique pour dédoublonnage (UNIQUE)
payload                 JSONB           Contenu exact envoyé (rejouable)
status                  ENUM            PENDING | SUCCESS | FAILED
attempt_count           INTEGER         Nombre de tentatives
response_status_code    INTEGER         Code HTTP dernière réponse
response_body_snippet   TEXT            Extrait de la réponse
last_error              TEXT            Message d'erreur dernière tentative
created_at              TIMESTAMP
updated_at              TIMESTAMP
```

### Pourquoi JSONB et pas JSON
JSONB est binaire, plus rapide à interroger et indexable par PostgreSQL.

### Pourquoi UUID et pas auto-incrément
Les UUID ne sont pas devinables. Un id=1,2,3 permettrait à un attaquant d'énumérer les ressources.

---

## 7. ENDPOINTS API REST

Tous les endpoints sous /api/v1 requièrent X-API-Key et X-API-Secret dans les headers.
Format de réponse uniforme : `{ "success": true, "data": {...} }` ou `{ "success": false, "error": { "code": "...", "message": "..." } }`

### Candidatures
| Méthode | Route | Description |
|---------|-------|-------------|
| GET | /api/v1/applications | Liste (filtres: status, program, dates ; pagination: limit, offset) |
| GET | /api/v1/applications/{id} | Détail avec documents et résultats validation |
| PATCH | /api/v1/applications/{id} | Mise à jour partielle |

### Documents
| Méthode | Route | Description |
|---------|-------|-------------|
| POST | /api/v1/applications/{id}/documents | Upload. MIME: PDF, JPEG, PNG, WEBP, HEIC. Max 10 Mo. Déclenche run_ocr_task |

### Décisions
| Méthode | Route | Description |
|---------|-------|-------------|
| POST | /api/v1/applications/{id}/decision | { decision: ACCEPTED\|REJECTED, comment }. Idempotent. Notifie l'étudiant via WhatsApp |

### Webhooks
| Méthode | Route | Description |
|---------|-------|-------------|
| GET | /api/v1/webhooks | Config actuelle |
| PUT | /api/v1/webhooks | Mettre à jour URL / régénérer secret (renvoyé en clair une seule fois) |
| GET | /api/v1/webhooks/deliveries | Historique paginé des envois |

### WhatsApp (mon endpoint)
| Méthode | Route | Description |
|---------|-------|-------------|
| POST | /whatsapp/incoming | Appelé par Twilio. Vérifie signature Twilio (HMAC-SHA1 en prod). Délègue au bot |

### Santé
| Méthode | Route | Description |
|---------|-------|-------------|
| GET | /health | { status: ok, version }. Utilisé par les load balancers |

---

## 8. SYSTÈME DE WEBHOOKS

### Événements
| Événement | Déclencheur |
|-----------|-------------|
| application.validated | Tous les documents présents et validés |
| application.decision.acknowledged | L'université a envoyé sa décision |

### Structure du payload
```json
{
  "event": "application.validated",
  "timestamp": "2026-05-30T14:30:00Z",
  "idempotency_key": "evt_abc123...",
  "data": {
    "application_id": "uuid...",
    "student_name": "Jean Dupont",
    "student_phone": "+228...",
    "program": "Master Informatique",
    "validation_score": 0.92,
    "ai_notes": "Dossier complet et cohérent.",
    "documents": [
      {
        "type": "DIPLOME",
        "is_valid": true,
        "confidence": 0.95,
        "download_url": "https://storage.googleapis.com/...?X-Goog-Signature=..."
      }
    ]
  }
}
```

### Signature HMAC-SHA256
- Message signé : `"<timestamp>.<payload_json_canonique>"`
- JSON canonique : clés triées, sans espaces (deterministic)
- Headers envoyés : X-Webhook-Signature, X-Webhook-Timestamp, X-Webhook-Event-Id
- Vérification en temps constant : hmac.compare_digest

### Retry
1min → 5min → 15min → 1h → 6h. Après 5 échecs → FAILED.

---

## 9. SPÉCIFICATIONS DÉTAILLÉES DE MES FICHIERS

### 9.1 `app/api/whatsapp/twilio_webhook.py`

Point d'entrée des messages WhatsApp.

```python
# Endpoint : POST /whatsapp/incoming
# Appelé par Twilio à chaque message WhatsApp entrant

# Étapes :
# 1. Vérifier la signature Twilio (HMAC-SHA1) en production
#    - Utiliser twilio.request_validator.RequestValidator
#    - En dev/test : skip la vérification
# 2. Parser les paramètres Twilio : From, Body, NumMedia, MediaUrl0, MediaContentType0
# 3. Déléguer à WhatsAppBot.handle_incoming_message(from_number, body, media_url, media_type)
# 4. Retourner un TwiML vide (on a déjà répondu via l'API REST Twilio)

# Pourquoi pas répondre via TwiML :
# TwiML limite à 1 message par requête.
# Via l'API REST Twilio on peut envoyer plusieurs messages et des médias.
```

### 9.2 `app/services/whatsapp_bot.py`

Machine à états conversationnelle. Plus gros service du projet.

```python
# Machine à états (conversation_state dans la table applications) :
# WELCOME → COLLECT_NAME → COLLECT_PROGRAM → COLLECT_DOCS → WAITING_VALIDATION → DONE

# Point d'entrée :
# handle_incoming_message(from_number: str, body: str, media_url: str | None, media_type: str | None)
#   - Si média (media_url non null) :
#     → Enqueue process_incoming_media.delay(...)
#     → Répondre immédiatement "📥 Document reçu ! Traitement en cours..."
#   - Si texte :
#     → Récupérer ou créer l'application active pour ce numéro
#     → Consulter conversation_state
#     → Appeler le handler correspondant

# Handlers par état :
#
# _handle_welcome(application, message)
#   → Envoyer message de bienvenue avec présentation du processus
#   → Passer à COLLECT_NAME
#
# _handle_collect_name(application, message)
#   → Valider longueur nom (min 2 caractères)
#   → Sauvegarder student_name
#   → Passer à COLLECT_PROGRAM
#
# _handle_collect_program(application, message)
#   → Sauvegarder program
#   → Lister les documents requis
#   → Passer à COLLECT_DOCS
#
# _handle_collect_docs(application, message)
#   → Si message == "statut" : envoyer la liste des docs reçus/manquants
#   → Sinon : rappeler d'envoyer des photos/PDF
#
# _handle_waiting_validation(application, message)
#   → Répondre que le dossier est en cours de traitement
#   → Si message == "statut" : donner l'état actuel

# Fonctions utilitaires :
#
# send_message(to_number: str, message: str)
#   → Envoyer via twilio client.messages.create()
#   → Logger les erreurs sans crasher
#
# notify_decision(application, decision: str, comment: str | None)
#   → Si ACCEPTED : message de félicitations 🎉
#   → Si REJECTED : message avec raison
#
# _guess_document_type(caption: str | None) → str | None
#   → Mots-clés : "diplome"/"diplôme" → DIPLOME, "releve"/"relevé"/"notes" → RELEVE_NOTES,
#     "identite"/"identité"/"cni"/"carte" → CARTE_IDENTITE, "photo" → PHOTO
#   → Retourne None si aucun match
#
# _get_or_create_application(phone_number: str) → Application
#   → Chercher une application active (status != ACCEPTED/REJECTED) pour ce numéro
#   → Si aucune : en créer une nouvelle en status COLLECTING, state WELCOME
#   → CRITIQUE : ne pas créer de doublon pour le même numéro
```

### 9.3 `app/services/ocr_service.py`

Extraction de texte via Tesseract.

```python
# Fonction principale :
# extract_text(content: bytes, mime_type: str) → str
#
# Logique :
#   - Si mime_type commence par "application/pdf" :
#     → pdf2image.convert_from_bytes(content) pour convertir chaque page en image
#     → Maximum 20 pages (sécurité)
#     → Tesseract sur chaque image
#     → Concaténer les textes avec "\n\n--- Page X ---\n\n"
#
#   - Si mime_type commence par "image/" :
#     → PIL.Image.open(BytesIO(content))
#     → pytesseract.image_to_string(image, lang="fra+eng")
#
#   - Si fichier corrompu ou erreur :
#     → Logger l'erreur
#     → Retourner "" (chaîne vide, PAS de crash)
#
# Configuration Tesseract :
#   - Langue : "fra+eng" (français + anglais simultanément)
#   - PSM (Page Segmentation Mode) : par défaut (3 = auto)
#
# IMPORTANT : ce service est appelé DANS un worker Celery, pas dans une requête HTTP.
# Il peut prendre jusqu'à 10 secondes par document.
```

### 9.4 `app/services/ai_classifier.py`

Classification de documents par Anthropic Claude.

```python
# Fonction principale :
# classify(ocr_text: str, expected_type: str | None = None) → DocumentClassificationResult
#
# Court-circuit : si ocr_text est vide ou None → retourner immédiatement un résultat
# invalide sans appeler l'API (économie de tokens).
#
# Appel API :
#   - Client : anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
#   - Modèle : "claude-sonnet-4-20250514" (ou configurable)
#   - max_tokens : 1000
#
# SYSTEM_PROMPT (en français) :
# """
# Tu es un expert en analyse de documents universitaires.
# Tu reçois le texte extrait par OCR d'un document.
# Tu dois classifier ce document et évaluer sa validité.
#
# Réponds UNIQUEMENT en JSON avec cette structure exacte :
# {
#   "detected_type": "DIPLOME" | "RELEVE_NOTES" | "CARTE_IDENTITE" | "PHOTO" | "AUTRE",
#   "is_valid": true | false,
#   "confidence": 0.0 à 1.0,
#   "errors": ["erreur1", "erreur2"],
#   "summary": "résumé en une phrase"
# }
#
# Critères de validité :
# - Le document est lisible (texte OCR exploitable)
# - Le type correspond à ce qui est attendu
# - Les informations essentielles sont présentes (nom, date, institution)
# - Pas d'anomalie détectée (document tronqué, langue incohérente)
#
# Si le texte OCR est trop court ou incompréhensible, is_valid = false.
# """
#
# Message utilisateur : f"Texte OCR du document :\n\n{ocr_text}"
# Si expected_type : ajouter f"\n\nType attendu : {expected_type}"
#
# Parsing de la réponse :
#   - Extraire le JSON (gérer les blocs ```json ... ```)
#   - json.loads()
#   - Construire un DocumentClassificationResult (schéma Pydantic)
#
# Gestion d'erreur :
#   - Si API Anthropic indisponible ou erreur réseau :
#     → Logger l'erreur
#     → Retourner un résultat invalide : { detected_type: "AUTRE", is_valid: false, confidence: 0.0,
#       errors: ["Erreur du service de classification"], summary: "Classification impossible" }
#     → NE PAS crasher
#
# IMPORTANT : ce service est appelé DANS un worker Celery.
# L'appel Claude prend environ 2-5 secondes.
```

### 9.5 `app/services/validator.py`

Décision sur la complétude d'un dossier.

```python
# Fonction 1 :
# validate(application) → tuple[bool, float, list[str]]
#   Retourne (is_complete, score, raisons)
#
# Vérifications :
#   1. student_name est rempli et non vide
#   2. program est rempli et non vide
#   3. Pour chaque type requis (DIPLOME, RELEVE_NOTES, CARTE_IDENTITE, PHOTO) :
#      → Vérifier qu'au moins un document de ce type existe ET is_valid == True
#      → Si manquant : ajouter "Document manquant : {type}" aux raisons
#      → Si présent mais invalide : ajouter "Document invalide : {type}" aux raisons
#   4. Calculer score = moyenne des confidence de classification_result de tous les documents valides
#      → Si aucun document valide : score = 0.0
#
# Fonction 2 :
# apply_validation(application, db_session) → None
#   → Appeler validate(application)
#   → Mettre à jour application.validation_score et application.ai_notes
#   → Si is_complete :
#     → application.status = "VALIDATED"
#     → Sauvegarder
#     → C'est le signal pour déclencher l'envoi du webhook (fait par le worker)
#   → Sinon :
#     → Rester en COLLECTING
#     → Sauvegarder les raisons dans ai_notes
```

### 9.6 `app/workers/ocr_tasks.py`

Tâches OCR asynchrones dans la file `ocr`.

```python
# Tâche 1 :
# process_incoming_media(application_id, media_url, media_type, caption)
#   Queue : "ocr"
#   Retry : max 3, délai 30s
#
#   Étapes :
#   1. Télécharger le fichier depuis Twilio (media_url avec basic auth Twilio)
#   2. Appeler storage_service.upload_document(content, application_id, filename)
#   3. Créer la ligne Document en base :
#      - application_id, original_filename, mime_type, gcs_path
#      - document_type = _guess_document_type(caption) ou None
#   4. Enchaîner : run_ocr_task.delay(document_id)
#
# Tâche 2 :
# run_ocr_task(document_id)
#   Queue : "ocr"
#   Retry : max 3, délai 30s
#
#   Étapes :
#   1. Récupérer le Document depuis la base
#   2. Télécharger le fichier depuis GCS : storage_service.download_to_bytes(gcs_path)
#   3. Appeler ocr_service.extract_text(content, mime_type)
#   4. Sauvegarder ocr_text dans le Document
#   5. Enchaîner : classify_document_task.delay(document_id)  (dans ai_tasks.py)
```

### 9.7 `app/workers/ai_tasks.py`

Tâches IA asynchrones dans la file `ai`.

```python
# Tâche 1 :
# classify_document_task(document_id)
#   Queue : "ai"
#   Retry : max 3, délai 30s
#
#   Étapes :
#   1. Récupérer le Document depuis la base
#   2. Appeler ai_classifier.classify(document.ocr_text, document.document_type)
#   3. Mettre à jour le Document :
#      - document_type = result.detected_type (si pas déjà défini)
#      - classification_result = result.dict() (JSONB)
#      - is_valid = result.is_valid
#      - confidence = result.confidence
#      - validation_errors = result.errors (JSONB)
#   4. Sauvegarder
#   5. Enchaîner : check_application_completion_task.delay(document.application_id)
#
# Tâche 2 :
# check_application_completion_task(application_id)
#   Queue : "ai"
#   Retry : max 3, délai 30s
#
#   Étapes :
#   1. Récupérer l'Application avec tous ses Documents (joinedload)
#   2. Appeler validator.apply_validation(application, db_session)
#   3. Si application.status est passé à VALIDATED :
#      → Enchaîner : dispatch_validated_application_task.delay(application_id)
#        (cette tâche est dans webhook_tasks.py — Dev 1)
#   4. Sinon : ne rien faire (le dossier reste en COLLECTING)
```

---

## 10. SÉCURITÉ

### Authentification API
- Paire API Key (`univ_<32 chars>`) + API Secret (`sk_<64 chars>`)
- Stockés en bcrypt cost 12, JAMAIS en clair
- Préfixe de clé indexé (12 chars) pour lookup rapide
- Headers : X-API-Key et X-API-Secret

### Isolation multi-tenant
- TOUTES les requêtes filtrent par university_id
- Une université ne voit jamais les données d'une autre

### Sécurité des documents
- Stockés sur GCS, jamais sur disque local
- URLs signées temporaires (60 min)
- Whitelist MIME : PDF, JPEG, PNG, WEBP, HEIC
- Taille max : 10 Mo

### Rate limiting
- 100 req/h par API Key (Redis via slowapi)
- 60 req/s par IP (Nginx en prod)

---

## 11. CONFIGURATION CELERY

```python
# celery_app.py (Dev 1, mais je dois connaître la config)
# 3 files distinctes :
CELERY_TASK_ROUTES = {
    "app.workers.ocr_tasks.*": {"queue": "ocr"},
    "app.workers.ai_tasks.*": {"queue": "ai"},
    "app.workers.webhook_tasks.*": {"queue": "webhooks"},
}

# Config importante :
task_acks_late = True              # Redistribution si worker meurt
worker_max_tasks_per_child = 200   # Anti fuite mémoire
task_reject_on_worker_lost = True  # Protection supplémentaire
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
```

---

## 12. TESTS

### Environnement de test
- PostgreSQL remplacé par SQLite en mémoire
- Variables d'env simulées AVANT import de config
- Twilio, Anthropic et GCS JAMAIS appelés en test (mocks)

### Fixtures (conftest.py)
- `db_session` : session par test, rollback automatique
- `client` : TestClient FastAPI avec get_db remplacé
- `test_university` : université avec credentials connus
- `auth_headers` : headers prêts à l'emploi

### Ce que je dois tester
- Bot WhatsApp : tous les scénarios de conversation
- OCR : routage image vs PDF, fichier corrompu
- Classification IA : parsing JSON, gestion erreur API
- Validation : dossier complet, incomplet, documents invalides

---

## 13. MES TÂCHES — PLANNING JOUR PAR JOUR

### SEMAINE 1

**Jour 1 — Setup**
- [ ] Créer compte Twilio, activer bac à sable WhatsApp
- [ ] Installer dépendances IA (anthropic, pytesseract, Pillow, pdf2image)
- [ ] Créer endpoint POST /whatsapp/incoming (squelette)
- [ ] Tester réception d'un message WhatsApp

**Jour 2 — Bot basique**
- [ ] Implémenter routage messages dans le bot
- [ ] Implémenter envoi réponses via Twilio
- [ ] Commencer machine à états (WELCOME, COLLECT_NAME)
- [ ] Tester dialogue aller-retour complet

**Jour 3 — Médias + machine à états complète**
- [ ] Réception médias WhatsApp (Twilio Media API)
- [ ] Implémenter worker process_incoming_media
- [ ] Compléter machine à états (COLLECT_PROGRAM, COLLECT_DOCS)
- [ ] Tester envoi d'une image via WhatsApp

**Jour 4 — OCR**
- [ ] Implémenter ocr_service.py (Tesseract, images + PDF)
- [ ] Implémenter run_ocr_task
- [ ] Détection langue fra+eng
- [ ] Tester OCR sur différents types de documents

**Jour 5 — Consolidation**
- [ ] Feedback WhatsApp après OCR (document reçu / illisible)
- [ ] Tester bot bout en bout (candidature sans IA)
- [ ] Corriger bugs pipeline
- [ ] Préparer squelette ai_classifier.py

### SEMAINE 2

**Jour 6 — Classification IA**
- [ ] Implémenter ai_classifier.py (appel Claude, system prompt, parsing JSON)
- [ ] Implémenter classify_document_task
- [ ] Tester précision classification sur différents documents
- [ ] Connecter résultat au modèle Document

**Jour 7 — Validation + pipeline complet**
- [ ] Implémenter validator.py (complétude, score)
- [ ] Implémenter check_application_completion_task
- [ ] Notifications WhatsApp (dossier envoyé / incomplet)
- [ ] Tester pipeline complet : OCR → IA → validation → webhook

**Jour 8 — Boucle complète**
- [ ] Implémenter notify_decision dans le bot
- [ ] Messages félicitations / refus
- [ ] Réponse au mot-clé "statut"
- [ ] Tester boucle complète bout en bout

**Jour 9 — Tests**
- [ ] Tests bot WhatsApp (tous scénarios)
- [ ] Tests OCR et IA (documents variés)
- [ ] Tests gestion d'erreur et cas limites
- [ ] Correction bugs

**Jour 10 — Finalisation**
- [ ] Vérification bot en conditions réelles
- [ ] Scénario de démonstration
- [ ] Documenter dialogues types du bot
- [ ] S'assurer que les notifications fonctionnent

---

## 14. DÉPENDANCES SUR DEV 1

| Ce dont j'ai besoin | Quand | Si pas prêt |
|---------------------|-------|-------------|
| Modèles SQLAlchemy (University, Application, Document) | Jour 2 | Créer des modèles stub |
| database.py (get_db, SessionLocal) | Jour 2 | Stub avec SQLite local |
| celery_app.py configuré | Jour 3 | Config minimale moi-même |
| storage_service.py (upload/download GCS) | Jour 3 | Mock avec stockage local /tmp |
| Endpoint POST /applications/{id}/decision | Jour 8 | Tester notify_decision en isolation |

**Règle : si Dev 1 est en retard, je crée un mock/stub et j'avance. Je ne bloque jamais.**

---

## 15. CONVENTIONS DE CODE

- **Commits** : `feat(bot): ...`, `feat(ocr): ...`, `feat(ai): ...`, `fix(bot): ...`
- **Branches** : `feat/whatsapp-bot`, `feat/ocr-service`, `feat/ai-classifier`
- **Format réponse API** : `{ "success": true/false, "data"/"error": ... }`
- **Rien de lent en HTTP** : tout traitement > 200ms → Celery
- **Modèles ≠ Schémas ≠ Services** : ne jamais mélanger
- **SQLAlchemy synchrone** : pas d'async
- **JSONB** pour les données structurées (pas JSON)
- **UUID** pour les identifiants (pas auto-incrément)
- **Tests** : pytest, Tesseract et Twilio mockés
- **Gestion d'erreur** : logger et continuer, ne JAMAIS crasher l'API

---

## 16. TÂCHES V2 — DEV 3

### Phase 1 — Fondations doc
- [x] Mettre à jour CLAUDE.md (rôle Dev 3)
- [x] Squelette `docs/INTEGRATION.md` et `docs/IMPORT.md`

### Phase 2 — Swagger
- [x] Security schemes `X-API-Key` + `X-API-Secret` (`app/openapi.py`)
- [x] Tags : Admin, Applications, Documents, Decisions, Webhooks, WhatsApp, System
- [x] Pré-remplissage credentials via `SWAGGER_DEMO_API_KEY` / `SWAGGER_DEMO_API_SECRET`
- [x] Tests `tests/test_openapi.py`

### Phase 3 — Intégration
- [x] Collection Postman v2 (`docs/postman/`)
- [ ] Valider import Boussole.in (bloqué : script Dev 1)
- [ ] Finaliser INTEGRATION.md quand endpoints admin livrés

### Phase 4 — Finalisation
- [ ] Mettre à jour diagrammes séquences / cas d'utilisation v2
- [ ] Cahier des charges technique v2

### Dépendances Dev 3

| Tâche bloquée | Dépend de |
|---------------|-----------|
| Postman scénario complet | Endpoints admin (Dev 1) |
| IMPORT.md procédure réelle | `scripts/import_boussole.py` (Dev 1) |
| Doc formulaire publié | Tables `admission_forms`, `form_fields` (Dev 1) |
| Tests bout en bout doc | Seed endpoint + bot v2 stable (Dev 1 + Dev 2) |