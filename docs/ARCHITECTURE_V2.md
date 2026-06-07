# Architecture & cahier des charges — v2

> Diagrammes de séquence et spécification de la version 2 (programmes,
> formulaires dynamiques, périodes d'inscription). Reflète le code **réellement
> implémenté** au 2026-06-07, pas une cible théorique.
>
> Complète `CLAUDE.md` (source de vérité produit) et `docs/STUBS_DEV1.md`
> (état migrations / infra).

---

## 1. Ce qui change en v2 (delta v1 → v2)

| Domaine | v1 | v2 |
|---------|----|----|
| Choix programme | texte libre (`COLLECT_PROGRAM`) | orientation par **domaine d'intérêt** → liste d'**universités** filtrées → liste de **programmes** (`programs`) ; le texte libre reste en fallback |
| Formulaire | champs fixes (nom, programme) | **formulaire dynamique** par programme (`admission_forms` / `form_fields`) |
| Documents requis | liste codée en dur | configurables par formulaire (`required_documents`), fallback liste ordonnée codée |
| Inscriptions | toujours ouvertes | **périodes d'inscription** par programme (`enrollment_start` / `enrollment_end`) + statut `PENDING_ENROLLMENT` + tâche Beat quotidienne |
| Statuts candidature | `COLLECTING … REJECTED` | + `CHOOSING_UNIVERSITY`, `CHOOSING_PROGRAM`, `COLLECTING_FIELDS`, `COLLECTING_DOCUMENTS`, `PENDING_ENROLLMENT` |
| Classification IA | Anthropic Claude | Anthropic **ou** Google Gemini (`AI_PROVIDER`, défaut `anthropic`) |
| Données | — | import des universités **Boussole.in** (`scripts/import_boussole.py`) |

---

## 2. Machine à états de la conversation WhatsApp (bot v2)

États : `app/services/whatsapp_bot.py::ConversationState`.

```mermaid
stateDiagram-v2
    [*] --> WELCOME
    WELCOME --> COLLECT_INTEREST : 1er message
    COLLECT_INTEREST --> CHOOSE_UNIVERSITY : domaine choisi (liste numérotée)
    CHOOSE_UNIVERSITY --> COLLECT_NAME : université choisie
    COLLECT_NAME --> CHOOSE_PROGRAM : nom saisi (≥ 2 car.)
    CHOOSE_PROGRAM --> AWAITING_ENROLLMENT_CHOICE : programme hors période
    CHOOSE_PROGRAM --> COLLECT_DOCS : programme ouvert
    COLLECT_PROGRAM --> COLLECT_DOCS : (fallback texte libre)
    AWAITING_ENROLLMENT_CHOICE --> COLLECT_DOCS : « déposer maintenant »
    AWAITING_ENROLLMENT_CHOICE --> [*] : « revenir plus tard » (candidature supprimée)
    COLLECT_DOCS --> WAITING_VALIDATION : tous les documents reçus
    WAITING_VALIDATION --> DONE : dossier validé / décidé
    DONE --> [*]

    note right of COLLECT_DOCS
        mot-clé « statut » à tout moment
        → liste docs reçus / manquants
    end note
```

---

## 3. Pipeline de validation d'un document (OCR → IA → validation)

Déclenché à chaque média reçu. Chaque worker enchaîne le suivant.

```mermaid
sequenceDiagram
    autonumber
    participant E as Étudiant (WhatsApp)
    participant T as Twilio
    participant W as /whatsapp/incoming
    participant B as WhatsAppBot
    participant Q1 as Worker OCR
    participant Q2 as Worker IA
    participant V as validator
    participant Q3 as Worker Webhook
    participant U as Université

    E->>T: envoie une photo / PDF
    T->>W: POST (média)
    W->>B: handle_incoming_message(...)
    B-->>E: « 📥 Document reçu, traitement… »
    B->>Q1: process_incoming_media.delay()
    Q1->>Q1: download Twilio → upload GCS → crée Document
    Q1->>Q1: run_ocr_task (Tesseract fra+eng)
    Q1->>Q2: classify_document_task.delay(doc_id)
    Q2->>Q2: ai_classifier.classify() → type, is_valid, confidence
    Q2->>V: check_application_completion_task → apply_validation()
    alt dossier complet ET inscriptions ouvertes
        V-->>Q3: status=VALIDATED → dispatch_validated_application_task
        Q3->>U: POST webhook signé HMAC-SHA256 (application.validated)
    else dossier complet MAIS inscriptions fermées
        V->>V: status = PENDING_ENROLLMENT
        V-->>E: « inscriptions fermées, on vous prévient à l'ouverture »
    else dossier incomplet
        V-->>E: prochain document manquant
    end
```

---

## 4. Périodes d'inscription (tâche planifiée)

`check_enrollment_periods_task` — Celery Beat, tous les jours à 06h00 UTC
(`app/workers/celery_app.py::beat_schedule`).

```mermaid
sequenceDiagram
    autonumber
    participant Beat as Celery Beat
    participant Task as check_enrollment_periods
    participant DB as PostgreSQL
    participant Q3 as Worker Webhook
    participant E as Étudiant

    Beat->>Task: déclenche (06h00 UTC)
    Task->>DB: SELECT applications WHERE status = PENDING_ENROLLMENT
    loop pour chaque candidature
        Task->>DB: programme.is_enrollment_open(today) ?
        alt période ouverte
            Task->>DB: status = VALIDATED
            Task->>E: « 🎉 inscriptions ouvertes, dossier en route »
            Task->>Q3: dispatch_validated_application_task.delay()
        else encore fermée
            Task->>Task: ignorer (re-balayée demain)
        end
    end
```

---

## 5. Boucle de décision (université → étudiant)

```mermaid
sequenceDiagram
    autonumber
    participant U as Université
    participant API as POST /applications/{id}/decision
    participant DB as PostgreSQL
    participant B as WhatsAppBot
    participant E as Étudiant

    U->>API: { decision: ACCEPTED|REJECTED, comment } (idempotent)
    API->>DB: status = ACCEPTED|REJECTED, decided_at
    API->>B: notify_decision(application, decision, comment)
    alt ACCEPTED
        B-->>E: « 🎉 Félicitations, vous êtes admis·e »
    else REJECTED
        B-->>E: « Décision défavorable + motif »
    end
    API-->>U: 200 { success: true } (+ webhook application.decision.acknowledged)
```

---

## 6. Cahier des charges technique v2 — points de conformité

- **Découverte guidée** : le bot oriente par domaine puis propose des **listes
  numérotées** (universités, programmes) — pas de saisie libre quand des
  `programs` sont configurés (fallback `COLLECT_PROGRAM` sinon).
- **Formulaires dynamiques** : un `AdmissionForm` publié par programme définit ses
  `FormField` (validés par `validation_regex`) et ses `RequiredDocument`.
- **Périodes d'inscription** : `Program.is_enrollment_open()` arbitre dispatch
  immédiat vs `PENDING_ENROLLMENT` ; la tâche Beat ré-ouvre automatiquement.
- **Multi-provider IA** : `AI_PROVIDER` bascule Anthropic ↔ Gemini sans changer le
  contrat `DocumentClassificationResult`.
- **Idempotence webhooks** : `idempotency_key` unique, signature HMAC-SHA256,
  retries 1m/5m/15m/1h/6h.
- **Migrations** : `0001` (socle) + `0002` (tout le v2, enrollment inclus). Voir
  `docs/STUBS_DEV1.md`.

### Reste à faire (hors périmètre code v2)
- [ ] `external_id` (universities/programs) pour ré-imports Boussole idempotents.
- [ ] Seed de programmes réels (domaines + dates) par université cliente.
