"""Tests for Check Run creation, update, and conclusion logic."""

from unittest.mock import patch, MagicMock

import pytest

from app.services.check_run import compute_conclusion, _severity_to_annotation_level


class TestComputeConclusion:
    def test_secret_failed_always_failure(self):
        assert compute_conclusion(secret_failed=True, risk_level="low", check_policy="advisory") == "failure"
        assert compute_conclusion(secret_failed=True, risk_level="low", check_policy="enforced") == "failure"

    def test_advisory_always_neutral(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="advisory") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="medium", check_policy="advisory") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="low", check_policy="advisory") == "neutral"

    def test_enforced_maps_risk_level(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="enforced") == "failure"
        assert compute_conclusion(secret_failed=False, risk_level="medium", check_policy="enforced") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="low", check_policy="enforced") == "success"

    def test_default_policy_is_advisory(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="") == "neutral"


class TestSeverityToAnnotationLevel:
    def test_error_maps_to_failure(self):
        assert _severity_to_annotation_level("error") == "failure"

    def test_warning_maps_to_warning(self):
        assert _severity_to_annotation_level("warning") == "warning"

    def test_suggestion_maps_to_notice(self):
        assert _severity_to_annotation_level("suggestion") == "notice"

    def test_unknown_defaults_to_notice(self):
        assert _severity_to_annotation_level("info") == "notice"


class TestCreateCheckRun:
    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.post")
    def test_creates_check_run_in_app_mode(self, mock_post, mock_token, mock_app):
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {"id": 42}

        from app.services.check_run import create_check_run
        check_id = create_check_run("owner/repo", "abc123sha")

        assert check_id == 42
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["json"]
        assert body["name"] == "Bot4Bread"
        assert body["head_sha"] == "abc123sha"
        assert body["status"] == "in_progress"

    @patch("app.services.check_run.is_app_mode", return_value=False)
    def test_returns_none_in_pat_mode(self, mock_app):
        from app.services.check_run import create_check_run
        assert create_check_run("owner/repo", "abc123sha") is None


class TestUpdateCheckRun:
    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_updates_check_run_with_conclusion(self, mock_patch, mock_token, mock_app):
        mock_patch.return_value.status_code = 200

        from app.services.check_run import update_check_run
        result = {
            "risk_level": "high",
            "summary": "Found critical issues",
            "comments": [
                {"filename": "auth.py", "line": 10, "severity": "error", "comment": "SQL injection"},
            ],
        }
        update_check_run("owner/repo", 42, "failure", result)

        mock_patch.assert_called_once()
        body = mock_patch.call_args[1]["json"]
        assert body["conclusion"] == "failure"
        assert body["output"]["title"] == "AI Review \u2014 risk: high"
        assert len(body["output"]["annotations"]) == 1
        assert body["output"]["annotations"][0]["annotation_level"] == "failure"

    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_annotations_capped_at_50(self, mock_patch, mock_token, mock_app):
        """GitHub API limits annotations to 50 per update."""
        from app.services.check_run import update_check_run
        result = {
            "risk_level": "medium",
            "summary": "Many findings",
            "comments": [
                {"filename": f"f{i}.py", "line": i, "severity": "warning", "comment": f"Issue {i}"}
                for i in range(60)
            ],
        }
        update_check_run("owner/repo", 42, "neutral", result)

        body = mock_patch.call_args[1]["json"]
        assert len(body["output"]["annotations"]) == 50

    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_includes_secret_findings_in_summary(self, mock_patch, mock_token, mock_app):
        from app.services.check_run import update_check_run
        result = {
            "risk_level": "high",
            "summary": "Review summary",
            "comments": [],
        }
        secret_findings = [
            {"filename": "config.py", "line": 5, "description": "AWS access key"},
        ]
        update_check_run("owner/repo", 42, "failure", result, secret_findings=secret_findings)

        body = mock_patch.call_args[1]["json"]
        assert "secret" in body["output"]["summary"].lower() or "AWS" in body["output"]["summary"]
