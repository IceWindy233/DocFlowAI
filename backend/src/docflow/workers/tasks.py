from __future__ import annotations

from docflow.db.models import IngestionJob
from docflow.db.session import SessionLocal
from docflow.services.pipeline import process_job
from docflow.workers.celery_app import celery_app


@celery_app.task(name="docflow.run_ingestion_job", bind=True, max_retries=2)
def run_ingestion_job(self, job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return {"job_id": job_id, "status": "NOT_FOUND"}
        try:
            result = process_job(db, job)
            return {"job_id": result.id, "status": result.status}
        except Exception as exc:
            raise self.retry(exc=exc, countdown=min(60, 10 * (self.request.retries + 1))) from exc
