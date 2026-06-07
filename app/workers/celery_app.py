"""Configuration de l'application Celery.

Trois queues distinctes :
  - ocr       : extraction OCR (Tesseract), CPU-bound
  - ai        : classification IA (Claude API), I/O-bound
  - webhooks  : envoi webhooks signés vers universités

Tâche périodique (Celery Beat) :
  - enrollment_tasks.check_enrollment_periods : balaie chaque jour les
    candidatures PENDING_ENROLLMENT dont la période d'inscription vient d'ouvrir.
"""
import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from app.config import settings


# Désactive la reconfiguration du logging par Celery — on utilise le nôtre
@setup_logging.connect
def _configure_logging(**kwargs):
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


celery_app = Celery(
    "admission",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.ocr_tasks",
        "app.workers.ai_tasks",
        "app.workers.webhook_tasks",
        "app.workers.enrollment_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # Tâches re-livrées si worker meurt
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=200,  # Recycle les workers — évite les fuites mémoire
    worker_prefetch_multiplier=1,  # Tâches longues = pas de pré-fetch agressif
    broker_connection_retry_on_startup=True,
    # Routage des tâches vers les bonnes queues
    task_routes={
        "app.workers.ocr_tasks.*": {"queue": "ocr"},
        "app.workers.ai_tasks.*": {"queue": "ai"},
        "app.workers.webhook_tasks.*": {"queue": "webhooks"},
        # La tâche d'inscription orchestre l'envoi des dossiers → queue webhooks
        "app.workers.enrollment_tasks.*": {"queue": "webhooks"},
    },
    # Planification périodique (Celery Beat) — lancer avec :
    #   celery -A app.workers.celery_app beat
    beat_schedule={
        "check-enrollment-periods-daily": {
            "task": "app.workers.enrollment_tasks.check_enrollment_periods",
            "schedule": crontab(hour=6, minute=0),  # tous les jours à 06h00 UTC
        },
    },
)
