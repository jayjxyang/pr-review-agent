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


class TestPostReviewLineValidation:
    """A hallucinated line must degrade per-comment, not 422 the whole review."""

    @patch("app.services.reviewer.get_pr_patches")
    @patch("app.services.reviewer.get_github_client")
    def test_invalid_lines_degrade_per_comment(self, mock_client, mock_patches):
        mock_pr = MagicMock()
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_issue_comments.return_value = []
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        from app.services.github import FilePatch
        # a.py hunk starts at new-file line 10 with two added lines -> valid {10, 11}
        mock_patches.return_value = [
            FilePatch(filename="a.py", patch="@@ -1,0 +10,2 @@\n+line10\n+line11"),
        ]

        result = {
            "risk_level": "low",
            "summary": "s",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "valid line"},
                {"filename": "a.py", "line": 999, "severity": "error", "comment": "hallucinated line"},
                {"filename": "ghost.py", "line": 1, "severity": "warning", "comment": "file not in diff"},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result, head_sha="sha")

        gh_comments = mock_pr.create_review.call_args[1]["comments"]
        assert len(gh_comments) == 1  # only the valid line posted inline
        assert gh_comments[0]["path"] == "a.py" and gh_comments[0]["line"] == 10

        body = mock_pr.create_review.call_args[1]["body"]
        assert "could not be anchored" in body.lower()  # unanchored findings surfaced
        assert "999" in body


class TestPostReviewIdempotentRepost:
    """Re-posting must replace prior bot artifacts, not stack duplicates."""

    @patch("app.services.reviewer.get_pr_patches", return_value=[])
    @patch("app.services.reviewer.get_github_client")
    def test_deletes_prior_marked_artifacts_before_posting(self, mock_client, mock_patches):
        marker = "<!-- bot4bread:ai-review -->"
        bot_inline = MagicMock(); bot_inline.body = f"{marker}\nold inline"
        human_inline = MagicMock(); human_inline.body = "a human comment"
        bot_issue = MagicMock(); bot_issue.body = f"{marker}\nold summary"

        mock_pr = MagicMock()
        mock_pr.get_review_comments.return_value = [bot_inline, human_inline]
        mock_pr.get_issue_comments.return_value = [bot_issue]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {"risk_level": "low", "summary": "new review", "comments": []}

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result, head_sha="sha")

        bot_inline.delete.assert_called_once()   # our prior artifact removed
        bot_issue.delete.assert_called_once()
        human_inline.delete.assert_not_called()  # human comments untouched
