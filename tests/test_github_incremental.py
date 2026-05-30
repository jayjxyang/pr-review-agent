"""Tests for get_pr_incremental_diff."""

from unittest.mock import patch, MagicMock

from app.services.github import get_pr_incremental_diff


class TestGetPrIncrementalDiff:
    @patch("app.services.github._github_client")
    def test_returns_file_patches(self, mock_client):
        mock_file1 = MagicMock()
        mock_file1.filename = "src/auth.py"
        mock_file1.patch = "@@ -1,3 +1,4 @@\n+new line"

        mock_file2 = MagicMock()
        mock_file2.filename = "src/db.py"
        mock_file2.patch = "@@ -5,3 +5,4 @@\n+another change"

        mock_comparison = MagicMock()
        mock_comparison.files = [mock_file1, mock_file2]
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert len(result) == 2
        assert result[0].filename == "src/auth.py"
        assert result[1].filename == "src/db.py"

    @patch("app.services.github._github_client")
    def test_skips_binary_and_filtered_files(self, mock_client):
        mock_code = MagicMock()
        mock_code.filename = "src/app.py"
        mock_code.patch = "+change"

        mock_binary = MagicMock()
        mock_binary.filename = "logo.png"
        mock_binary.patch = None

        mock_lock = MagicMock()
        mock_lock.filename = "package-lock.json"
        mock_lock.patch = "+lots of stuff"

        mock_comparison = MagicMock()
        mock_comparison.files = [mock_code, mock_binary, mock_lock]
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert len(result) == 1
        assert result[0].filename == "src/app.py"

    @patch("app.services.github._github_client")
    def test_empty_diff(self, mock_client):
        mock_comparison = MagicMock()
        mock_comparison.files = []
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert result == []

    @patch("app.services.github._github_client")
    def test_raises_on_api_error(self, mock_client):
        from github import GithubException
        mock_client.return_value.get_repo.return_value.compare.side_effect = GithubException(
            404, {"message": "Not Found"}, {}
        )

        try:
            get_pr_incremental_diff("org/repo", "abc123", "def456")
            assert False, "Should have raised"
        except GithubException:
            pass
