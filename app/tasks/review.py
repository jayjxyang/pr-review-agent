"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task
from langgraph.errors import GraphRecursionError

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha, get_repo_config
from app.services.persistence import save_review, get_last_review, resolve_comments

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
    1. Detect re-review (query PostgreSQL for prior review)
    2. Build graph and invoke with PR context + prior comments
    3. Persist results, resolve old comments
    4. Post review to GitHub
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Load per-repo config
        repo_config = get_repo_config(repo_full_name, ref)
        ignore_paths = repo_config.get("ignore_paths", [])
        if ignore_paths:
            log.info("repo_config_ignore_paths", patterns=ignore_paths)

        # Re-review detection
        last_review = get_last_review(repo_full_name, pr_number)
        prior_comments = []
        last_reviewed_sha = ""

        if last_review:
            last_reviewed_sha = last_review["reviewed_sha"]
            prior_comments = last_review["comments"]
            log.info(
                "re_review_detected",
                last_sha=last_reviewed_sha[:7],
                unresolved_comments=len(prior_comments),
            )

        # Build and invoke graph with checkpointer thread_id
        graph = build_review_graph()
        thread_id = f"{repo_full_name}:{pr_number}:{ref}"
        config = {"configurable": {"thread_id": thread_id}}

        initial_state = {
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
            "traces": [],
            "compress_count": 0,
            "prior_comments": prior_comments,
            "last_reviewed_sha": last_reviewed_sha,
            "repo_config": repo_config,
        }

        try:
            result = graph.invoke(initial_state, config=config)
        except GraphRecursionError:
            log.error("graph_recursion_limit_hit")
            result = {
                "risk_level": "low",
                "summary": "Review terminated: graph recursion limit reached.",
                "comments": [],
                "escalated": False,
                "traces": [],
            }

        log.info(
            "agent_complete",
            risk=result["risk_level"],
            escalated=result["escalated"],
            comments=len(result["comments"]),
            traces=len(result.get("traces", [])),
        )

        # Extract resolved prior comment IDs from agent output
        resolved_ids = [
            c["prior_comment_id"]
            for c in result.get("comments", [])
            if c.get("severity") == "resolved" and c.get("prior_comment_id")
        ]

        # Persist new review to PostgreSQL (exclude resolved-status entries — they're prior-review metadata)
        save_result = dict(result)
        save_result["comments"] = [c for c in result.get("comments", []) if c.get("severity") != "resolved"]
        save_review(repo_full_name, pr_number, ref, save_result)

        # Mark old comments as resolved
        if resolved_ids:
            resolve_comments(resolved_ids)
            log.info("prior_comments_resolved", count=len(resolved_ids))

        # Post review to GitHub
        post_review(repo_full_name, pr_number, result)
        log.info("review_posted")

    except Exception as exc:
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
