"""Tests for query_review_history tool."""

from datetime import datetime, timezone

from sqlalchemy import create_engine, JSON
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.review import Review, ReviewComment, AgentTrace


_test_engine = create_engine("sqlite:///:memory:")
_TestSession = sessionmaker(bind=_test_engine)


def _setup_test_db():
    """Create tables and seed test data."""
    # Swap JSONB to JSON for SQLite compatibility
    AgentTrace.__table__.c.tool_params.type = JSON()

    Base.metadata.create_all(_test_engine)
    session = _TestSession()

    review = Review(
        repo="org/repo",
        pr_number=42,
        risk_level="medium",
        summary="Found issues",
        reviewed_sha="abc123",
    )
    session.add(review)
    session.flush()

    session.add(ReviewComment(
        review_id=review.id,
        filename="src/auth.py",
        line=10,
        severity="warning",
        comment="Missing null check on token",
    ))
    session.add(ReviewComment(
        review_id=review.id,
        filename="src/db.py",
        line=25,
        severity="error",
        comment="SQL injection risk in query builder",
    ))
    session.commit()
    return session


class TestQueryReviewHistory:
    def setup_method(self):
        Base.metadata.drop_all(_test_engine)
        self.session = _setup_test_db()

    def teardown_method(self):
        self.session.close()

    def test_query_by_repo(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo")
        assert "auth.py" in result
        assert "db.py" in result

    def test_query_by_file_path(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", file_path="auth")
        assert "auth.py" in result
        assert "db.py" not in result

    def test_query_by_keyword(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", keyword="SQL injection")
        assert "SQL injection" in result
        assert "null check" not in result

    def test_no_results(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "other/repo")
        assert "No review history found" in result

    def test_combined_filters(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", file_path="auth", keyword="token")
        assert "null check on token" in result
        assert "SQL injection" not in result
