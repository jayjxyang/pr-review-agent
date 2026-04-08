from github import GithubException

from app.core.logging import get_logger
from app.services.github import _github_client
from app.services.llm import ReviewComment, ReviewResult

logger = get_logger(__name__)

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🔵"}


def post_review(repo_full_name: str, pr_number: int, results: list[ReviewResult]) -> None:
    """Aggregate all chunk ReviewResults and post a single GitHub PR review.

    Strategy:
    1. Try to post one review with all inline comments (one API call, clean UX).
    2. If GitHub rejects it (e.g. line numbers outside the diff → 422), fall back
       to a plain PR-level issue comment that lists every finding as text.
    """
    all_comments = [c for r in results for c in r.comments]
    summaries = [r.summary for r in results if r.summary.strip()]
    body = "\n\n---\n\n".join(summaries) if summaries else "Review complete — no major issues found."

    repo = _github_client().get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    gh_comments = [
        {
            "path": c.filename,
            "line": c.line,
            "side": "RIGHT",
            "body": f"{_SEVERITY_EMOJI.get(c.severity, '')} **{c.severity.upper()}**: {c.comment}",
        }
        for c in all_comments
    ]

    try:
        pr.create_review(body=body, event="COMMENT", comments=gh_comments)
        logger.info(
            "review_posted",
            repo=repo_full_name,
            pr=pr_number,
            inline_comments=len(gh_comments),
        )
    except GithubException as exc:
        # GitHub rejects inline comments whose line numbers fall outside the diff.
        # Degrade gracefully: post everything as a single plain comment.
        logger.warning(
            "inline_review_failed_fallback",
            repo=repo_full_name,
            pr=pr_number,
            status=exc.status,
            error=str(exc.data),
        )
        pr.create_issue_comment(_format_fallback(body, all_comments))
        logger.info("fallback_comment_posted", repo=repo_full_name, pr=pr_number)


def _format_fallback(summary: str, comments: list[ReviewComment]) -> str:
    lines = ["## PR Review\n", summary]
    if comments:
        lines.append("\n\n---\n\n### Findings\n")
        for c in comments:
            emoji = _SEVERITY_EMOJI.get(c.severity, "")
            lines.append(
                f"- {emoji} **{c.severity.upper()}** `{c.filename}:{c.line}` — {c.comment}"
            )
    return "\n".join(lines)
