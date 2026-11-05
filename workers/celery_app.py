"""The project's single Celery application, using the existing RabbitMQ broker."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "knowledge_ai",
    broker=settings.rabbitmq_url,
    include=["workers.tasks.ingestion"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    task_ignore_result=True,
    beat_schedule={
        "reconcile-rag-ingestion": {
            "task": "rag.ingestion.reconcile",
            "schedule": 60.0,
        },
    },
)
