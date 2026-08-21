from celery import Celery

from docflow.core.settings import get_settings

settings = get_settings()
celery_app = Celery("docflow", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    timezone="Asia/Shanghai",
)
celery_app.autodiscover_tasks(["docflow.workers"])
