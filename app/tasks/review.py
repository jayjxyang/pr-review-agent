"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task
from langgraph.errors import GraphRecursionError

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha, get_repo_config
from app.services.persistence import (
    save_review, get_last_review, resolve_comments,
    collect_feedback, update_github_comment_ids,
)
from app.services.check_run import create_check_run, update_check_run, compute_conclusion
from app.services.tools.quality import run_secret_scan

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
    End-to-end PR review pipeline:
    1. Collect feedback on prior review (reactions)
    2. Create Check Run (in_progress)
    3. Run independent secret scan (security bypass)
    4. Build graph and invoke with PR context
    5. Compute conclusion and update Check Run
    6. Persist results, post review
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    check_run_id = None
    try:
        # 0. Collect feedback from prior review reactions
        collect_feedback(repo_full_name, pr_number)

        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Load per-repo config
        repo_config = get_repo_config(repo_full_name, ref)
        ignore_paths = repo_config.get("ignore_paths", [])
        if ignore_paths:
            log.info("repo_config_ignore_paths", patterns=ignore_paths)

        # 1. Create Check Run (returns None in PAT mode)
        check_run_id = create_check_run(repo_full_name, ref)

        # 2. Independent secret scan (before graph, cannot be overridden by LLM)
        secret_findings = run_secret_scan(repo_full_name, pr_number)
        secret_failed = len(secret_findings) > 0
        if secret_failed:
            log.warning("secrets_detected", count=len(secret_findings))

        # 3. Re-review detection
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

        # 4. Build and invoke graph
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
            "secret_findings": secret_findings,
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

        # 5. Compute conclusion and update Check Run
        check_policy = repo_config.get("check_policy", "advisory")
        conclusion = compute_conclusion(
            secret_failed=secret_failed,
            risk_level=result["risk_level"],
            check_policy=check_policy,
        )
        if check_run_id:
            update_check_run(
                repo_full_name, check_run_id, conclusion, result,
                secret_findings=secret_findings if secret_failed else None,
            )
            log.info("check_run_completed", conclusion=conclusion)

        # 6. Extract resolved prior comment IDs
        resolved_ids = [
            c["prior_comment_id"]
            for c in result.get("comments", [])
            if c.get("severity") == "resolved" and c.get("prior_comment_id")
        ]

        # 7. Persist to PostgreSQL
        save_result = dict(result)
        save_result["comments"] = [c for c in result.get("comments", []) if c.get("severity") != "resolved"]
        review_id = save_review(repo_full_name, pr_number, ref, save_result)

        if resolved_ids:
            resolve_comments(resolved_ids)
            log.info("prior_comments_resolved", count=len(resolved_ids))

        # 8. Post review to GitHub and store comment IDs
        github_comment_ids = post_review(repo_full_name, pr_number, result)
        if review_id and github_comment_ids:
            update_github_comment_ids(review_id, github_comment_ids)

        log.info("review_posted")

    except Exception as exc:
        # Update Check Run to failure on unexpected error
        try:
            if check_run_id:
                update_check_run(
                    repo_full_name, check_run_id, "failure",
                    {"risk_level": "unknown", "summary": f"Review failed: {exc}", "comments": []},
                )
        except Exception:
            pass
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
