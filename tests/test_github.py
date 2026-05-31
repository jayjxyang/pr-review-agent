"""Tests for get_pr_patches with extra skip patterns."""

from unittest.mock import patch, MagicMock

import pytest

from app.services.github import get_pr_patches


@pytest.fixture(autouse=True)
def _clear_github_cache():
    from app.services.github import _github_client
    _github_client.cache_clear()
    yield
    _github_client.cache_clear()


@patch("app.services.github._github_client")
def test_get_pr_patches_with_extra_skip_patterns(mock_client):
    """Extra skip patterns filter additional files."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    file_app = MagicMock()
    file_app.filename = "app/main.py"
    file_app.patch = "@@ -1 +1 @@\n-old\n+new"

    file_generated = MagicMock()
    file_generated.filename = "generated/models.py"
    file_generated.patch = "@@ -1 +1 @@\n-old\n+new"

    file_docs = MagicMock()
    file_docs.filename = "docs/README.md"
    file_docs.patch = "@@ -1 +1 @@\n-old\n+new"

    mock_pr.get_files.return_value = [file_app, file_generated, file_docs]
    mock_repo.get_pull.return_value = mock_pr
    mock_client.return_value.get_repo.return_value = mock_repo

    result = get_pr_patches("owner/repo", 1, extra_skip_patterns=["generated/**", "docs/**"])

    assert len(result) == 1
    assert result[0].filename == "app/main.py"


@patch("app.services.github._github_client")
def test_get_pr_patches_without_extra_skip_patterns(mock_client):
    """Without extra skip patterns, behavior is unchanged (backwards compatible)."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    file_app = MagicMock()
    file_app.filename = "app/main.py"
    file_app.patch = "@@ -1 +1 @@\n-old\n+new"

    file_generated = MagicMock()
    file_generated.filename = "generated/models.py"
    file_generated.patch = "@@ -1 +1 @@\n-old\n+new"

    mock_pr.get_files.return_value = [file_app, file_generated]
    mock_repo.get_pull.return_value = mock_pr
    mock_client.return_value.get_repo.return_value = mock_repo

    result = get_pr_patches("owner/repo", 1)

    assert len(result) == 2
