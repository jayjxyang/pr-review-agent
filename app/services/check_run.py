"""Check Run management — create, update, compute conclusion."""

import requests

from app.services.github import is_app_mode, get_installation_token
from app.core.logging import get_logger

logger = get_logger(__name__)

_CHECK_NAME = "Bot4Bread"
_MAX_ANNOTATIONS = 50  # GitHub API limit per update

_SEVERITY_ANNOTATION = {
    "error": "failure",
    "warning": "warning",
    "suggestion": "notice",
}


def _severity_to_annotation_level(severity: str) -> str:
    return _SEVERITY_ANNOTATION.get(severity, "notice")


def compute_conclusion(*, secret_failed: bool, risk_level: str, check_policy: str) -> str:
    """Compute Check Run conclusion from review results and policy."""
    if secret_failed:
        return "failure"
    if check_policy == "enforced":
        return {"high": "failure", "medium": "neutral", "low": "success"}.get(risk_level, "neutral")
    return "neutral"


def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_installation_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_check_run(repo_full_name: str, head_sha: str) -> int | None:
    """Create a Check Run in 'in_progress' status. Returns check_run_id, or None in PAT mode."""
    if not is_app_mode():
        logger.debug("check_run_skipped_pat_mode")
        return None

    response = requests.post(
        f"https://api.github.com/repos/{repo_full_name}/check-runs",
        headers=_gh_headers(),
        json={
            "name": _CHECK_NAME,
            "head_sha": head_sha,
            "status": "in_progress",
        },
        timeout=30,
    )
    response.raise_for_status()
    check_id = response.json()["id"]
    logger.info("check_run_created", repo=repo_full_name, check_id=check_id)
    return check_id


def update_check_run(
    repo_full_name: str,
    check_run_id: int,
    conclusion: str,
    result: dict,
    *,
    secret_findings: list[dict] | None = None,
) -> None:
    """Update a Check Run with conclusion and review output."""
    if not is_app_mode():
        return

    risk_level = result.get("risk_level", "low")
    summary_text = result.get("summary", "")

    if secret_findings:
        secret_lines = "\n".join(f"- {f['filename']}:L{f['line']}: {f['description']}" for f in secret_findings)
        summary_text = f"**:rotating_light: Secrets detected (auto-blocked):**\n{secret_lines}\n\n{summary_text}"

    comments = result.get("comments", [])
    annotations = []
    for c in comments[:_MAX_ANNOTATIONS]:
        annotations.append({
            "path": c.get("filename", "unknown"),
            "start_line": c.get("line", 1),
            "end_line": c.get("line", 1),
            "annotation_level": _severity_to_annotation_level(c.get("severity", "suggestion")),
            "message": c.get("comment", ""),
        })

    response = requests.patch(
        f"https://api.github.com/repos/{repo_full_name}/check-runs/{check_run_id}",
        headers=_gh_headers(),
        json={
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": f"AI Review \u2014 risk: {risk_level}",
                "summary": summary_text,
                "annotations": annotations,
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    logger.info("check_run_updated", check_id=check_run_id, conclusion=conclusion)
