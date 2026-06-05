# Script de démonstration — Dev 2 (Bot WhatsApp + IA)

> Document de référence pour la démo Jour 10.
> Tous les messages WhatsApp sont simulés via `curl` sur l'endpoint Twilio local.
> Durée estimée : 10-15 minutes.

---

## 1. Prérequis avant la démo

### Variables d'environnement (`.env`)
```
AI_PROVIDER=gemini
GOOGLE_AI_API_KEY=<ta_cle_google_ai_studio>
DEMO_MODE=false
AI_MOCK=false           # true si pas de clé Gemini le jour J
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

> **Pas de clé Gemini ?** Mettre `AI_MOCK=true` — le bot fonctionne avec des
> résultats simulés réalistes, le pipeline complet est démontrable.

### Numéro de téléphone "étudiant" pour la démo
```
STUDENT_PHONE=whatsapp:+22890123456
```
Utiliser ce numéro dans tous les curl ci-dessous.

---

## 2. Lancer le projet

```bash
# Construire et démarrer tous les services
docker compose up --build -d

# Vérifier que tout est healthy
docker compose ps

# Appliquer les migrations
docker compose exec api alembic upgrade head
```

Résultat attendu :
```
admission_postgres    running (healthy)
admission_redis       running (healthy)
admission_api         running
admission_worker_ocr  running
admission_worker_ai   running
admission_worker_webhook running
```

---

## 3. Seeder les données de démo

### 3.1 Créer une université
```bash
docker compose exec api python scripts/create_university.py \
  --name "Université de Lomé" \
  --email "admin@univ-lome.tg" \
  --webhook-url "https://webhook.site/ton-id-unique"
```

> Copier et sauvegarder l'API Key, l'API Secret et le Webhook Secret affichés.

### 3.2 Seeder des programmes (avec domaine + période d'inscription)

> Chaque `Program` porte un `domain` (alimente la liste de domaines du bot) et,
> optionnellement, une période `enrollment_start` / `enrollment_end`.
> Ici on crée **2 programmes** : un avec inscriptions **ouvertes**, un avec
> inscriptions **fermées** (pour démontrer le scénario d'attente).
>
> Les documents requis suivent le modèle Dev 1 (`Program → AdmissionForm →
> RequiredDocument`). Sans `AdmissionForm` publié, le validator retombe sur les
> 4 types par défaut (DIPLOME, RELEVE_NOTES, CARTE_IDENTITE, PHOTO) — suffisant
> pour la démo. Pour des documents précis, utiliser l'API admin de Dev 1 ou l'import Boussole.

```bash
docker compose exec api python -c "
from datetime import date, timedelta
from app.database import SessionLocal
from app.models.university import University
from app.models.program import Program
import uuid

db = SessionLocal()

univ = db.query(University).filter_by(name='Université de Lomé').first()
print(f'Université : {univ.id}')

# Programme 1 : inscriptions OUVERTES (domaine Informatique)
prog_open = Program(
    id=uuid.uuid4(), university_id=univ.id,
    name='Licence Informatique', domain='Informatique', is_active=True,
    enrollment_start=date.today() - timedelta(days=10),
    enrollment_end=date.today() + timedelta(days=30),
)
db.add(prog_open)

# Programme 2 : inscriptions FERMÉES (domaine Médecine, ouvre dans 15 jours)
prog_closed = Program(
    id=uuid.uuid4(), university_id=univ.id,
    name='Médecine Générale', domain='Médecine', is_active=True,
    enrollment_start=date.today() + timedelta(days=15),
    enrollment_end=date.today() + timedelta(days=60),
)
db.add(prog_closed)

db.commit()
print(f'Programme OUVERT  : {prog_open.name} ({prog_open.domain})')
print(f'Programme FERMÉ   : {prog_closed.name} ({prog_closed.domain})')
db.close()
"
```

### 3.3 Vérifier l'état initial
```bash
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT name, is_active FROM universities;"

docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT name, domain, enrollment_start, enrollment_end FROM programs ORDER BY domain;"

# Documents requis (modèle Dev 1 : via admission_forms → required_documents)
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT p.name, rd.document_type, rd.\"order\"
   FROM programs p
   JOIN admission_forms f ON f.program_id = p.id
   JOIN required_documents rd ON rd.form_id = f.id
   ORDER BY p.name, rd.\"order\";"
```

---

## 4. Scénario de démonstration

> **Convention curl** : toutes les requêtes simulent ce que Twilio envoie
> à `POST /whatsapp/incoming`. Le bot répond via l'API Twilio (ou la console
> en DEMO_MODE).

Exporter l'URL de base une fois :
```bash
export API=http://localhost:8000
export FROM="whatsapp:+22890123456"
export TO="whatsapp:+14155238886"
```

---

### Étape 1 — Premier contact (état : WELCOME → COLLECT_INTEREST)

**Message étudiant :**
```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=Bonjour&NumMedia=0"
```

**Réponse bot attendue :**
```
Bonjour ! 👋 Je suis l'assistant d'admission universitaire.

Dans quel domaine souhaitez-vous poursuivre vos études ?

1. Informatique
2. Médecine

Répondez avec le numéro de votre choix.
```
*(Le bot demande d'abord le **domaine**. Les domaines viennent de `Program.domain`.
Si aucun domaine n'est configuré : le bot liste directement les universités.)*

**État DB attendu :**
```sql
SELECT student_phone, status, conversation_state
FROM applications ORDER BY created_at DESC LIMIT 1;
-- status: COLLECTING | conversation_state: COLLECT_INTEREST
```

---

### Étape 2 — Choix du domaine (état : COLLECT_INTEREST → CHOOSE_UNIVERSITY)

```bash
# "1" = Informatique (inscriptions ouvertes)
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=1&NumMedia=0"
```

**Réponse bot attendue :**
```
Voici les universités qui proposent des formations en Informatique :

1. Université de Lomé

Répondez avec le numéro de votre choix.
```
*(Si une seule université correspond au domaine : sélection auto, le bot demande
directement le nom et passe à COLLECT_NAME.)*

**État DB :**
```sql
SELECT conversation_state, ai_notes FROM applications
ORDER BY created_at DESC LIMIT 1;
-- conversation_state: CHOOSE_UNIVERSITY | ai_notes: Informatique (domaine mémorisé)
-- (ou COLLECT_NAME si auto-sélection)
```

---

### Étape 3 — Choix de l'université (état : CHOOSE_UNIVERSITY → COLLECT_NAME)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=1&NumMedia=0"
```

**Réponse bot attendue :**
```
✅ Université de Lomé sélectionnée !

Quel est votre nom complet ?
```

**État DB :**
```sql
SELECT conversation_state, university_id FROM applications
ORDER BY created_at DESC LIMIT 1;
-- conversation_state: COLLECT_NAME
```

---

### Étape 4 — Nom de l'étudiant (état : COLLECT_NAME → CHOOSE_PROGRAM)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=Kofi+Mensah&NumMedia=0"
```

> **Validation** : un nom avec chiffres (`Kofi123`) ou sans lettres est rejeté
> avec un message explicatif. L'étudiant reste en COLLECT_NAME.

**Réponse bot attendue :**
```
Enchanté Kofi Mensah ! 🎓

Quel programme souhaitez-vous intégrer ?

1. Licence Informatique

Répondez avec le numéro de votre choix.
```

**État DB :**
```sql
SELECT student_name, conversation_state FROM applications
ORDER BY created_at DESC LIMIT 1;
-- student_name: Kofi Mensah | conversation_state: CHOOSE_PROGRAM
```

---

### Étape 5 — Choix du programme (état : CHOOSE_PROGRAM → COLLECT_DOCS)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=1&NumMedia=0"
```

**Réponse bot attendue (inscriptions ouvertes) :**
```
✅ Programme Licence Informatique sélectionné !

Envoyez vos documents un par un. Commençons par
votre *diplôme* (ou attestation du baccalauréat).

📎 En photo ou PDF directement dans cette conversation.
```

> ⚠️ **Si le programme choisi a ses inscriptions fermées**, le bot bifurque vers
> le scénario de la section **4 bis** ci-dessous au lieu de COLLECT_DOCS.

**État DB :**
```sql
SELECT program, conversation_state FROM applications
ORDER BY created_at DESC LIMIT 1;
-- program: Licence Informatique | conversation_state: COLLECT_DOCS
```

---

### Étape 6 — Envoi du diplôme (état : COLLECT_DOCS)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=diplome&NumMedia=1" \
  -d "MediaUrl0=https://sample-files.com/samples/images/jpg/sample-birch-400x300.jpg" \
  -d "MediaContentType0=image/jpeg"
```

**Réponse immédiate du bot :**
```
📥 Document reçu ! Je l'analyse, vous recevrez un retour dans quelques minutes.
```

**Logs workers à surveiller :**
```bash
# Terminal 1 — OCR
docker compose logs -f worker-ocr

# Terminal 2 — IA
docker compose logs -f worker-ai
```

**Logs attendus (worker-ocr) :**
```
[INFO] process_incoming_media: téléchargement Twilio OK
[INFO] Document uploadé sur GCS: applications/.../...jpg
[INFO] run_ocr_task: OCR extrait XX caractères
```

**Logs attendus (worker-ai) :**
```
[INFO] classify_document_task: classification OK (DIPLOME, confidence=0.94)
[INFO] WhatsApp envoyé à whatsapp:+22890123456 (sid=...)
```

**Feedback WhatsApp automatique :**
```
✅ Votre diplôme (ou attestation du baccalauréat) validé !

Envoyez maintenant votre *relevé de notes*.
```

**État DB :**
```sql
SELECT d.document_type, d.is_valid, d.classification_result->>'confidence' as conf
FROM documents d
JOIN applications a ON a.id = d.application_id
ORDER BY d.uploaded_at DESC LIMIT 1;
-- document_type: DIPLOME | is_valid: true | conf: 0.94
```

---

### Étape 7 — Envoi des 3 documents restants

Répéter le même curl pour relevé de notes, carte d'identité et photo :

```bash
# Relevé de notes
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=releve&NumMedia=1" \
  -d "MediaUrl0=https://sample-files.com/samples/images/jpg/sample-birch-400x300.jpg" \
  -d "MediaContentType0=image/jpeg"

# Attendre le feedback ✅, puis...

# Carte d'identité
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=carte&NumMedia=1" \
  -d "MediaUrl0=https://sample-files.com/samples/images/jpg/sample-birch-400x300.jpg" \
  -d "MediaContentType0=image/jpeg"

# Photo
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=photo&NumMedia=1" \
  -d "MediaUrl0=https://sample-files.com/samples/images/jpg/sample-birch-400x300.jpg" \
  -d "MediaContentType0=image/jpeg"
```

---

### Étape 8 — Dossier complet et transmis (état : VALIDATED)

Quand le 4ème document est validé, le bot envoie automatiquement :
```
🎉 Votre dossier est complet et a été transmis à l'université !

Vous recevrez une réponse ici dès qu'une décision sera prise. 🙏
```

**État DB complet :**
```sql
SELECT
  a.student_name,
  a.program,
  a.status,
  a.validation_score,
  COUNT(d.id) AS nb_docs,
  SUM(CASE WHEN d.is_valid THEN 1 ELSE 0 END) AS docs_valides
FROM applications a
LEFT JOIN documents d ON d.application_id = a.id
GROUP BY a.id
ORDER BY a.created_at DESC LIMIT 1;
-- status: VALIDATED | validation_score: ~0.92 | nb_docs: 4 | docs_valides: 4
```

**Webhook envoyé à l'université :**
```bash
# Vérifier dans webhook_deliveries
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT event_type, status, attempt_count
   FROM webhook_deliveries ORDER BY created_at DESC LIMIT 1;"
-- event_type: application.validated | status: SUCCESS
```

---

### Étape 9 — Décision de l'université (via l'API REST)

```bash
# Récupérer l'ID de la candidature
APP_ID=$(docker compose exec postgres psql -U admission -d admission_db -t -c \
  "SELECT id FROM applications ORDER BY created_at DESC LIMIT 1;" | tr -d ' \n')

# L'université envoie sa décision (requiert X-API-Key + X-API-Secret)
curl -s -X POST "$API/api/v1/applications/$APP_ID/decision" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <api_key_de_l_universite>" \
  -H "X-API-Secret: <api_secret_de_l_universite>" \
  -d '{"decision": "ACCEPTED", "comment": "Dossier excellent, bienvenue !"}'
```

**Notification WhatsApp finale :**
```
🎉 *Félicitations !* Votre candidature a été acceptée.

💬 Commentaire de l'université :
Dossier excellent, bienvenue !
```

**État DB final :**
```sql
SELECT status, decision_comment, decided_at
FROM applications ORDER BY created_at DESC LIMIT 1;
-- status: ACCEPTED
```

---

### Étape bonus A — Commande "statut" en cours de route

À n'importe quel moment pendant la collecte (interceptée globalement) :
```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=statut&NumMedia=0"
```

**Réponse attendue :**
```
📋 *État de votre dossier* :

✅ DIPLOME
⏳ RELEVE_NOTES
⏳ CARTE_IDENTITE
⏳ PHOTO

Envoyez les documents manquants pour finaliser votre candidature.
```

### Étape bonus B — Commande "aide" (aide contextuelle)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=aide&NumMedia=0"
```

**Réponse attendue (adaptée à l'état courant)** — ex. en COLLECT_DOCS :
```
ℹ️ *Aide* :

Envoyez vos documents en photo ou PDF directement ici.
Tapez statut pour voir les documents déjà reçus et ceux qui manquent.
```

### Étape bonus C — Réponse invalide à un choix numéroté

```bash
# L'étudiant tape du texte au lieu d'un numéro à l'étape du domaine
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=je+veux+informatique&NumMedia=0"
```

**Réponse attendue :**
```
Je n'ai pas compris « je veux informatique ». Répondez avec un numéro entre 1 et 2.

1. Informatique
2. Médecine
```

---

## 4 bis. Scénario — Inscriptions fermées

> Ce scénario démontre le cas où le programme choisi n'est pas encore en
> période d'inscription. Utiliser un **nouveau numéro** d'étudiant pour repartir
> à zéro, et choisir le domaine *Médecine* (programme fermé seedé en 3.2).

```bash
export FROM2="whatsapp:+22890999888"

# 1. Démarrage → liste des domaines
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=Bonjour&NumMedia=0"

# 2. Choix du domaine Médecine (= "2")
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=2&NumMedia=0"

# 3. Choix de l'université (= "1") — auto si une seule
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=1&NumMedia=0"

# 4. Nom
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=Ama+Koffi&NumMedia=0"

# 5. Choix du programme Médecine Générale (= "1") → inscriptions FERMÉES
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=1&NumMedia=0"
```

**Réponse bot attendue à l'étape 5 :**
```
⚠️ Les inscriptions pour Médecine Générale ne sont pas encore ouvertes.

📅 Ouverture : 20/06/2026
📅 Fermeture : 04/08/2026

Que souhaitez-vous faire ?

1️⃣ Je reviendrai pendant la période d'inscription
2️⃣ Je dépose ma candidature maintenant (elle sera envoyée à l'université dès l'ouverture)
```

**État DB :**
```sql
SELECT conversation_state FROM applications WHERE student_phone = '+22890999888';
-- conversation_state: AWAITING_ENROLLMENT_CHOICE
```

### Option 1 — Reporter

```bash
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=1&NumMedia=0"
```
**Réponse :**
```
D'accord ! Revenez pendant la période d'inscription pour postuler. 😊

📅 Les inscriptions ouvrent le 20/06/2026.

Envoyez Bonjour quand vous êtes prêt(e).
```
**État DB :** la candidature est **supprimée**.
```sql
SELECT COUNT(*) FROM applications WHERE student_phone = '+22890999888';
-- 0
```

### Option 2 — Déposer maintenant (dépôt anticipé)

```bash
curl -s -X POST "$API/whatsapp/incoming" -d "From=$FROM2&To=$TO&Body=2&NumMedia=0"
```
**Réponse :**
```
✅ Votre candidature sera transmise à l'université dès l'ouverture des inscriptions.

Constituons votre dossier dès maintenant. Commençons par votre *diplôme*...
```

L'étudiant envoie ensuite ses 4 documents (comme en étapes 6-7). Une fois le
dossier complet, **au lieu de partir directement**, il passe en attente :

**État DB après dossier complet :**
```sql
SELECT status FROM applications WHERE student_phone = '+22890999888';
-- status: PENDING_ENROLLMENT
```

**Notification WhatsApp reçue :**
```
✅ Vos documents ont tous été validés, votre dossier est complet !

🗓️ Les inscriptions ne sont pas encore ouvertes. Votre candidature sera
automatiquement envoyée à l'université dès l'ouverture.
```

### Déclenchement automatique à l'ouverture

La tâche **Celery Beat quotidienne** `check_enrollment_periods` (6h00) détecte
l'ouverture et envoie les candidatures en attente. Pour la **tester
immédiatement** en démo :

```bash
docker compose exec worker-ai python -c "
from app.workers.enrollment_tasks import check_enrollment_periods_task
print(check_enrollment_periods_task.run())
"
# → dispatched:N   (N = nb de candidatures envoyées dont la période est ouverte)
```

> Pour forcer l'ouverture en démo, re-seeder le programme Médecine avec
> `enrollment_start=date.today()` puis relancer la commande ci-dessus.

**Notification WhatsApp envoyée à l'étudiant :**
```
🎉 Bonne nouvelle ! Les inscriptions pour votre programme sont maintenant ouvertes.

Votre candidature est en cours de traitement et sera envoyée à l'université
dans les prochaines minutes. 🚀
```

**État DB final :** `PENDING_ENROLLMENT` → `VALIDATED` → `SENT_TO_UNIVERSITY`.

---

## 5. Commandes essentielles le jour J

```bash
# Voir tous les logs en temps réel
docker compose logs -f api worker-ocr worker-ai

# Redémarrer un worker sans tout couper
docker compose restart worker-ocr

# Voir les tâches Celery en file
docker compose exec redis redis-cli llen celery

# Vérifier l'état d'une application spécifique
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT id, student_name, program, status, conversation_state
   FROM applications ORDER BY created_at DESC LIMIT 5;"

# Voir tous les documents d'une candidature
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT document_type, is_valid, classification_result->>'confidence'
   FROM documents WHERE application_id = '<uuid>';"

# Réinitialiser la démo (supprimer toutes les candidatures)
docker compose exec postgres psql -U admission -d admission_db -c \
  "TRUNCATE documents, webhook_deliveries, applications RESTART IDENTITY CASCADE;"

# Lancer les tests
docker compose exec api python -m pytest tests/ -v

# Health check API
curl http://localhost:8000/health
```

---

## 6. Résolution des problèmes fréquents

| Symptôme | Cause probable | Solution |
|----------|---------------|----------|
| Bot ne répond pas | Worker OCR/AI éteint | `docker compose restart worker-ocr worker-ai` |
| `❌ Document non valide` systématique | Image trop petite / OCR vide | Utiliser une vraie image avec du texte, ou mettre `AI_MOCK=true` |
| `Aucune université disponible` | Pas de seed | Relancer l'étape 3 |
| Bot liste les universités au lieu des domaines | `Program.domain` non renseigné | Re-seeder avec `domain=...` (étape 3.2) |
| Candidature bloquée en `PENDING_ENROLLMENT` | Inscriptions encore fermées | Normal — lancer `check_enrollment_periods_task` ou avancer `enrollment_start` |
| Webhook non reçu | `webhook_url` incorrecte | Vérifier dans `universities`, utiliser webhook.site |
| `503 Service Unavailable` | API pas encore démarrée | Attendre 10s et réessayer |

---

## 7. Dépendances bloquantes Dev 1

> Si ces éléments ne sont pas livrés avant la démo, utiliser le workaround.

> Détail complet et à jour dans `docs/STUBS_DEV1.md`.

| Besoin | Statut | Workaround |
|--------|--------|-----------|
| Migration `0002` : tables formulaires dynamiques (`programs`, `admission_forms`, `form_fields`, `required_documents`, `application_field_values`) | ⏳ À créer | `Base.metadata.create_all` (workaround ci-dessous) |
| Migration `0003` : `programs.enrollment_start` / `enrollment_end` | ⏳ À créer | idem |
| Migration `0004` : `PENDING_ENROLLMENT` dans `application_status_enum` | ⏳ À créer | En SQLite (démo) l'enum est libre ; en PostgreSQL `ALTER TYPE ... ADD VALUE` |
| Enregistrer `check_enrollment_periods` dans Celery Beat (`crontab(hour=6, minute=0)`) | ⏳ À créer | Lancer la tâche à la main (cf. 4 bis) |
| Seeding programmes en production | ⏳ À définir | Script Python de l'étape 3.2 |

### Activer le workaround migrations (si Dev 1 n'a pas livré)

```bash
# Crée toutes les tables connues des modèles directement (bypasse Alembic)
docker compose exec api python -c "
import app.models  # importe tous les modèles dans Base.metadata
from app.database import engine, Base
Base.metadata.create_all(engine)
print('Tables créées depuis les modèles.')
"
```

### Ajouter PENDING_ENROLLMENT à l'enum PostgreSQL (si Dev 1 n'a pas livré)

```bash
docker compose exec postgres psql -U admission -d admission_db -c \
  "ALTER TYPE application_status_enum ADD VALUE IF NOT EXISTS 'PENDING_ENROLLMENT';"
```

---

## 8. Récapitulatif des fichiers Dev 2

| Fichier | Rôle | Couverture tests |
|---------|------|-----------------|
| `app/api/whatsapp/twilio_webhook.py` | Endpoint Twilio | 94% |
| `app/services/whatsapp_bot.py` | Machine à états : domaine → université → programme → docs, validation des saisies, périodes d'inscription | 85% |
| `app/services/ai_classifier.py` | Gemini Flash + Anthropic | 91% |
| `app/services/ocr_service.py` | Tesseract OCR | 94% |
| `app/services/validator.py` | Validation dynamique (Program → AdmissionForm → RequiredDocument) + fallback 4 types | 94% |
| `app/workers/ocr_tasks.py` | Pipeline OCR async | 95% |
| `app/workers/ai_tasks.py` | Pipeline IA async + feedback WhatsApp + bascule PENDING_ENROLLMENT | 95% |
| `app/workers/enrollment_tasks.py` | Tâche Beat quotidienne d'ouverture des inscriptions | — |

> Modèles (`Program`, `AdmissionForm`, `RequiredDocument`, `Application`) = périmètre Dev 1.
> Dev 2 y a seulement ajouté `enrollment_start/end` + `is_enrollment_open()` sur `Program`
> et `PENDING_ENROLLMENT` sur `ApplicationStatus`.

**Total tests : 164, 0 échec.**

### Flow conversationnel complet (résumé)

```
WELCOME
  → COLLECT_INTEREST      (choix du domaine, liste numérotée)
  → CHOOSE_UNIVERSITY     (universités filtrées par domaine)
  → COLLECT_NAME          (validation : lettres, pas de chiffres)
  → CHOOSE_PROGRAM        (ou COLLECT_PROGRAM en texte libre)
       │
       ├─ inscriptions ouvertes → COLLECT_DOCS
       └─ inscriptions fermées  → AWAITING_ENROLLMENT_CHOICE
              ├─ 1. reporter        → candidature supprimée
              └─ 2. déposer         → COLLECT_DOCS (envoi différé)
  → COLLECT_DOCS          (documents un par un, feedback ✅/❌)
  → VALIDATED              (inscriptions ouvertes → webhook immédiat)
     ou PENDING_ENROLLMENT (inscriptions fermées → webhook à l'ouverture)
  → SENT_TO_UNIVERSITY → ACCEPTED / REJECTED

Commandes globales (tout état) : statut, aide/help
```
