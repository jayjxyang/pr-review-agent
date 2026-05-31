"""Tests for feedback collection via GitHub reactions."""

from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, JSON
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.review import Review, ReviewComment, AgentTrace
from app.services.persistence import collect_feedback


def _setup_test_db(monkeypatch):
    original_type = AgentTrace.__table__.c.tool_params.type
    AgentTrace.__table__.c.tool_params.type = JSON()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    AgentTrace.__table__.c.tool_params.type = original_type
    monkeypatch.setattr("app.services.persistence.SessionLocal", test_session_factory)
    return test_session_factory


class TestCollectFeedback:
    @patch("app.services.persistence.get_github_client")
    def test_thumbs_down_marks_false_positive(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(repo="org/repo", pr_number=42, risk_level="medium", summary="Issues", reviewed_sha="abc123")
        session.add(review)
        session.flush()
        comment = ReviewComment(review_id=review.id, filename="a.py", line=10, severity="warning", comment="Issue", github_comment_id=1001)
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        mock_reaction = MagicMock()
        mock_reaction.content = "-1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [mock_reaction]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "false_positive"
        session.close()

    @patch("app.services.persistence.get_github_client")
    def test_thumbs_up_marks_helpful(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(repo="org/repo", pr_number=42, risk_level="low", summary="OK", reviewed_sha="abc123")
        session.add(review)
        session.flush()
        comment = ReviewComment(review_id=review.id, filename="a.py", line=10, severity="suggestion", comment="Tip", github_comment_id=2001)
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        mock_reaction = MagicMock()
        mock_reaction.content = "+1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [mock_reaction]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "helpful"
        session.close()

    @patch("app.services.persistence.get_github_client")
    def test_both_reactions_thumbs_down_wins(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(repo="org/repo", pr_number=42, risk_level="low", summary="OK", reviewed_sha="abc123")
        session.add(review)
        session.flush()
        comment = ReviewComment(review_id=review.id, filename="a.py", line=10, severity="warning", comment="Issue", github_comment_id=3001)
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        up = MagicMock()
        up.content = "+1"
        down = MagicMock()
        down.content = "-1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [up, down]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "false_positive"
        session.close()

    def test_skips_comments_without_github_comment_id(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(repo="org/repo", pr_number=42, risk_level="low", summary="OK", reviewed_sha="abc123")
        session.add(review)
        session.flush()
        session.add(ReviewComment(review_id=review.id, filename="a.py", line=10, severity="warning", comment="Issue", github_comment_id=None))
        session.commit()
        session.close()
        collect_feedback("org/repo", 42)  # Should not raise

    def test_no_prior_review_is_noop(self, monkeypatch):
        _setup_test_db(monkeypatch)
        collect_feedback("org/repo", 999)  # Should not raise
