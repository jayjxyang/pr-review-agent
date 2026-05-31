"""Tests for get_repo_config — fetches .ai-review/config.yml from a repo."""

from unittest.mock import patch, MagicMock

import pytest
from github import GithubException, UnknownObjectException

from app.services.github import get_repo_config


@pytest.fixture(autouse=True)
def _clear_github_cache():
    yield


@patch("app.services.github._github_client")
def test_returns_parsed_config(mock_client):
    """Valid YAML config is parsed and returned as a dict."""
    yaml_content = b"ignore_paths:\n  - 'generated/**'\ntech_stack:\n  language: python\n  framework: fastapi\n"
    mock_repo = MagicMock()
    mock_file = MagicMock()
    mock_file.decoded_content = yaml_content
    mock_repo.get_contents.return_value = mock_file
    mock_client.return_value.get_repo.return_value = mock_repo

    result = get_repo_config("owner/repo", "abc123")

    assert result == {
        "ignore_paths": ["generated/**"],
        "tech_stack": {"language": "python", "framework": "fastapi"},
    }
    mock_repo.get_contents.assert_called_once_with(".ai-review/config.yml", ref="abc123")


@patch("app.services.github._github_client")
def test_returns_empty_on_missing_file(mock_client):
    """Missing config file returns empty dict (no error)."""
    mock_repo = MagicMock()
    mock_repo.get_contents.side_effect = UnknownObjectException(
        status=404, data={"message": "Not Found"}, headers={}
    )
    mock_client.return_value.get_repo.return_value = mock_repo

    result = get_repo_config("owner/repo", "abc123")

    assert result == {}


@patch("app.services.github._github_client")
def test_returns_empty_on_invalid_yaml(mock_client):
    """Invalid YAML returns empty dict (no error)."""
    mock_repo = MagicMock()
    mock_file = MagicMock()
    mock_file.decoded_content = b"{{invalid: yaml: [unterminated"
    mock_repo.get_contents.return_value = mock_file
    mock_client.return_value.get_repo.return_value = mock_repo

    result = get_repo_config("owner/repo", "abc123")

    assert result == {}
