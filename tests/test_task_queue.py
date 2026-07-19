import task_queue


def test_celery_uses_reliable_redis_task_settings():
    assert task_queue.celery_app is not None
    config = task_queue.celery_app.conf
    assert config.task_acks_late is True
    assert config.task_reject_on_worker_lost is True
    assert config.worker_prefetch_multiplier == 1
    assert config.worker_cancel_long_running_tasks_on_connection_loss is True
    assert config.worker_soft_shutdown_timeout == 30.0
    assert config.visibility_timeout == 600
    assert config.broker_transport_options["visibility_timeout"] == 600
    assert config.result_backend_transport_options["visibility_timeout"] == 600
    assert config.task_track_started is True
    assert config.result_expires == 3600
    assert config.broker_url.startswith("redis")
    assert config.result_backend.startswith("redis")


def test_enqueue_degrades_without_contacting_broker_when_redis_is_down(monkeypatch):
    called = []
    monkeypatch.setattr(
        task_queue,
        "queue_health",
        lambda: {"enabled": True, "available": False, "state": "unavailable"},
    )
    monkeypatch.setattr(
        task_queue.import_project_task,
        "apply_async",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    queued, state = task_queue.enqueue_project_import(
        "a" * 32,
        "ventes.csv",
        ".csv",
        task_id="b" * 32,
    )
    assert queued is False
    assert state == "unavailable"
    assert called == []


def test_enqueue_sends_only_small_project_metadata(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        task_queue,
        "queue_health",
        lambda: {"enabled": True, "available": True, "state": "ready"},
    )
    monkeypatch.setattr(
        task_queue.import_project_task,
        "apply_async",
        lambda **kwargs: captured.update(kwargs),
    )
    queued, state = task_queue.enqueue_project_import(
        "a" * 32,
        "ventes.csv",
        ".csv",
        task_id="b" * 32,
    )
    assert queued is True
    assert state == "queued"
    assert captured == {
        "args": ["a" * 32, "ventes.csv", ".csv"],
        "task_id": "b" * 32,
        "retry": False,
    }
