"""Tests for run_review re-review detection logic."""

from unittest.mock import patch, MagicMock
from langgraph.errors import GraphRecursionError


class TestRunReviewReReview:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha")
    def test_re_review_passes_prior_comments_to_graph(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        mock_sha.return_value = "newsha456"
        mock_last.return_value = {
            "reviewed_sha": "oldsha123",
            "comments": [
                {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
            ],
        }

        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 3, "total_input_tokens": 5000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "oldsha123",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        # Call the underlying function directly, mocking the Celery task self
        mock_task = MagicMock()
        mock_task.request.id = "test-task-id"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        # Verify graph was invoked with prior_comments
        invoke_args = mock_graph.return_value.invoke.call_args[0][0]
        assert invoke_args["prior_comments"] == mock_last.return_value["comments"]
        assert invoke_args["last_reviewed_sha"] == "oldsha123"

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha")
    def test_first_review_has_empty_prior_comments(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        mock_sha.return_value = "abc123"
        mock_last.return_value = None

        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-task-id"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        invoke_args = mock_graph.return_value.invoke.call_args[0][0]
        assert invoke_args["prior_comments"] == []
        assert invoke_args["last_reviewed_sha"] == ""

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha")
    def test_resolved_comments_are_persisted(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,  # noqa: E501
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        mock_sha.return_value = "newsha"
        mock_last.return_value = {
            "reviewed_sha": "oldsha",
            "comments": [
                {"id": 5, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
            ],
        }

        mock_result = {
            "risk_level": "low", "summary": "OK",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 5},
            ],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "oldsha",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-task-id"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        mock_resolve.assert_called_once_with([5])

        # Verify resolved comments are excluded from save_review
        save_call_result = mock_save.call_args[0][3]  # 4th positional arg is result dict
        assert all(c.get("severity") != "resolved" for c in save_call_result["comments"])


class TestRunReviewConfig:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config")
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_run_review_loads_config(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """run_review loads repo config and passes it to the graph."""
        mock_config.return_value = {
            "ignore_paths": ["generated/**", "docs/**"],
            "tech_stack": {"language": "python"},
        }

        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-config-task"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

        # Verify config was loaded with the correct ref
        mock_config.assert_called_once_with("owner/repo", "abc123")

        # Verify graph was invoked with repo_config
        invoke_args = mock_graph.return_value.invoke.call_args[0][0]
        assert invoke_args["repo_config"] == {
            "ignore_paths": ["generated/**", "docs/**"],
            "tech_stack": {"language": "python"},
        }


class TestRunReviewResilience:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_graph_recursion_error_produces_degraded_result(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """GraphRecursionError produces a degraded result instead of crashing."""
        mock_graph.return_value.invoke.side_effect = GraphRecursionError("recursion limit")

        mock_task = MagicMock()
        mock_task.request.id = "test-recursion"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

        # Verify a degraded result was saved
        mock_save.assert_called_once()
        saved_result = mock_save.call_args[0][3]
        assert "recursion limit" in saved_result["summary"].lower()

        # Verify review was still posted
        mock_post.assert_called_once()

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_graph_invoked_with_thread_id_config(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """graph.invoke is called with a config containing thread_id for checkpointing."""
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-thread-id"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

        # Verify invoke was called with config containing thread_id
        call_kwargs = mock_graph.return_value.invoke.call_args
        # graph.invoke(state, config=config) — config is a keyword arg
        config = call_kwargs.kwargs.get("config")
        assert config is not None
        assert config["configurable"]["thread_id"] == "owner/repo:42:abc123"


class TestRunReviewCheckRun:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_creates_and_updates_check_run(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-check-run"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        mock_create.assert_called_once_with("org/repo", "abc123")
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1] == 42  # check_run_id

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan")
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_secret_scan_forces_failure(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_scan.return_value = [
            {"filename": "config.py", "line": 5, "description": "AWS access key"},
        ]
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-secret-veto"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        update_args = mock_update.call_args
        assert update_args[0][2] == "failure"  # conclusion

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_collects_feedback_before_review(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-feedback"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        mock_feedback.assert_called_once_with("org/repo", 42)


class TestRunReviewHeadShaPinning:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha")
    def test_uses_event_head_sha_not_live_head(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """When head_sha is supplied by the webhook, the task pins to it and never refetches HEAD."""
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 1, "total_input_tokens": 100, "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-pin"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42, "evt_sha", "delivery-1")

        mock_sha.assert_not_called()  # did not refetch live HEAD
        config = mock_graph.return_value.invoke.call_args.kwargs.get("config")
        assert config["configurable"]["thread_id"] == "org/repo:42:evt_sha"
        assert mock_post.call_args.kwargs.get("head_sha") == "evt_sha"


class TestRunReviewRetryClassification:
    def _make_task(self):
        mock_task = MagicMock()
        mock_task.request.id = "test-retry"
        mock_task.request.retries = 0
        mock_task.max_retries = 3
        return mock_task

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_terminal_4xx_does_not_retry(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """A 404 (PR gone) is terminal — dead-letter instead of looping retries."""
        from github import GithubException
        mock_graph.return_value.invoke.side_effect = GithubException(404, {}, {})

        mock_task = self._make_task()
        from app.tasks.review import run_review
        # Should return normally (dead-lettered), not raise
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42, "abc123", "d1")

        mock_task.retry.assert_not_called()

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=None)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_transient_5xx_retries(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
        mock_scan, mock_feedback, mock_create, mock_update, mock_update_ids,
    ):
        """A 503 is transient — the task should retry."""
        import pytest
        from github import GithubException
        mock_graph.return_value.invoke.side_effect = GithubException(503, {}, {})

        mock_task = self._make_task()
        # raise self.retry(...) — emulate Celery by having retry raise a sentinel
        sentinel = RuntimeError("retried")
        mock_task.retry.return_value = sentinel

        from app.tasks.review import run_review
        with pytest.raises(RuntimeError, match="retried"):
            run_review.__wrapped__.__func__(mock_task, "org/repo", 42, "abc123", "d1")

        mock_task.retry.assert_called_once()
