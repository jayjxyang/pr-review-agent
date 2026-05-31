"""Knowledge tools — read project-specific review rules."""

import base64

from langchain_core.tools import tool
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.review import Review, ReviewComment
from app.services.github import _github_client

logger = get_logger(__name__)

_RULES_DIR = ".ai-review/rules"


@tool
def read_repo_rules(repo: str, ref: str) -> str:
    """Read the project's AI review rules from .ai-review/rules/ directory. Returns all rule files concatenated."""
    try:
        contents = _github_client().get_repo(repo).get_contents(_RULES_DIR, ref=ref)
    except Exception:
        return "No .ai-review/rules/ directory found in this repository."

    if not isinstance(contents, list):
        return "No rule files found."

    output = []
    for item in contents:
        if item.type == "file" and item.name.endswith(".md"):
            raw = base64.b64decode(item.content).decode("utf-8", errors="replace")
            output.append(f"## {item.name}\n\n{raw}")

    return "\n\n---\n\n".join(output) if output else "No rule files found."


_MAX_HISTORY_RESULTS = 5


def _query_review_history_impl(session: Session, repo: str, file_path: str = None, keyword: str = None) -> str:
    """Core implementation — accepts a session for testability."""
    query = (
        session.query(ReviewComment)
        .join(Review)
        .filter(Review.repo == repo)
    )
    if file_path:
        query = query.filter(ReviewComment.filename.like(f"%{file_path}%"))
    if keyword:
        query = query.filter(ReviewComment.comment.like(f"%{keyword}%"))

    query = query.order_by(ReviewComment.created_at.desc()).limit(_MAX_HISTORY_RESULTS)
    results = query.all()

    if not results:
        return f"No review history found for '{repo}'" + (f" matching filters" if file_path or keyword else "") + "."

    output = []
    for rc in results:
        output.append(
            f"- PR #{rc.review.pr_number} | {rc.filename}:L{rc.line} [{rc.severity}]\n"
            f"  {rc.comment}"
        )
    return "\n".join(output)


@tool
def query_review_history(repo: str, file_path: str = None, keyword: str = None) -> str:
    """Search past review comments for this repository. Useful for finding recurring issues.

    Args:
        repo: Repository full name (owner/repo).
        file_path: Optional file path substring to filter by.
        keyword: Optional keyword to search in comment text.
    """
    try:
        with SessionLocal() as session:
            return _query_review_history_impl(session, repo, file_path, keyword)
    except Exception as e:
        logger.warning("query_review_history_failed", error=str(e))
        return f"Error querying review history: {e}"
