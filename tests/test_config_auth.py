"""Tests for GitHub App auth config detection."""

import os
from unittest.mock import patch

from app.core.config import Settings


class TestAuthModeDetection:
    def test_app_mode_when_all_app_settings_present(self):
        s = Settings(
            github_app_id="123",
            github_app_private_key_path="./test.pem",
            github_app_installation_id="456",
            github_webhook_secret="secret",
            ai_gateway_key="key",
        )
        assert s.github_app_id == "123"
        assert s.github_app_installation_id == "456"

    def test_pat_mode_when_app_settings_missing(self):
        s = Settings(
            github_app_token="ghp_xxx",
            github_webhook_secret="secret",
            ai_gateway_key="key",
        )
        assert s.github_app_id is None
        assert s.github_app_token == "ghp_xxx"

    def test_neither_mode_raises(self):
        """Settings with no auth should still be constructible
        (validation happens at runtime in github.py)."""
        s = Settings(
            github_webhook_secret="secret",
            ai_gateway_key="key",
        )
        assert s.github_app_id is None
        assert s.github_app_token == ""
