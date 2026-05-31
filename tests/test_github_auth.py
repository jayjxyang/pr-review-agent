"""Tests for dual-mode GitHub authentication."""

import time
from unittest.mock import patch, MagicMock, mock_open

import pytest


class TestAppModeAuth:
    @patch("app.services.github.requests.post")
    @patch("app.services.github.jwt.encode", return_value="fake-jwt")
    @patch("builtins.open", mock_open(read_data="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"))
    @patch("app.services.github.get_settings")
    def test_get_installation_token_calls_github_api(self, mock_settings, mock_jwt, mock_post):
        mock_settings.return_value.github_app_id = "123"
        mock_settings.return_value.github_app_private_key_path = "./test.pem"
        mock_settings.return_value.github_app_installation_id = "456"

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "token": "ghs_install_token_xxx",
            "expires_at": "2026-06-01T00:00:00Z",
        }

        from app.services.github import _get_installation_token, _token_cache
        _token_cache.clear()
        token = _get_installation_token()

        assert token == "ghs_install_token_xxx"
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "/installations/456/access_tokens" in url

    @patch("app.services.github.get_settings")
    def test_is_app_mode_true_when_app_id_set(self, mock_settings):
        mock_settings.return_value.github_app_id = "123"
        mock_settings.return_value.github_app_private_key_path = "./k.pem"
        mock_settings.return_value.github_app_installation_id = "456"

        from app.services.github import is_app_mode
        assert is_app_mode() is True

    @patch("app.services.github.get_settings")
    def test_is_app_mode_false_when_app_id_not_set(self, mock_settings):
        mock_settings.return_value.github_app_id = None

        from app.services.github import is_app_mode
        assert is_app_mode() is False


class TestPATModeAuth:
    @patch("app.services.github.get_settings")
    def test_pat_mode_uses_token_directly(self, mock_settings):
        mock_settings.return_value.github_app_id = None
        mock_settings.return_value.github_app_token = "ghp_testtoken123"

        from app.services.github import is_app_mode
        assert is_app_mode() is False


class TestTokenCaching:
    @patch("app.services.github.requests.post")
    @patch("app.services.github.jwt.encode", return_value="fake-jwt")
    @patch("builtins.open", mock_open(read_data="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"))
    @patch("app.services.github.get_settings")
    def test_token_is_cached_on_second_call(self, mock_settings, mock_jwt, mock_post):
        mock_settings.return_value.github_app_id = "123"
        mock_settings.return_value.github_app_private_key_path = "./test.pem"
        mock_settings.return_value.github_app_installation_id = "456"

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "token": "ghs_cached_token",
            "expires_at": "2026-06-01T00:00:00Z",
        }

        from app.services.github import _get_installation_token, _token_cache
        _token_cache.clear()

        token1 = _get_installation_token()
        token2 = _get_installation_token()

        assert token1 == token2
        assert mock_post.call_count == 1  # Only one API call
