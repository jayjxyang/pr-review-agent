"""Tests for git history tools — git_log and git_blame."""

import json
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.services.tools.git_history import git_log, git_blame


class TestGitLog:
    @patch("app.services.tools.git_history.get_github_client")
    def test_returns_formatted_commits(self, mock_client):
        mock_commit = MagicMock()
        mock_commit.sha = "abc1234567890"
        mock_commit.commit.message = "Fix auth bug"
        mock_commit.commit.author.name = "dev1"
        mock_commit.commit.author.date = datetime(2026, 5, 30, 10, 0, 0)
        mock_commit.files = [MagicMock(filename="auth.py")]

        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        mock_client.return_value.get_repo.return_value = mock_repo

        result = git_log.invoke({"repo": "org/repo", "ref": "main"})
        assert "abc1234" in result
        assert "Fix auth bug" in result
        assert "dev1" in result

    @patch("app.services.tools.git_history.get_github_client")
    def test_with_path_filter(self, mock_client):
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = []
        mock_client.return_value.get_repo.return_value = mock_repo

        result = git_log.invoke({"repo": "org/repo", "ref": "main", "path": "src/auth.py"})
        mock_repo.get_commits.assert_called_once()
        call_kwargs = mock_repo.get_commits.call_args
        assert call_kwargs[1].get("path") == "src/auth.py" or call_kwargs.kwargs.get("path") == "src/auth.py"

    @patch("app.services.tools.git_history.get_github_client")
    def test_limits_results(self, mock_client):
        commits = []
        for i in range(15):
            c = MagicMock()
            c.sha = f"sha{i:04d}"
            c.commit.message = f"Commit {i}"
            c.commit.author.name = "dev"
            c.commit.author.date = datetime(2026, 5, 30)
            c.files = []
            commits.append(c)

        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = commits
        mock_client.return_value.get_repo.return_value = mock_repo

        result = git_log.invoke({"repo": "org/repo", "ref": "main", "limit": 5})
        assert result.count("sha") == 5

    @patch("app.services.tools.git_history.get_github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("Not found")
        result = git_log.invoke({"repo": "org/repo", "ref": "main"})
        assert "Error" in result


class TestGitBlame:
    @patch("app.services.tools.git_history.graphql_query")
    def test_returns_formatted_blame(self, mock_gql):
        mock_gql.return_value = {
            "repository": {
                "object": {
                    "blame": {
                        "ranges": [
                            {
                                "startingLine": 1,
                                "endingLine": 2,
                                "commit": {
                                    "oid": "abc1234",
                                    "message": "Initial commit",
                                    "author": {"name": "dev1", "date": "2026-05-30T10:00:00Z"},
                                },
                            }
                        ]
                    }
                }
            }
        }

        result = git_blame.invoke({
            "repo": "org/repo", "path": "auth.py", "ref": "main",
            "start_line": 1, "end_line": 10,
        })
        assert "abc1234" in result
        assert "dev1" in result
        assert "Initial commit" in result

    @patch("app.services.tools.git_history.graphql_query")
    def test_error_handling(self, mock_gql):
        mock_gql.side_effect = Exception("GraphQL error")
        result = git_blame.invoke({
            "repo": "org/repo", "path": "auth.py", "ref": "main",
            "start_line": 1, "end_line": 5,
        })
        assert "Error" in result
