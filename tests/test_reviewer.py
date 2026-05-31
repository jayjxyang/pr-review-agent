"""Tests for post_review re-review behavior."""

from unittest.mock import patch, MagicMock


class TestPostReviewReReview:
    @patch("app.services.reviewer.get_github_client")
    def test_filters_resolved_comments_from_posting(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Some issues",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "New issue"},
                {"filename": "b.py", "line": 20, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 5},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        # Only non-resolved comments should be posted as inline comments
        call_kwargs = mock_pr.create_review.call_args[1]
        gh_comments = call_kwargs["comments"]
        assert len(gh_comments) == 1
        assert gh_comments[0]["path"] == "a.py"

    @patch("app.services.reviewer.get_github_client")
    def test_includes_resolution_summary(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Re-reviewed",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 1},
                {"filename": "b.py", "line": 20, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 2},
                {"filename": "c.py", "line": 30, "severity": "warning", "comment": "New issue"},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        call_kwargs = mock_pr.create_review.call_args[1]
        body = call_kwargs["body"]
        assert "2" in body
        assert "resolved" in body.lower()

    @patch("app.services.reviewer.get_github_client")
    def test_all_resolved_no_new_issues(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "All fixed",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 1},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        # Should post as issue comment (no inline comments)
        mock_pr.create_issue_comment.assert_called_once()
        body = mock_pr.create_issue_comment.call_args[0][0]
        assert "resolved" in body.lower()


class TestPostReviewReturnsCommentIds:
    @patch("app.services.reviewer.get_github_client")
    def test_returns_comment_ids_from_review(self, mock_client):
        mock_pr = MagicMock()
        mock_review = MagicMock()
        mock_comment_1 = MagicMock()
        mock_comment_1.id = 1001
        mock_comment_2 = MagicMock()
        mock_comment_2.id = 1002
        mock_review.get_review_comments.return_value = [mock_comment_1, mock_comment_2]
        mock_pr.create_review.return_value = mock_review
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Issues found",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue 1"},
                {"filename": "b.py", "line": 20, "severity": "error", "comment": "Issue 2"},
            ],
        }

        from app.services.reviewer import post_review
        comment_ids = post_review("org/repo", 42, result)
        assert comment_ids == [1001, 1002]
