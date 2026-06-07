"""Tâche Celery de vérification des périodes d'inscription.

Enregistrée dans ``celery_app.py`` :
  - ``include`` → la tâche est découverte par les workers,
  - ``task_routes`` → routée vers la queue ``webhooks``,
  - ``beat_schedule`` → exécutée chaque jour à 06h00 UTC par Celery Beat.

Lancer le planificateur avec : ``celery -A app.workers.celery_app beat``.
"""
import logging
from datetime import date

from celery import shared_task
from sqlalchemy import select

from app.database import get_db_session
from app.models.application import Application, ApplicationStatus
from app.models.program import Program
from app.services.whatsapp_bot import send_whatsapp

logger = logging.getLogger(__name__)


@shared_task(name="app.workers.enrollment_tasks.check_enrollment_periods")
def check_enrollment_periods_task() -> str:
    """Vérifie quotidiennement les candidatures en attente d'ouverture d'inscription.

    Pour chaque application PENDING_ENROLLMENT dont la période est maintenant ouverte :
    1. Passe le statut à VALIDATED
    2. Notifie l'étudiant par WhatsApp
    3. Déclenche le pipeline normal (dispatch webhook)

    Retourne "dispatched:N" où N est le nombre de candidatures envoyées.
    """
    db = get_db_session()
    try:
        today = date.today()

        pending = list(
            db.execute(
                select(Application).where(
                    Application.status == ApplicationStatus.PENDING_ENROLLMENT
                )
            ).scalars().all()
        )

        dispatched = 0
        for application in pending:
            program = db.execute(
                select(Program).where(
                    Program.university_id == application.university_id,
                    Program.name == application.program,
                    Program.is_active.is_(True),
                )
            ).scalar_one_or_none()

            # Envoyer si : pas de programme trouvé (fallback) OU période maintenant ouverte
            if program is not None and not program.is_enrollment_open(today):
                continue

            application.status = ApplicationStatus.VALIDATED
            db.add(application)
            db.commit()

            try:
                send_whatsapp(
                    application.student_phone,
                    "🎉 Bonne nouvelle ! Les inscriptions pour votre programme sont maintenant ouvertes.\n\n"
                    "Votre candidature est en cours de traitement et sera envoyée à l'université "
                    "dans les prochaines minutes. 🚀",
                )
            except Exception:
                logger.warning(
                    "Impossible d'envoyer la notification d'ouverture pour app %s",
                    application.id,
                    exc_info=True,
                )

            from app.workers.webhook_tasks import dispatch_validated_application_task
            dispatch_validated_application_task.delay(str(application.id))

            dispatched += 1
            logger.info(
                "Application %s (étudiant %s) PENDING_ENROLLMENT → VALIDATED",
                application.id,
                application.student_phone,
            )

        logger.info("check_enrollment_periods_task : %d candidature(s) envoyée(s)", dispatched)
        return f"dispatched:{dispatched}"
    finally:
        db.close()
