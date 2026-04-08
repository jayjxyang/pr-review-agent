from celery import Celery
from app.core.config import get_settings

celery_app = Celery(
    "pr_review",
    broker=get_settings().redis_url,
    # No result backend — review tasks are fire-and-forget.
    # Adding a backend here would fill Redis with unconsumed results.
    include=["app.tasks.review"],
)

celery_app.conf.update(
    task_serializer="json",
    # Acknowledge tasks only after successful execution, not on delivery.
    # Combined with task_reject_on_worker_lost, this ensures tasks are
    # re-queued if the worker crashes mid-execution.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Route review tasks to a dedicated queue so they can be scaled
    # independently and monitored separately from other workloads.
    task_routes={
        "tasks.run_review": {"queue": "reviews"},
    },
)
