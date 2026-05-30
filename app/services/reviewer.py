"""Post review results to GitHub as PR review comments."""

from app.core.logging import get_logger
from app.services.github import _github_client

logger = get_logger(__name__)

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🔵"}


def post_review(repo_full_name: str, pr_number: int, result: dict) -> None:
    """Post the agent's review to GitHub as a PR review with inline comments.

    Args:
        result: Graph output dict with keys: risk_level, summary, comments.
    """
    gh = _github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)

    summary = result.get("summary", "")
    comments = result.get("comments", [])
    risk_level = result.get("risk_level", "low")

    if not comments and not summary:
        return

    body = f"## AI Review (risk: {risk_level})\n\n{summary}"

    gh_comments = []
    for c in comments:
        emoji = _SEVERITY_EMOJI.get(c.get("severity", "suggestion"), "🔵")
        gh_comments.append({
            "path": c["filename"],
            "line": c["line"],
            "side": "RIGHT",
            "body": f"{emoji} **{c.get('severity', 'suggestion')}**: {c['comment']}",
        })

    try:
        if gh_comments:
            pr.create_review(body=body, event="COMMENT", comments=gh_comments)
        else:
            pr.create_issue_comment(body)
    except Exception:
        fallback = body + "\n\n### Findings\n\n"
        for c in comments:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "🔵")
            fallback += f"- {emoji} **{c['filename']}:{c['line']}** — {c['comment']}\n"
        pr.create_issue_comment(fallback)
