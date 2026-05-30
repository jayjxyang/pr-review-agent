"""Tests for quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re
from unittest.mock import patch, MagicMock

from app.services.tools.quality import scan_secrets, check_test_coverage


class TestScanSecrets:
    @patch("app.services.tools.quality._github_client")
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

    @patch("app.services.tools.quality._github_client")
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

    @patch("app.services.tools.quality._github_client")
    def test_detects_private_key(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "certs/key.pem"
        mock_file.patch = '+-----BEGIN RSA PRIVATE KEY-----'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "key.pem" in result

    @patch("app.services.tools.quality._github_client")
    def test_clean_diff(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "app.py"
        mock_file.patch = '+def hello():\n+    return "world"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality._github_client")
    def test_only_scans_added_lines(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '-OLD_KEY = "sk-removed123456789012345678901234"\n+# key removed'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("Not found")
        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "Error" in result


class TestCheckTestCoverage:
    @patch("app.services.tools.quality._github_client")
    def test_finds_test_references(self, mock_client):
        mock_item = MagicMock()
        mock_item.path = "tests/test_auth.py"
        mock_client.return_value.search_code.return_value = [mock_item]

        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "test_auth.py" in result

    @patch("app.services.tools.quality._github_client")
    def test_no_test_references(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/utils.py", "ref": "main",
        })
        assert "No test references found" in result

    @patch("app.services.tools.quality._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.search_code.side_effect = Exception("API error")
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "Error" in result
