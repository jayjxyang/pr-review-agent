"""Tests for review persistence using SQLite in-memory database."""

from sqlalchemy import create_engine, JSON
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.models.review import Review, ReviewComment, AgentTrace
from app.services.persistence import save_review


def _setup_test_db(monkeypatch):
    """Create an in-memory SQLite database and patch SessionLocal.

    SQLite does not support JSONB. We override the AgentTrace.tool_params
    column type to use plain JSON before creating tables.
    """
    # Temporarily swap JSONB -> JSON so SQLite can handle the schema
    original_type = AgentTrace.__table__.c.tool_params.type
    AgentTrace.__table__.c.tool_params.type = JSON()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)

    # Restore the original type so we don't pollute other tests
    AgentTrace.__table__.c.tool_params.type = original_type

    monkeypatch.setattr("app.services.persistence.SessionLocal", test_session_factory)
    return test_session_factory


class TestSaveReview:
    def test_saves_review_with_comments_and_traces(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "medium",
            "summary": "Found issues",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "bad pattern"},
                {"filename": "b.py", "line": 20, "severity": "error", "comment": "security issue"},
            ],
            "traces": [
                {"round_number": 1, "tool_name": "get_pr_changed_files", "tool_params": {"repo": "x", "pr_number": 1}, "tool_result_summary": "3 files"},
                {"round_number": 2, "tool_name": "read_file", "tool_params": {"repo": "x", "path": "a.py", "ref": "abc"}, "tool_result_summary": "file content"},
            ],
            "escalated": False,
            "round_count": 5,
            "total_input_tokens": 30000,
        }

        review_id = save_review("org/repo", 42, "abc123def", result)
        assert review_id is not None

        session = session_factory()
        review = session.get(Review, review_id)
        assert review.repo == "org/repo"
        assert review.pr_number == 42
        assert review.risk_level == "medium"
        assert review.reviewed_sha == "abc123def"
        assert review.round_count == 5
        assert review.total_input_tokens == 30000
        assert len(review.comments) == 2
        assert len(review.traces) == 2
        assert review.comments[0].filename == "a.py"
        assert review.traces[0].tool_name == "get_pr_changed_files"
        session.close()

    def test_saves_review_with_empty_comments_and_traces(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "low",
            "summary": "All good",
            "comments": [],
            "traces": [],
            "escalated": False,
            "round_count": 3,
            "total_input_tokens": 10000,
        }

        review_id = save_review("org/repo", 10, "def456", result)
        assert review_id is not None

        session = session_factory()
        review = session.get(Review, review_id)
        assert review.comments == []
        assert review.traces == []
        session.close()

    def test_cascade_delete(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "high",
            "summary": "Critical",
            "comments": [{"filename": "x.py", "line": 1, "severity": "error", "comment": "bad"}],
            "traces": [{"round_number": 1, "tool_name": "read_file", "tool_params": {}, "tool_result_summary": "ok"}],
            "escalated": True,
            "round_count": 2,
            "total_input_tokens": 5000,
        }

        review_id = save_review("org/repo", 5, "aaa111", result)

        session = session_factory()
        review = session.get(Review, review_id)
        session.delete(review)
        session.commit()

        assert session.query(ReviewComment).filter_by(review_id=review_id).count() == 0
        assert session.query(AgentTrace).filter_by(review_id=review_id).count() == 0
        session.close()

    def test_returns_none_on_failure(self, monkeypatch):
        """If the database is unreachable, save_review returns None instead of raising."""
        monkeypatch.setattr("app.services.persistence.SessionLocal", lambda: (_ for _ in ()).throw(Exception("connection refused")))
        review_id = save_review("org/repo", 1, "xxx", {"risk_level": "low", "summary": "", "comments": [], "traces": []})
        assert review_id is None
