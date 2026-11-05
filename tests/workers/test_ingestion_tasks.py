from __future__ import annotations

import importlib

import pytest

from app.core.config import get_settings
from app.rag.ingestion.workflow import RetryableIngestionError


@pytest.fixture
def tasks_module(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/rag-celery-test.db")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    get_settings.cache_clear()
    tasks = importlib.import_module("workers.tasks.ingestion")
    was_eager = tasks.celery_app.conf.task_always_eager
    yield tasks
    tasks.celery_app.conf.task_always_eager = was_eager
    get_settings.cache_clear()


class FakeWorkflow:
    def __init__(self, *, fail_once=False):
        self.calls = 0
        self.schedules = []
        self.fail_once = fail_once

    async def process(self, job_id):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RetryableIngestionError("temporary database/Qdrant failure")
        return {"job_id": job_id, "status": "succeeded"}

    def record_retry_schedule(self, job_id, countdown):
        self.schedules.append((job_id, countdown))

    async def close(self):
        return None


def test_celery_task_executes_workflow(tasks_module, monkeypatch):
    tasks = tasks_module
    tasks.celery_app.conf.task_always_eager = True
    fake = FakeWorkflow()
    monkeypatch.setattr(tasks, "build_workflow", lambda: fake)

    result = tasks.ingest_document.apply(args=["job-1"], throw=True).get()

    assert result == {"job_id": "job-1", "status": "succeeded"}
    assert fake.calls == 1


def test_celery_task_retries_transient_workflow_failure(tasks_module, monkeypatch):
    tasks = tasks_module
    fake = FakeWorkflow(fail_once=True)
    monkeypatch.setattr(tasks, "build_workflow", lambda: fake)
    retry_calls = []

    class RetryScheduled(Exception):
        pass

    def retry(**kwargs):
        retry_calls.append(kwargs)
        raise RetryScheduled

    monkeypatch.setattr(tasks.ingest_document, "retry", retry)

    with pytest.raises(RetryScheduled):
        tasks.ingest_document.run("job-2")
    assert fake.calls == 1
    assert fake.schedules == [("job-2", 2)]
    assert retry_calls[0]["countdown"] == 2
