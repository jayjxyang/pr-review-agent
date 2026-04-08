from celery import Task
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.chunker import chunk_diff
from app.services.github import get_pr_patches
from app.services.llm import ReviewResult, call_llm
from app.services.reviewer import post_review

logger = get_logger(__name__)


@celery_app.task(
    name="tasks.run_review",
    bind=True,
    ignore_result=True,
    max_retries=3,
    default_retry_delay=60,  # seconds between retries
    acks_late=True,
)
def run_review(self: Task, repo_full_name: str, pr_number: int):
    """
    End-to-end PR review pipeline:
    1. Fetch & filter PR diff (PyGithub)
    2. Split diff into token-bounded chunks
    3. Call LLM for each chunk, collect ReviewResults
    4. Post aggregated review back to GitHub
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        # Phase 2 — fetch & chunk
        patches = get_pr_patches(repo_full_name, pr_number)
        if not patches:
            log.info("review_skipped", reason="no reviewable files")
            return

        chunks = chunk_diff(patches, token_limit=get_settings().diff_token_limit)
        log.info("chunks_ready", total=len(chunks))

        # Phase 3 — call LLM per chunk
        results: list[ReviewResult] = []
        for i, chunk in enumerate(chunks):
            log.info("llm_call_start", chunk=i, tokens=chunk.token_count, files=len(chunk.files))
            result = call_llm(chunk)
            log.info("llm_call_done", chunk=i, comments=len(result.comments))
            results.append(result)

        # Phase 3 — post review back to GitHub
        post_review(repo_full_name, pr_number, results)

    except Exception as exc:
        log.error(
            "review_failed",
            error=str(exc),
            attempt=self.request.retries + 1,
            max_retries=self.max_retries,
        )
        raise self.retry(exc=exc)

    log.info("review_completed")
