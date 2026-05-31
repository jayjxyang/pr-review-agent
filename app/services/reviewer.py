"""Post review results to GitHub as PR review comments."""

from app.core.logging import get_logger
from app.services.github import get_github_client

logger = get_logger(__name__)

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🔵"}


def post_review(repo_full_name: str, pr_number: int, result: dict) -> None:
    """Post the agent's review to GitHub as a PR review with inline comments.

    Args:
        result: Graph output dict with keys: risk_level, summary, comments.
               Comments with severity="resolved" are filtered out of inline posting.
    """
    gh = get_github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)

    summary = result.get("summary", "")
    all_comments = result.get("comments", [])
    risk_level = result.get("risk_level", "low")

    # Separate resolved vs new/open comments
    resolved = [c for c in all_comments if c.get("severity") == "resolved"]
    active_comments = [c for c in all_comments if c.get("severity") != "resolved"]

    if not active_comments and not summary and not resolved:
        return

    # Build body with resolution summary if applicable
    body = f"## AI Review (risk: {risk_level})\n\n{summary}"
    if resolved:
        body += f"\n\n**Re-review:** {len(resolved)} prior issue(s) resolved."

    gh_comments = []
    for c in active_comments:
        emoji = _SEVERITY_EMOJI.get(c.get("severity", "suggestion"), "🔵")
        gh_comments.append({
            "path": c.get("filename", "unknown"),
            "line": c.get("line", 1),
            "side": "RIGHT",
            "body": f"{emoji} **{c.get('severity', 'suggestion')}**: {c.get('comment', '')}",
        })

    try:
        if gh_comments:
            pr.create_review(body=body, event="COMMENT", comments=gh_comments)
        else:
            pr.create_issue_comment(body)
    except Exception as exc:
        logger.warning("inline_review_failed_fallback", error=str(exc))
        fallback = body + "\n\n### Findings\n\n"
        for c in active_comments:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "🔵")
            fallback += f"- {emoji} **{c.get('filename', 'unknown')}:{c.get('line', '?')}** — {c.get('comment', '')}\n"
        pr.create_issue_comment(fallback)
