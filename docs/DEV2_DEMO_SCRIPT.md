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

### 3.2 Seeder un programme et ses documents requis
```bash
docker compose exec api python -c "
from app.database import SessionLocal
from app.models.university import University
from app.models.program import Program
from app.models.required_document import RequiredDocument
from app.models.document import DocumentType
import uuid

db = SessionLocal()

# Récupérer l'université
univ = db.query(University).filter_by(name='Université de Lomé').first()
print(f'Université : {univ.id}')

# Créer le programme
prog = Program(
    id=uuid.uuid4(),
    university_id=univ.id,
    name='Licence Informatique',
    is_active=True,
)
db.add(prog)
db.flush()

# Définir les 4 documents requis dans l'ordre
for i, (doc_type, label) in enumerate([
    (DocumentType.DIPLOME,        'Diplôme du baccalauréat'),
    (DocumentType.RELEVE_NOTES,   'Relevé de notes'),
    (DocumentType.CARTE_IDENTITE, 'Carte d\\'identité'),
    (DocumentType.PHOTO,          'Photo d\\'identité'),
]):
    db.add(RequiredDocument(
        id=uuid.uuid4(),
        program_id=prog.id,
        document_type=doc_type,
        is_required=True,
        label=label,
        order=i,
    ))

db.commit()
print(f'Programme créé : {prog.name} ({prog.id})')
print('4 documents requis configurés.')
db.close()
"
```

### 3.3 Vérifier l'état initial
```bash
docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT name, is_active FROM universities;"

docker compose exec postgres psql -U admission -d admission_db -c \
  "SELECT p.name, rd.document_type, rd.order
   FROM programs p JOIN required_documents rd ON rd.program_id = p.id
   ORDER BY rd.order;"
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

### Étape 1 — Premier contact (état : WELCOME)

**Message étudiant :**
```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=Bonjour&NumMedia=0"
```

**Réponse bot attendue :**
```
Bonjour ! 👋 Dans quelle université souhaitez-vous postuler ?

1. Université de Lomé

Répondez avec le numéro de votre choix.
```
*(Si 1 seule université : sélection auto, demande directement le nom)*

**État DB attendu :**
```sql
SELECT student_phone, status, conversation_state
FROM applications ORDER BY created_at DESC LIMIT 1;
-- status: COLLECTING | conversation_state: CHOOSE_UNIVERSITY (ou COLLECT_NAME)
```

---

### Étape 2 — Choix de l'université (état : CHOOSE_UNIVERSITY)

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

### Étape 3 — Nom de l'étudiant (état : COLLECT_NAME)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=Kofi+Mensah&NumMedia=0"
```

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

### Étape 4 — Choix du programme (état : CHOOSE_PROGRAM)

```bash
curl -s -X POST "$API/whatsapp/incoming" \
  -d "From=$FROM&To=$TO&Body=1&NumMedia=0"
```

**Réponse bot attendue :**
```
✅ Programme Licence Informatique sélectionné !

Envoyez vos documents un par un. Commençons par
votre *diplôme* (ou attestation du baccalauréat).

📎 En photo ou PDF directement dans cette conversation.
```

**État DB :**
```sql
SELECT program, conversation_state FROM applications
ORDER BY created_at DESC LIMIT 1;
-- program: Licence Informatique | conversation_state: COLLECT_DOCS
```

---

### Étape 5 — Envoi du diplôme (état : COLLECT_DOCS)

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

### Étape 6 — Envoi des 3 documents restants

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

### Étape 7 — Dossier complet et transmis (état : VALIDATED)

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

### Étape 8 — Décision de l'université (via l'API REST)

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

### Étape bonus — Commande "statut" en cours de route

À n'importe quel moment pendant la collecte :
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
| Webhook non reçu | `webhook_url` incorrecte | Vérifier dans `universities`, utiliser webhook.site |
| `503 Service Unavailable` | API pas encore démarrée | Attendre 10s et réessayer |

---

## 7. Dépendances bloquantes Dev 1

> Si ces éléments ne sont pas livrés avant la démo, utiliser le workaround.

| Besoin | Statut | Workaround |
|--------|--------|-----------|
| Migration `0002_add_programs.py` | ⏳ À créer | Créer la table manuellement via le script Python de l'étape 3.2 |
| Migration `0003_add_required_documents.py` | ⏳ À créer | Idem — le script Python crée les tables via `Base.metadata.create_all` |
| Seeding programmes en production | ⏳ À définir | Script Python de l'étape 3.2 suffit pour la démo |

### Activer le workaround migrations (si Dev 1 n'a pas livré)

```bash
# Créer les tables stubs directement depuis Python (bypasse Alembic)
docker compose exec api python -c "
from app.database import engine, Base
from app.models.program import Program
from app.models.required_document import RequiredDocument
Base.metadata.create_all(engine, tables=[
    Program.__table__,
    RequiredDocument.__table__,
])
print('Tables programs et required_documents créées.')
"
```

---

## 8. Récapitulatif des fichiers Dev 2

| Fichier | Rôle | Couverture tests |
|---------|------|-----------------|
| `app/api/whatsapp/twilio_webhook.py` | Endpoint Twilio | 94% |
| `app/services/whatsapp_bot.py` | Machine à états + flow université/programme | 85% |
| `app/services/ai_classifier.py` | Gemini Flash + Anthropic | 91% |
| `app/services/ocr_service.py` | Tesseract OCR | 94% |
| `app/services/validator.py` | Validation dynamique via RequiredDocument | 94% |
| `app/workers/ocr_tasks.py` | Pipeline OCR async | 95% |
| `app/workers/ai_tasks.py` | Pipeline IA async + feedback WhatsApp | 95% |
| `app/models/program.py` | Stub Programme *(TODO migration Dev 1)* | — |
| `app/models/required_document.py` | Stub Documents requis *(TODO migration Dev 1)* | — |

**Total tests Dev 2 : 128 tests, 0 échec.**
