# Dépendances Dev 1 — migrations & infra

> Recense ce que le code v2 (modèles Dev 1 + feature enrollment Dev 2) attend
> côté **migrations Alembic** et **Celery Beat**. En SQLite (tests) tout marche
> via `Base.metadata.create_all` ; ce document concerne le démarrage **PostgreSQL (prod)**.
>
> **État au 2026-06-07 : migrations et Celery Beat livrés.** Les points
> historiquement bloquants sont résolus ; seul subsiste un item optionnel
> (`external_id` pour l'idempotence de l'import Boussole).

---

## 1. Migrations Alembic — ✅ complètes

| Migration | Tables / changements | État |
|-----------|----------------------|------|
| `0001_initial_schema` | `universities`, `applications`, `documents`, `webhook_deliveries` (+ enums) | ✅ |
| `0002_v2_dynamic_forms` | `programs` (avec `enrollment_start`/`enrollment_end`), `admission_forms`, `form_fields`, `required_documents`, `application_field_values`, FK `applications.program_id`, **et** valeurs d'enum v2 dont `PENDING_ENROLLMENT` | ✅ |

> La migration `0002` couvre **à la fois** les formulaires dynamiques (Dev 1) et la
> feature « périodes d'inscription » de Dev 2 (colonnes DATE + statut
> `PENDING_ENROLLMENT`). Les migrations séparées `0003`/`0004` envisagées
> initialement sont donc **inutiles**.
>
> ⚠️ PostgreSQL : `ADD VALUE` sur un enum est fait via
> `op.execute("ALTER TYPE ... ADD VALUE IF NOT EXISTS ...")` (PG 12+ supporte
> ça dans une transaction — cible projet : PG 15+). Voir `0002` lignes 39-42.

### Reste optionnel
- [ ] `external_id` sur `universities` + `programs` — **non implémenté** (ni modèle,
      ni migration). Utile seulement pour rendre l'import Boussole *idempotent*
      (ré-import sans doublon). Aujourd'hui l'import suppose une base vierge.
      À ajouter si on veut des ré-imports incrémentaux.

---

## 2. Feature « périodes d'inscription » (Dev 2, greffée sur modèles Dev 1)

Ajouts faits par Dev 2 sur les modèles **canoniques de Dev 1** :

- `Program.enrollment_start` / `enrollment_end` (DATE, null = pas de borne) — migration `0002`
- `Program.is_enrollment_open(reference_date=None)` — méthode métier
- `ApplicationStatus.PENDING_ENROLLMENT` — statut (enum, migration `0002`)
- `ConversationState.AWAITING_ENROLLMENT_CHOICE` — état bot (colonne `conversation_state`, pas de migration)

Comportement :
- À la sélection d'un programme hors période → le bot propose : revenir plus tard
  (candidature supprimée) ou déposer maintenant (statut `PENDING_ENROLLMENT`).
- `check_application_completion_task` : si dossier complet mais inscriptions fermées
  → `PENDING_ENROLLMENT` au lieu de dispatch.
- `check_enrollment_periods_task` (quotidienne) → bascule les `PENDING_ENROLLMENT`
  ouverts en `VALIDATED` + notif WhatsApp + dispatch.

---

## 3. Celery Beat — ✅ enregistré

`app/workers/enrollment_tasks.py::check_enrollment_periods_task` est désormais :

- déclarée dans `include=[...]` de `celery_app` (découverte par les workers),
- routée vers la queue `webhooks` via `task_routes`,
- planifiée dans `beat_schedule` (chaque jour à 06h00 UTC).

```python
# app/workers/celery_app.py
beat_schedule={
    "check-enrollment-periods-daily": {
        "task": "app.workers.enrollment_tasks.check_enrollment_periods",
        "schedule": crontab(hour=6, minute=0),
    },
}
```

Lancer le planificateur : `celery -A app.workers.celery_app beat`
(le service `worker-beat` de `docker-compose.yml` doit exécuter cette commande).

---

## 4. Checklist de mise en production Dev 1

- [x] Migration `0002` : tables formulaires dynamiques + `applications.program_id`
- [x] `programs.enrollment_start` / `enrollment_end` (dans `0002`)
- [x] `PENDING_ENROLLMENT` dans `application_status_enum` (dans `0002`)
- [x] Enregistrer `check_enrollment_periods` dans `celery_app.beat_schedule`
- [ ] `external_id` (universities, programs) pour idempotence import Boussole — *optionnel*
- [ ] Seeder programmes (avec `domain` + dates d'inscription) par université
