# -*- coding: utf-8 -*-
"""File de tâches DataPilot avec Redis/Celery et dégradation contrôlée."""

from __future__ import annotations

import logging
import os
from typing import Any


LOGGER = logging.getLogger(__name__)
REDIS_URL = os.environ.get("DATAPILOT_REDIS_URL", "redis://127.0.0.1:6379/0")
QUEUE_ENABLED = os.environ.get("DATAPILOT_QUEUE_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off",
}

try:
    from celery import Celery
except ImportError:  # L’application garde son repli local si les extras manquent.
    Celery = None

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ImportError:
    Redis = None
    RedisError = Exception


celery_app = None
import_project_task = None
if Celery is not None:
    celery_app = Celery(
        "datapilot",
        broker=REDIS_URL,
        backend=REDIS_URL,
        include=["task_queue"],
    )
    celery_app.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        worker_soft_shutdown_timeout=30.0,
        broker_connection_retry_on_startup=True,
        visibility_timeout=600,
        broker_transport_options={
            "visibility_timeout": 600,
            "socket_connect_timeout": 2,
            "max_retries": 3,
        },
        result_backend_transport_options={
            "global_keyprefix": "datapilot_",
            "retry_policy": {"timeout": 5.0},
            "visibility_timeout": 600,
        },
        result_expires=3600,
        task_soft_time_limit=300,
        task_time_limit=360,
    )


if celery_app is not None:
    @celery_app.task(bind=True, name="datapilot.import_project")
    def import_project_task(
        self,
        project_id: str,
        safe_name: str,
        extension: str,
    ) -> dict[str, Any]:
        """Analyse un fichier déjà enregistré ; la tâche est idempotente."""
        from app import process_project_import

        try:
            return process_project_import(project_id, safe_name, extension)
        except OSError as exc:
            raise self.retry(
                exc=exc,
                countdown=min(30, 2 ** (self.request.retries + 1)),
                max_retries=2,
            )


def enqueue_project_import(
    project_id: str,
    safe_name: str,
    extension: str,
    *,
    task_id: str,
) -> tuple[bool, str]:
    """Envoie l’import au worker sans bloquer si Redis est indisponible."""
    if not QUEUE_ENABLED:
        return False, "disabled"
    if celery_app is None:
        return False, "dependency_missing"
    health = queue_health()
    if not health["available"]:
        return False, health["state"]
    try:
        import_project_task.apply_async(
            args=[project_id, safe_name, extension],
            task_id=task_id,
            retry=False,
        )
        return True, "queued"
    except Exception as exc:
        LOGGER.warning("File Celery indisponible, repli local: %s", exc)
        return False, "unavailable"


_redis_client = (
    Redis.from_url(
        REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=2,
        health_check_interval=30,
        decode_responses=True,
    )
    if Redis is not None
    else None
)


def queue_health() -> dict[str, Any]:
    """Expose un état court, sans transformer Redis en dépendance bloquante."""
    if not QUEUE_ENABLED:
        return {"enabled": False, "available": False, "state": "disabled"}
    if celery_app is None or _redis_client is None:
        return {"enabled": True, "available": False, "state": "dependency_missing"}
    try:
        _redis_client.ping()
        return {"enabled": True, "available": True, "state": "ready"}
    except RedisError:
        return {"enabled": True, "available": False, "state": "unavailable"}


__all__ = [
    "REDIS_URL",
    "celery_app",
    "enqueue_project_import",
    "import_project_task",
    "queue_health",
]
