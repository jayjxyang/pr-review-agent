"""Tests for quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re
from unittest.mock import patch, MagicMock

from app.services.tools.quality import scan_secrets, check_test_coverage
from app.services.tools.quality import get_ci_status, get_ci_logs


class TestScanSecrets:
    @patch("app.services.tools.quality.get_github_client")
    def test_detects_api_key(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '+API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "config.py" in result
        assert "secret" in result.lower() or "key" in result.lower()

    @patch("app.services.tools.quality.get_github_client")
    def test_detects_github_token(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "deploy.sh"
        mock_file.patch = '+GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "deploy.sh" in result
        assert "GitHub" in result or "ghp_" in result.lower() or "token" in result.lower()

    @patch("app.services.tools.quality.get_github_client")
    def test_detects_private_key(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "certs/key.pem"
        mock_file.patch = '+-----BEGIN RSA PRIVATE KEY-----'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "key.pem" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_clean_diff(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "app.py"
        mock_file.patch = '+def hello():\n+    return "world"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_only_scans_added_lines(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '-OLD_KEY = "sk-removed123456789012345678901234"\n+# key removed'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("Not found")
        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result


class TestCheckTestCoverage:
    @patch("app.services.tools.quality.get_github_client")
    def test_finds_test_references(self, mock_client):
        mock_item = MagicMock()
        mock_item.path = "tests/test_auth.py"
        mock_client.return_value.search_code.return_value = [mock_item]

        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "test_auth.py" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_no_test_references(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/utils.py", "ref": "main",
        })
        assert "No test references found" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.search_code.side_effect = Exception("API error")
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "Error" in result


class TestGetCiStatus:
    @patch("app.services.tools.quality.get_github_client")
    def test_returns_check_statuses(self, mock_client):
        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.status = "completed"
        mock_check.conclusion = "success"
        mock_check.html_url = "https://github.com/org/repo/actions/runs/123"

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "CI / test" in result
        assert "success" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_no_checks(self, mock_client):
        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = []
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No CI checks found" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("API error")
        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "Error" in result


class TestGetCiLogs:
    @patch("app.services.tools.quality.get_github_client")
    def test_returns_failure_annotations(self, mock_client):
        mock_annotation = MagicMock()
        mock_annotation.path = "src/auth.py"
        mock_annotation.start_line = 10
        mock_annotation.annotation_level = "failure"
        mock_annotation.message = "AssertionError: expected True"

        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.conclusion = "failure"
        mock_check.output.annotations_count = 1
        mock_check.get_annotations.return_value = [mock_annotation]

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "CI / test",
        })
        assert "AssertionError" in result

    @patch("app.services.tools.quality.get_github_client")
    def test_check_not_found(self, mock_client):
        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = []
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "nonexistent",
        })
        assert "not found" in result.lower()

    @patch("app.services.tools.quality.get_github_client")
    def test_check_passed(self, mock_client):
        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.conclusion = "success"

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "CI / test",
        })
        assert "passed" in result.lower() or "success" in result.lower()
