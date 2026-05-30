"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha

logger = get_logger(__name__)


@celery_app.task(
    name="tasks.run_review",
    bind=True,
    ignore_result=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def run_review(self: Task, repo_full_name: str, pr_number: int):
    """
    End-to-end PR review using LangGraph agent:
    1. Build graph and invoke with PR context
    2. Graph handles: scan → risk assessment → optional escalation
    3. Post review to GitHub
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Build and invoke graph
        graph = build_review_graph()
        result = graph.invoke({
            "messages": [],
            "repo": repo_full_name,
            "pr_number": pr_number,
            "ref": ref,
            "risk_level": "",
            "summary": "",
            "comments": [],
            "escalated": False,
            "escalate_reason": "",
            "round_count": 0,
            "total_input_tokens": 0,
            "tool_call_history": [],
        })

        log.info(
            "agent_complete",
            risk=result["risk_level"],
            escalated=result["escalated"],
            comments=len(result["comments"]),
        )

        # Post review to GitHub
        post_review(repo_full_name, pr_number, result)
        log.info("review_posted")

    except Exception as exc:
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
