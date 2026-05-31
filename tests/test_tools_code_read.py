"""Tests for find_definition tool."""

from unittest.mock import patch, MagicMock

from app.services.tools.code_read import find_definition


class TestFindDefinition:
    @patch("app.services.tools.code_read.get_github_client")
    def test_finds_python_def(self, mock_client):
        mock_item = MagicMock()
        mock_item.path = "src/auth.py"
        mock_item.html_url = "https://github.com/org/repo/blob/main/src/auth.py"
        mock_client.return_value.search_code.return_value = [mock_item]

        result = find_definition.invoke({"repo": "org/repo", "symbol": "login"})
        assert "auth.py" in result

    @patch("app.services.tools.code_read.get_github_client")
    def test_no_results(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        result = find_definition.invoke({"repo": "org/repo", "symbol": "nonexistent"})
        assert "No definition found" in result

    @patch("app.services.tools.code_read.get_github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.search_code.side_effect = Exception("API error")
        result = find_definition.invoke({"repo": "org/repo", "symbol": "login"})
        assert "Error" in result

    @patch("app.services.tools.code_read.get_github_client")
    def test_with_path_filter(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        find_definition.invoke({"repo": "org/repo", "symbol": "login", "path_filter": "src/"})
        call_args = mock_client.return_value.search_code.call_args[0][0]
        assert "path:src/" in call_args
