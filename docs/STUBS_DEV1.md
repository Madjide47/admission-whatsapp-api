# Dépendances Dev 1 — migrations & infra à livrer

> Recense ce que le code v2 (modèles Dev 1 + feature enrollment Dev 2) attend
> côté **migrations Alembic** et **Celery Beat**. En SQLite (tests) tout marche
> via `Base.metadata.create_all`. En **PostgreSQL (prod), rien ne démarrera**
> tant que les migrations ci-dessous ne sont pas écrites.

---

## 1. Migrations Alembic manquantes

Seule `0001_initial_schema.py` existe. Les tables v2 (créées comme modèles mais
**pas** comme migrations) doivent être ajoutées :

| Migration | Tables / changements | Origine |
|-----------|----------------------|---------|
| `0002_dynamic_forms` | `programs`, `admission_forms`, `form_fields`, `required_documents`, `application_field_values` + FK `applications.program_id` | Dev 1 |
| `0003_enrollment` | colonnes `programs.enrollment_start` (DATE), `programs.enrollment_end` (DATE) | **Dev 2 (enrollment)** |
| `0004_pending_enrollment` | `ALTER TYPE application_status_enum ADD VALUE 'PENDING_ENROLLMENT'` | **Dev 2 (enrollment)** |
| (idem) | `external_id` sur `universities` + `programs` si import Boussole idempotent | Dev 1 |

> ⚠️ PostgreSQL : `ADD VALUE` sur un enum ne peut pas tourner dans une transaction.
> Utiliser `op.execute("ALTER TYPE ... ADD VALUE IF NOT EXISTS ...")` hors bloc
> transactionnel (ou `COMMIT` explicite).

---

## 2. Feature « périodes d'inscription » (Dev 2, greffée sur modèles Dev 1)

Ajouts faits par Dev 2 sur les modèles **canoniques de Dev 1** :

- `Program.enrollment_start` / `enrollment_end` (DATE, null = pas de borne)
- `Program.is_enrollment_open(reference_date=None)` — méthode métier
- `ApplicationStatus.PENDING_ENROLLMENT` — nouveau statut
- `ConversationState.AWAITING_ENROLLMENT_CHOICE` — état bot (colonne `conversation_state`, pas de migration)

Comportement :
- À la sélection d'un programme hors période → le bot propose : revenir plus tard
  (candidature supprimée) ou déposer maintenant (statut `PENDING_ENROLLMENT`).
- `check_application_completion_task` : si dossier complet mais inscriptions fermées
  → `PENDING_ENROLLMENT` au lieu de dispatch.
- `check_enrollment_periods_task` (quotidienne) → bascule les `PENDING_ENROLLMENT`
  ouverts en `VALIDATED` + notif WhatsApp + dispatch.

---

## 3. Celery Beat — tâche planifiée

`app/workers/enrollment_tasks.py::check_enrollment_periods_task` doit être
enregistrée dans `app/workers/celery_app.py` (Dev 1) :

```python
from celery.schedules import crontab

app.conf.beat_schedule = {
    "check-enrollment-periods-daily": {
        "task": "app.workers.enrollment_tasks.check_enrollment_periods",
        "schedule": crontab(hour=6, minute=0),  # tous les jours à 6h00
    },
}
```

Le service `worker-beat` existe déjà dans `docker-compose.yml`. Sans cette config,
les candidatures `PENDING_ENROLLMENT` ne partent pas automatiquement (workaround :
lancer la tâche à la main).

---

## 4. Checklist de mise en production Dev 1

- [ ] Migration `0002` : tables formulaires dynamiques + `applications.program_id`
- [ ] Migration `0003` : `programs.enrollment_start` / `enrollment_end`
- [ ] Migration `0004` : `PENDING_ENROLLMENT` dans `application_status_enum`
- [ ] `external_id` (universities, programs) pour idempotence import Boussole
- [ ] Enregistrer `check_enrollment_periods` dans `celery_app.beat_schedule`
- [ ] Seeder programmes (avec `domain` + dates d'inscription) par université
