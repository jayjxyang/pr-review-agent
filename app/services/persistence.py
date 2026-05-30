"""Persist review results and agent traces to PostgreSQL."""

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.review import Review, ReviewComment, AgentTrace

logger = get_logger(__name__)


def save_review(repo: str, pr_number: int, ref: str, result: dict) -> int | None:
    """Save review result and traces to PostgreSQL.

    Args:
        repo: Repository full name (e.g. "org/repo").
        pr_number: PR number.
        ref: Reviewed commit SHA.
        result: Graph output dict with keys: risk_level, summary, comments,
                escalated, round_count, total_input_tokens, traces.

    Returns:
        The review ID, or None if persistence failed.
    """
    try:
        session = SessionLocal()
        try:
            review = Review(
                repo=repo,
                pr_number=pr_number,
                risk_level=result.get("risk_level", "low"),
                summary=result.get("summary", ""),
                escalated=result.get("escalated", False),
                model_used=result.get("escalated", False) and "reason" or "scan",
                reviewed_sha=ref,
                total_input_tokens=result.get("total_input_tokens", 0),
                round_count=result.get("round_count", 0),
            )
            session.add(review)
            session.flush()  # Get review.id

            for c in result.get("comments", []):
                session.add(ReviewComment(
                    review_id=review.id,
                    filename=c.get("filename", "unknown"),
                    line=c.get("line", 0),
                    severity=c.get("severity", "suggestion"),
                    comment=c.get("comment", ""),
                ))

            for t in result.get("traces", []):
                session.add(AgentTrace(
                    review_id=review.id,
                    round_number=t.get("round_number", 0),
                    tool_name=t.get("tool_name", ""),
                    tool_params=t.get("tool_params", {}),
                    tool_result_summary=t.get("tool_result_summary", "")[:500],
                ))

            session.commit()
            logger.info("review_persisted", review_id=review.id, repo=repo, pr=pr_number)
            return review.id

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as exc:
        logger.warning("review_persistence_failed", error=str(exc), repo=repo, pr=pr_number)
        return None


_MAX_PRIOR_COMMENTS = 20


def get_last_review(repo: str, pr_number: int) -> dict | None:
    """Get the most recent review for a PR with its unresolved comments.

    Returns:
        Dict with 'reviewed_sha' and 'comments' (list of dicts), or None if no prior review.
    """
    try:
        session = SessionLocal()
        try:
            review = (
                session.query(Review)
                .filter(Review.repo == repo, Review.pr_number == pr_number)
                .order_by(Review.created_at.desc())
                .first()
            )
            if not review:
                return None

            unresolved = (
                session.query(ReviewComment)
                .filter(
                    ReviewComment.review_id == review.id,
                    ReviewComment.resolved == False,  # noqa: E712
                )
                .order_by(ReviewComment.created_at.asc())
                .limit(_MAX_PRIOR_COMMENTS)
                .all()
            )

            return {
                "reviewed_sha": review.reviewed_sha,
                "comments": [
                    {
                        "id": c.id,
                        "filename": c.filename,
                        "line": c.line,
                        "severity": c.severity,
                        "comment": c.comment,
                    }
                    for c in unresolved
                ],
            }
        finally:
            session.close()

    except Exception as exc:
        logger.warning("get_last_review_failed", error=str(exc), repo=repo, pr=pr_number)
        return None
