"""Tests for standalone run_secret_scan (independent security bypass)."""

from unittest.mock import patch, MagicMock

from app.services.tools.quality import run_secret_scan


class TestRunSecretScan:
    @patch("app.services.tools.quality.get_github_client")
    def test_returns_findings_list(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '+API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        findings = run_secret_scan("org/repo", 1)

        assert len(findings) == 1
        assert findings[0]["filename"] == "config.py"
        assert "description" in findings[0]
        assert "line" in findings[0]

    @patch("app.services.tools.quality.get_github_client")
    def test_returns_empty_list_for_clean_diff(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "app.py"
        mock_file.patch = '+def hello():\n+    return "world"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        findings = run_secret_scan("org/repo", 1)
        assert findings == []

    @patch("app.services.tools.quality.get_github_client")
    def test_detects_multiple_secrets_across_files(self, mock_client):
        file1 = MagicMock()
        file1.filename = "config.py"
        file1.patch = '+password = "SuperSecret123!"'
        file2 = MagicMock()
        file2.filename = "deploy.sh"
        file2.patch = '+GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [file1, file2]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        findings = run_secret_scan("org/repo", 1)
        assert len(findings) == 2
        filenames = {f["filename"] for f in findings}
        assert filenames == {"config.py", "deploy.sh"}

    @patch("app.services.tools.quality.get_github_client")
    def test_returns_empty_on_api_error(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("Not found")
        findings = run_secret_scan("org/repo", 1)
        assert findings == []

    @patch("app.services.tools.quality.get_github_client")
    def test_only_scans_added_lines(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '-OLD_KEY = "sk-removed123456789012345678901234"\n+# key removed'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        findings = run_secret_scan("org/repo", 1)
        assert findings == []
