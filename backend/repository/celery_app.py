"""Celery application instance for Korchess background task processing."""

import os

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("korchess", include=["repository.tasks"])

app.conf.update(
    broker_url=REDIS_URL,
    result_backend=None,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
)


@worker_process_init.connect
def init_worker_process(**kwargs):
    """Initialize connection pool and engine pool when Celery worker process starts."""
    from repository.db_connection import init_pool
    from services.full_analysis import init_engine_pool
    init_pool()
    init_engine_pool()


@worker_process_shutdown.connect
def shutdown_worker_process(**kwargs):
    """Cleanup engine pool and connection pool when Celery worker process shuts down."""
    from repository.db_connection import close_pool
    from services.full_analysis import shutdown_engine_pool
    shutdown_engine_pool()
    close_pool()
