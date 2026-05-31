# GitHub App Auth + Check Run Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate from PAT to GitHub App authentication with Check Run integration, extract scan_secrets as an independent security bypass, and add developer feedback collection via emoji reactions.

**Architecture:** Dual-mode auth (App/PAT) with automatic detection. Check Runs created via REST API (PyGithub's Check Run support is limited). `scan_secrets` runs before the agent graph and can independently veto via Check Run failure. Feedback collected by polling reactions on prior bot comments at review start.

**Tech Stack:** PyJWT + cryptography (JWT signing), requests (Check Run REST API), existing PyGithub/SQLAlchemy/Celery stack.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `.gitignore` | Modify | Add `*.pem` pattern |
| `requirements.txt` | Modify | Add PyJWT, cryptography |
| `app/core/config.py` | Modify | Add App auth settings |
| `app/services/github.py` | Modify | Dual-mode auth, replace `_github_client()` with `get_github_client()`, add `is_app_mode()`, `get_installation_token()` |
| `app/services/check_run.py` | Create | `create_check_run()`, `update_check_run()`, `compute_conclusion()`, severity→annotation mapping |
| `app/services/tools/quality.py` | Modify | Extract `run_secret_scan()` standalone function alongside existing `@tool` |
| `app/services/reviewer.py` | Modify | Update to use `get_github_client()` |
| `app/services/tools/code_read.py` | Modify | Update to use `get_github_client()` |
| `app/services/tools/pr_context.py` | Modify | Update to use `get_github_client()` |
| `app/services/tools/git_history.py` | Modify | Update to use `get_github_client()` |
| `app/services/tools/knowledge.py` | Modify | Update to use `get_github_client()` |
| `app/services/persistence.py` | Modify | Add `collect_feedback()`, update `query_review_history` output |
| `app/models/review.py` | Modify | Add `feedback` column to ReviewComment, add `github_comment_id` column |
| `app/agent/state.py` | Modify | Add `secret_findings` field |
| `app/tasks/review.py` | Modify | Orchestrate check_run → secret_scan → graph → conclusion → update |
| `alembic/versions/002_add_feedback_and_github_comment_id.py` | Create | Migration for new columns |
| `tests/test_github_auth.py` | Create | Tests for dual-mode auth |
| `tests/test_check_run.py` | Create | Tests for Check Run logic |
| `tests/test_secret_bypass.py` | Create | Tests for standalone secret scan |
| `tests/test_feedback.py` | Create | Tests for feedback collection |

---

### Task 1: Add dependencies and gitignore

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Add PyJWT and cryptography to requirements.txt**

```
PyJWT>=2.8
cryptography>=42.0
```

Append these two lines after `tenacity>=8.2` in `requirements.txt`.

- [ ] **Step 2: Add `*.pem` to .gitignore**

Add to `.gitignore` after the `# Environment variables` section:

```
# Private keys
*.pem
```

- [ ] **Step 3: Install dependencies**

Run: `pip install PyJWT>=2.8 cryptography>=42.0`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: add PyJWT, cryptography deps; gitignore *.pem"
```

---

### Task 2: Add App auth settings to config

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_config_auth.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_auth.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_auth.py -v`
Expected: FAIL — `Settings.__init__() got an unexpected keyword argument 'github_app_id'`

- [ ] **Step 3: Add App auth fields to Settings**

In `app/core/config.py`, add three fields after `github_app_token`:

```python
    github_app_token: str = ""

    # GitHub App auth (takes precedence over PAT when set)
    github_app_id: str | None = None
    github_app_private_key_path: str | None = None
    github_app_installation_id: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_auth.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_config_auth.py
git commit -m "feat: add GitHub App auth settings to config"
```

---

### Task 3: Implement dual-mode auth in github.py

**Files:**
- Modify: `app/services/github.py`
- Test: `tests/test_github_auth.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_auth.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_github_auth.py -v`
Expected: FAIL — `cannot import name '_get_installation_token' from 'app.services.github'`

- [ ] **Step 3: Implement dual-mode auth**

Rewrite `app/services/github.py`. Key changes:
- Add `import jwt, time` at top
- Replace `_github_client()` with `get_github_client()` (public name)
- Add `_token_cache` dict for installation token caching
- Add `_get_installation_token()` — JWT sign → POST to GitHub → cache token
- Add `is_app_mode() -> bool`
- Add `get_installation_token() -> str` (public, for Check Run REST calls)
- Keep `_github_client` as a deprecated alias for backwards compatibility during migration
- Update `graphql_query()` to use the right token

```python
import fnmatch
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
import requests
import yaml
from github import Github, GithubException

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Skip patterns (unchanged) ──

_SKIP_PATTERNS = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Pipfile.lock",
    "poetry.lock", "go.sum", "Cargo.lock", "*.lock",
    "*.min.js", "*.min.css", "*.pb.go", "*.pb.py",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico",
    "*.webp", "*.pdf", "*.woff", "*.woff2", "*.ttf", "*.eot",
]


@dataclass(frozen=True)
class FilePatch:
    filename: str
    patch: str


def _should_skip(filename: str, extra_patterns: list[str] | None = None) -> bool:
    name = filename.split("/")[-1]
    for pattern in _SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    if extra_patterns:
        for pattern in extra_patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
                return True
    return False


# ── GitHub App Auth ──

_token_cache: dict = {}  # {"token": str, "expires_at": float}


def is_app_mode() -> bool:
    """Check if GitHub App credentials are configured."""
    s = get_settings()
    return bool(s.github_app_id and s.github_app_private_key_path and s.github_app_installation_id)


def _create_jwt() -> str:
    """Create a JWT signed with the App's private key (10-minute expiry)."""
    s = get_settings()
    with open(s.github_app_private_key_path, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,       # issued at (clock drift buffer)
        "exp": now + (10 * 60),  # 10 minutes
        "iss": s.github_app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _get_installation_token() -> str:
    """Get an installation access token, using cache if valid."""
    # Return cached token if it expires more than 5 minutes from now
    if _token_cache.get("token"):
        if _token_cache["expires_at"] > time.time() + 300:
            return _token_cache["token"]

    s = get_settings()
    jwt_token = _create_jwt()
    response = requests.post(
        f"https://api.github.com/app/installations/{s.github_app_installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    # Parse expiry — GitHub returns ISO 8601 format
    from datetime import datetime, timezone
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    _token_cache["token"] = data["token"]
    _token_cache["expires_at"] = expires_at.timestamp()

    logger.info("installation_token_refreshed", expires_at=data["expires_at"])
    return data["token"]


def get_installation_token() -> str:
    """Public API: get the current installation token for REST API calls."""
    return _get_installation_token()


def get_github_client() -> Github:
    """Get an authenticated GitHub client. Uses App mode if configured, else PAT."""
    if is_app_mode():
        token = _get_installation_token()
        return Github(login_or_token=token)
    s = get_settings()
    if not s.github_app_token:
        raise RuntimeError("No GitHub credentials configured. Set GITHUB_APP_ID + key, or GITHUB_APP_TOKEN.")
    return Github(login_or_token=s.github_app_token)


# Backwards compatibility alias — will be removed after all call sites migrate
_github_client = get_github_client


# ── Existing functions (unchanged except _github_client -> get_github_client) ──

def get_pr_patches(repo_full_name: str, pr_number: int, *, extra_skip_patterns: list[str] | None = None) -> list[FilePatch]:
    # ... (body unchanged, already uses _github_client which is now aliased)

def get_pr_incremental_diff(repo_full_name: str, base_sha: str, head_sha: str) -> list[FilePatch]:
    # ... (body unchanged)

def get_pr_head_sha(repo_full_name: str, pr_number: int) -> str:
    # ... (body unchanged)

def get_repo_config(repo_full_name: str, ref: str) -> dict:
    # ... (body unchanged)

def graphql_query(query: str, variables: dict) -> dict:
    """Execute a GitHub GraphQL query — uses App or PAT token as appropriate."""
    if is_app_mode():
        token = _get_installation_token()
    else:
        token = get_settings().github_app_token
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL error: {result['errors'][0]['message']}")
    return result["data"]
```

Note: The `# ... (body unchanged)` comments mean keep the existing function bodies exactly as they are. They already call `_github_client()` which is now aliased to `get_github_client`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_github_auth.py -v`
Expected: all passed

- [ ] **Step 5: Run existing github tests to verify no regression**

Run: `python -m pytest tests/test_github.py tests/test_github_config.py tests/test_github_graphql.py tests/test_github_incremental.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/services/github.py tests/test_github_auth.py
git commit -m "feat: dual-mode GitHub auth (App + PAT fallback)"
```

---

### Task 4: Create Check Run module

**Files:**
- Create: `app/services/check_run.py`
- Create: `tests/test_check_run.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_run.py`:

```python
"""Tests for Check Run creation, update, and conclusion logic."""

from unittest.mock import patch, MagicMock

import pytest

from app.services.check_run import compute_conclusion, _severity_to_annotation_level


class TestComputeConclusion:
    def test_secret_failed_always_failure(self):
        assert compute_conclusion(secret_failed=True, risk_level="low", check_policy="advisory") == "failure"
        assert compute_conclusion(secret_failed=True, risk_level="low", check_policy="enforced") == "failure"

    def test_advisory_always_neutral(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="advisory") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="medium", check_policy="advisory") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="low", check_policy="advisory") == "neutral"

    def test_enforced_maps_risk_level(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="enforced") == "failure"
        assert compute_conclusion(secret_failed=False, risk_level="medium", check_policy="enforced") == "neutral"
        assert compute_conclusion(secret_failed=False, risk_level="low", check_policy="enforced") == "success"

    def test_default_policy_is_advisory(self):
        assert compute_conclusion(secret_failed=False, risk_level="high", check_policy="") == "neutral"


class TestSeverityToAnnotationLevel:
    def test_error_maps_to_failure(self):
        assert _severity_to_annotation_level("error") == "failure"

    def test_warning_maps_to_warning(self):
        assert _severity_to_annotation_level("warning") == "warning"

    def test_suggestion_maps_to_notice(self):
        assert _severity_to_annotation_level("suggestion") == "notice"

    def test_unknown_defaults_to_notice(self):
        assert _severity_to_annotation_level("info") == "notice"


class TestCreateCheckRun:
    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.post")
    def test_creates_check_run_in_app_mode(self, mock_post, mock_token, mock_app):
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {"id": 42}

        from app.services.check_run import create_check_run
        check_id = create_check_run("owner/repo", "abc123sha")

        assert check_id == 42
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["json"]
        assert body["name"] == "CodeLens Review"
        assert body["head_sha"] == "abc123sha"
        assert body["status"] == "in_progress"

    @patch("app.services.check_run.is_app_mode", return_value=False)
    def test_returns_none_in_pat_mode(self, mock_app):
        from app.services.check_run import create_check_run
        assert create_check_run("owner/repo", "abc123sha") is None


class TestUpdateCheckRun:
    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_updates_check_run_with_conclusion(self, mock_patch, mock_token, mock_app):
        mock_patch.return_value.status_code = 200

        from app.services.check_run import update_check_run
        result = {
            "risk_level": "high",
            "summary": "Found critical issues",
            "comments": [
                {"filename": "auth.py", "line": 10, "severity": "error", "comment": "SQL injection"},
            ],
        }
        update_check_run("owner/repo", 42, "failure", result)

        mock_patch.assert_called_once()
        body = mock_patch.call_args[1]["json"]
        assert body["conclusion"] == "failure"
        assert body["output"]["title"] == "AI Review \u2014 risk: high"
        assert len(body["output"]["annotations"]) == 1
        assert body["output"]["annotations"][0]["annotation_level"] == "failure"

    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_annotations_capped_at_50(self, mock_patch, mock_token, mock_app):
        """GitHub API limits annotations to 50 per update."""
        from app.services.check_run import update_check_run
        result = {
            "risk_level": "medium",
            "summary": "Many findings",
            "comments": [
                {"filename": f"f{i}.py", "line": i, "severity": "warning", "comment": f"Issue {i}"}
                for i in range(60)
            ],
        }
        update_check_run("owner/repo", 42, "neutral", result)

        body = mock_patch.call_args[1]["json"]
        assert len(body["output"]["annotations"]) == 50

    @patch("app.services.check_run.is_app_mode", return_value=True)
    @patch("app.services.check_run.get_installation_token", return_value="ghs_token")
    @patch("app.services.check_run.requests.patch")
    def test_includes_secret_findings_in_summary(self, mock_patch, mock_token, mock_app):
        from app.services.check_run import update_check_run
        result = {
            "risk_level": "high",
            "summary": "Review summary",
            "comments": [],
        }
        secret_findings = [
            {"filename": "config.py", "line": 5, "description": "AWS access key"},
        ]
        update_check_run("owner/repo", 42, "failure", result, secret_findings=secret_findings)

        body = mock_patch.call_args[1]["json"]
        assert "secret" in body["output"]["summary"].lower() or "AWS" in body["output"]["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_check_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.check_run'`

- [ ] **Step 3: Implement check_run.py**

Create `app/services/check_run.py`:

```python
"""Check Run management — create, update, compute conclusion."""

import requests

from app.services.github import is_app_mode, get_installation_token
from app.core.logging import get_logger

logger = get_logger(__name__)

_CHECK_NAME = "CodeLens Review"
_MAX_ANNOTATIONS = 50  # GitHub API limit per update

_SEVERITY_ANNOTATION = {
    "error": "failure",
    "warning": "warning",
    "suggestion": "notice",
}


def _severity_to_annotation_level(severity: str) -> str:
    return _SEVERITY_ANNOTATION.get(severity, "notice")


def compute_conclusion(*, secret_failed: bool, risk_level: str, check_policy: str) -> str:
    """Compute Check Run conclusion from review results and policy.

    Args:
        secret_failed: True if scan_secrets found issues (unconditional veto).
        risk_level: Agent's risk assessment ("low", "medium", "high").
        check_policy: Repo config — "advisory" (default, never blocks) or "enforced".
    """
    if secret_failed:
        return "failure"
    if check_policy == "enforced":
        return {"high": "failure", "medium": "neutral", "low": "success"}.get(risk_level, "neutral")
    return "neutral"


def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_installation_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_check_run(repo_full_name: str, head_sha: str) -> int | None:
    """Create a Check Run in 'in_progress' status. Returns check_run_id, or None in PAT mode."""
    if not is_app_mode():
        logger.debug("check_run_skipped_pat_mode")
        return None

    response = requests.post(
        f"https://api.github.com/repos/{repo_full_name}/check-runs",
        headers=_gh_headers(),
        json={
            "name": _CHECK_NAME,
            "head_sha": head_sha,
            "status": "in_progress",
        },
        timeout=30,
    )
    response.raise_for_status()
    check_id = response.json()["id"]
    logger.info("check_run_created", repo=repo_full_name, check_id=check_id)
    return check_id


def update_check_run(
    repo_full_name: str,
    check_run_id: int,
    conclusion: str,
    result: dict,
    *,
    secret_findings: list[dict] | None = None,
) -> None:
    """Update a Check Run with conclusion and review output."""
    if not is_app_mode():
        return

    risk_level = result.get("risk_level", "low")
    summary_text = result.get("summary", "")

    # Prepend secret findings to summary if present
    if secret_findings:
        secret_lines = "\n".join(f"- {f['filename']}:L{f['line']}: {f['description']}" for f in secret_findings)
        summary_text = f"**:rotating_light: Secrets detected (auto-blocked):**\n{secret_lines}\n\n{summary_text}"

    # Build annotations from comments (capped at 50)
    comments = result.get("comments", [])
    annotations = []
    for c in comments[:_MAX_ANNOTATIONS]:
        annotations.append({
            "path": c.get("filename", "unknown"),
            "start_line": c.get("line", 1),
            "end_line": c.get("line", 1),
            "annotation_level": _severity_to_annotation_level(c.get("severity", "suggestion")),
            "message": c.get("comment", ""),
        })

    response = requests.patch(
        f"https://api.github.com/repos/{repo_full_name}/check-runs/{check_run_id}",
        headers=_gh_headers(),
        json={
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": f"AI Review \u2014 risk: {risk_level}",
                "summary": summary_text,
                "annotations": annotations,
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    logger.info("check_run_updated", check_id=check_run_id, conclusion=conclusion)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_run.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/services/check_run.py tests/test_check_run.py
git commit -m "feat: Check Run create/update with conclusion logic"
```

---

### Task 5: Extract standalone run_secret_scan

**Files:**
- Modify: `app/services/tools/quality.py`
- Create: `tests/test_secret_bypass.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_secret_bypass.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_secret_bypass.py -v`
Expected: FAIL — `cannot import name 'run_secret_scan'`

- [ ] **Step 3: Extract run_secret_scan and update imports**

In `app/services/tools/quality.py`, add a standalone function and refactor the `@tool` to use it.

Add `run_secret_scan` function before the existing `scan_secrets` tool. Also update the import from `_github_client` to `get_github_client`:

```python
"""Quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import get_github_client

logger = get_logger(__name__)

_SECRET_PATTERNS = [
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[\w!@#$%^&*]{8,}', "password assignment"),
    (r'(?:sk-|sk_live_|sk_test_)[a-zA-Z0-9]{20,}', "OpenAI/Stripe key"),
    (r'ghp_[a-zA-Z0-9]{36,}', "GitHub personal access token"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', "private key"),
    (r'(?:bearer|authorization)\s*[=:]\s*["\']?[a-zA-Z0-9\-_.]{20,}', "bearer token"),
    (r'(?:secret|api_key|apikey|token)\s*[=:]\s*["\']?[\w\-]{16,}', "secret/key assignment"),
]


def run_secret_scan(repo: str, pr_number: int) -> list[dict]:
    """Standalone secret scan — returns list of finding dicts.

    Called before the agent graph as an independent security bypass.
    Returns: [{"filename": str, "line": int, "description": str}, ...]
    """
    try:
        pr = get_github_client().get_repo(repo).get_pull(pr_number)
    except Exception as e:
        logger.warning("secret_scan_error", error=str(e))
        return []

    findings = []
    for f in pr.get_files():
        patch = f.patch or ""
        for line_num, line in enumerate(patch.splitlines(), 1):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, description in _SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "filename": f.filename,
                        "line": line_num,
                        "description": description,
                    })
                    break

    return findings


@tool
def scan_secrets(repo: str, pr_number: int) -> str:
    """Scan the PR diff for potential hardcoded secrets, API keys, tokens, or passwords.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number to scan.
    """
    findings = run_secret_scan(repo, pr_number)
    if not findings:
        return "No secrets detected in the PR diff."
    lines = [f"- {f['filename']}:L{f['line']}: {f['description']}" for f in findings]
    return f"Potential secrets found ({len(findings)}):\n" + "\n".join(lines)
```

Also update the remaining tools in this file to use `get_github_client` instead of `_github_client`:
- `check_test_coverage`: `_github_client()` → `get_github_client()`
- `get_ci_status`: `_github_client()` → `get_github_client()`
- `get_ci_logs`: `_github_client()` → `get_github_client()`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_secret_bypass.py -v`
Expected: all passed

- [ ] **Step 5: Run existing quality tool tests to verify no regression**

Run: `python -m pytest tests/test_tools_quality.py -v`

Note: These tests mock `_github_client`. Since `_github_client` is now aliased to `get_github_client`, the mock path needs to be updated in the test file. If tests fail, update `@patch("app.services.tools.quality._github_client")` to `@patch("app.services.tools.quality.get_github_client")` in `tests/test_tools_quality.py`.

- [ ] **Step 6: Commit**

```bash
git add app/services/tools/quality.py tests/test_secret_bypass.py tests/test_tools_quality.py
git commit -m "feat: extract run_secret_scan as standalone security bypass"
```

---

### Task 6: Update remaining call sites from _github_client to get_github_client

**Files:**
- Modify: `app/services/reviewer.py`
- Modify: `app/services/tools/code_read.py`
- Modify: `app/services/tools/pr_context.py`
- Modify: `app/services/tools/git_history.py`
- Modify: `app/services/tools/knowledge.py`

- [ ] **Step 1: Update imports in all files**

In each file, replace:
```python
from app.services.github import _github_client
```
with:
```python
from app.services.github import get_github_client
```

And replace all `_github_client()` calls with `get_github_client()`.

Files to update:
- `app/services/reviewer.py`: line 4
- `app/services/tools/code_read.py`: find the import and all usages
- `app/services/tools/pr_context.py`: find the import and all usages
- `app/services/tools/git_history.py`: find the import and all usages (note: `git_history.py` may use `graphql_query` instead)
- `app/services/tools/knowledge.py`: line 11

- [ ] **Step 2: Update test mock paths**

In test files that mock `_github_client`, update the mock path:
- `tests/test_github.py`: `@patch("app.services.github._github_client")` → `@patch("app.services.github.get_github_client")`
- `tests/test_reviewer.py`: `@patch("app.services.reviewer._github_client")` → `@patch("app.services.reviewer.get_github_client")`
- `tests/test_tools_code_read.py`: update mock path
- `tests/test_tools_knowledge.py`: update mock path
- Any other test files referencing `_github_client`

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add app/services/ tests/
git commit -m "refactor: migrate all call sites from _github_client to get_github_client"
```

---

### Task 7: Add feedback column and github_comment_id to ReviewComment

**Files:**
- Modify: `app/models/review.py`
- Create: `alembic/versions/002_add_feedback_and_github_comment_id.py`
- Modify: `app/services/reviewer.py` (store github_comment_id after posting)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_persistence.py`:

```python
class TestFeedbackColumn:
    def test_review_comment_has_feedback_field(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="OK", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="issue",
            feedback="false_positive",
            github_comment_id=12345,
        )
        session.add(comment)
        session.commit()

        loaded = session.get(ReviewComment, comment.id)
        assert loaded.feedback == "false_positive"
        assert loaded.github_comment_id == 12345
        session.close()

    def test_feedback_defaults_to_none(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="OK", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="issue",
        )
        session.add(comment)
        session.commit()

        loaded = session.get(ReviewComment, comment.id)
        assert loaded.feedback is None
        assert loaded.github_comment_id is None
        session.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_persistence.py::TestFeedbackColumn -v`
Expected: FAIL — `TypeError: ...unexpected keyword argument 'feedback'`

- [ ] **Step 3: Add columns to ReviewComment model**

In `app/models/review.py`, add to `ReviewComment` class after `resolved`:

```python
    resolved = Column(Boolean, default=False)
    feedback = Column(String(20), nullable=True)         # "false_positive" | "helpful" | None
    github_comment_id = Column(Integer, nullable=True)   # GitHub's comment ID for reaction lookup
```

- [ ] **Step 4: Create alembic migration**

Create `alembic/versions/002_add_feedback_and_github_comment_id.py`:

```python
"""Add feedback and github_comment_id columns to review_comments.

Revision ID: 002
Revises: 001
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"


def upgrade():
    op.add_column("review_comments", sa.Column("feedback", sa.String(20), nullable=True))
    op.add_column("review_comments", sa.Column("github_comment_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("review_comments", "github_comment_id")
    op.drop_column("review_comments", "feedback")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/models/review.py alembic/versions/002_add_feedback_and_github_comment_id.py tests/test_persistence.py
git commit -m "feat: add feedback and github_comment_id columns to ReviewComment"
```

---

### Task 8: Store github_comment_id when posting reviews

**Files:**
- Modify: `app/services/reviewer.py`
- Modify: `app/services/persistence.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reviewer.py`:

```python
class TestPostReviewReturnsCommentIds:
    @patch("app.services.reviewer.get_github_client")
    def test_returns_comment_ids_from_review(self, mock_client):
        mock_pr = MagicMock()
        mock_review = MagicMock()
        # GitHub API returns review with comments that have IDs
        mock_comment_1 = MagicMock()
        mock_comment_1.id = 1001
        mock_comment_2 = MagicMock()
        mock_comment_2.id = 1002
        mock_review.get_review_comments.return_value = [mock_comment_1, mock_comment_2]
        mock_pr.create_review.return_value = mock_review
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Issues found",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue 1"},
                {"filename": "b.py", "line": 20, "severity": "error", "comment": "Issue 2"},
            ],
        }

        from app.services.reviewer import post_review
        comment_ids = post_review("org/repo", 42, result)

        assert comment_ids == [1001, 1002]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reviewer.py::TestPostReviewReturnsCommentIds -v`
Expected: FAIL — `post_review` currently returns None

- [ ] **Step 3: Update post_review to return GitHub comment IDs**

In `app/services/reviewer.py`, modify `post_review` to:
1. Capture the return value of `pr.create_review()`
2. Get review comments from the created review
3. Return list of comment IDs

```python
def post_review(repo_full_name: str, pr_number: int, result: dict) -> list[int]:
    """Post the agent's review to GitHub as a PR review with inline comments.

    Returns:
        List of GitHub comment IDs for the posted inline comments (for reaction tracking).
    """
    gh = get_github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)

    summary = result.get("summary", "")
    all_comments = result.get("comments", [])
    risk_level = result.get("risk_level", "low")

    resolved = [c for c in all_comments if c.get("severity") == "resolved"]
    active_comments = [c for c in all_comments if c.get("severity") != "resolved"]

    if not active_comments and not summary and not resolved:
        return []

    body = f"## AI Review (risk: {risk_level})\n\n{summary}"
    if resolved:
        body += f"\n\n**Re-review:** {len(resolved)} prior issue(s) resolved."

    gh_comments = []
    for c in active_comments:
        emoji = _SEVERITY_EMOJI.get(c.get("severity", "suggestion"), "\U0001f535")
        gh_comments.append({
            "path": c.get("filename", "unknown"),
            "line": c.get("line", 1),
            "side": "RIGHT",
            "body": f"{emoji} **{c.get('severity', 'suggestion')}**: {c.get('comment', '')}",
        })

    try:
        if gh_comments:
            review = pr.create_review(body=body, event="COMMENT", comments=gh_comments)
            # Extract GitHub comment IDs for reaction tracking
            try:
                return [rc.id for rc in review.get_review_comments()]
            except Exception:
                return []
        else:
            pr.create_issue_comment(body)
            return []
    except Exception as exc:
        logger.warning("inline_review_failed_fallback", error=str(exc))
        fallback = body + "\n\n### Findings\n\n"
        for c in active_comments:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "\U0001f535")
            fallback += f"- {emoji} **{c.get('filename', 'unknown')}:{c.get('line', '?')}** \u2014 {c.get('comment', '')}\n"
        pr.create_issue_comment(fallback)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reviewer.py -v`
Expected: all passed

- [ ] **Step 5: Add update_github_comment_ids to persistence.py**

Add to `app/services/persistence.py`:

```python
def update_github_comment_ids(review_id: int, github_comment_ids: list[int]) -> None:
    """Store GitHub comment IDs on ReviewComment rows for reaction tracking.

    Maps by position — assumes github_comment_ids[i] corresponds to the i-th
    non-resolved comment in the review (same order as posted).
    """
    if not github_comment_ids:
        return
    try:
        session = SessionLocal()
        try:
            comments = (
                session.query(ReviewComment)
                .filter(ReviewComment.review_id == review_id)
                .order_by(ReviewComment.id.asc())
                .all()
            )
            for i, comment in enumerate(comments):
                if i < len(github_comment_ids):
                    comment.github_comment_id = github_comment_ids[i]
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:
        logger.warning("update_github_comment_ids_failed", error=str(exc))
```

- [ ] **Step 6: Commit**

```bash
git add app/services/reviewer.py app/services/persistence.py tests/test_reviewer.py
git commit -m "feat: capture GitHub comment IDs for reaction tracking"
```

---

### Task 9: Implement feedback collection

**Files:**
- Modify: `app/services/persistence.py`
- Create: `tests/test_feedback.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feedback.py`:

```python
"""Tests for feedback collection via GitHub reactions."""

from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, JSON
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.review import Review, ReviewComment, AgentTrace
from app.services.persistence import collect_feedback


def _setup_test_db(monkeypatch):
    original_type = AgentTrace.__table__.c.tool_params.type
    AgentTrace.__table__.c.tool_params.type = JSON()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    AgentTrace.__table__.c.tool_params.type = original_type
    monkeypatch.setattr("app.services.persistence.SessionLocal", test_session_factory)
    return test_session_factory


class TestCollectFeedback:
    @patch("app.services.persistence.get_github_client")
    def test_thumbs_down_marks_false_positive(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="medium",
            summary="Issues", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="Issue",
            github_comment_id=1001,
        )
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        # Mock GitHub reaction API
        mock_reaction = MagicMock()
        mock_reaction.content = "-1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [mock_reaction]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "false_positive"
        session.close()

    @patch("app.services.persistence.get_github_client")
    def test_thumbs_up_marks_helpful(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="OK", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="suggestion", comment="Tip",
            github_comment_id=2001,
        )
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        mock_reaction = MagicMock()
        mock_reaction.content = "+1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [mock_reaction]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "helpful"
        session.close()

    @patch("app.services.persistence.get_github_client")
    def test_both_reactions_thumbs_down_wins(self, mock_client, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="OK", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="Issue",
            github_comment_id=3001,
        )
        session.add(comment)
        session.commit()
        comment_id = comment.id
        session.close()

        up = MagicMock()
        up.content = "+1"
        down = MagicMock()
        down.content = "-1"
        mock_pr = MagicMock()
        mock_review_comment = MagicMock()
        mock_review_comment.get_reactions.return_value = [up, down]
        mock_pr.get_review_comment.return_value = mock_review_comment
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        collect_feedback("org/repo", 42)

        session = session_factory()
        loaded = session.get(ReviewComment, comment_id)
        assert loaded.feedback == "false_positive"
        session.close()

    def test_skips_comments_without_github_comment_id(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="OK", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        comment = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="Issue",
            github_comment_id=None,  # No GitHub ID
        )
        session.add(comment)
        session.commit()
        session.close()

        # Should not raise, should be a no-op
        collect_feedback("org/repo", 42)

    def test_no_prior_review_is_noop(self, monkeypatch):
        _setup_test_db(monkeypatch)
        # Should not raise
        collect_feedback("org/repo", 999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_feedback.py -v`
Expected: FAIL — `cannot import name 'collect_feedback'`

- [ ] **Step 3: Implement collect_feedback**

Add to `app/services/persistence.py`:

```python
from app.services.github import get_github_client

# ... existing code ...

def collect_feedback(repo: str, pr_number: int) -> None:
    """Collect 👍/👎 reactions on bot comments from the most recent review.

    Checks GitHub reactions on each comment that has a github_comment_id.
    Updates the feedback column: "false_positive" (👎) or "helpful" (👍).
    If both present, 👎 wins (conservative).
    """
    try:
        session = SessionLocal()
        try:
            # Find most recent review for this PR
            review = (
                session.query(Review)
                .filter(Review.repo == repo, Review.pr_number == pr_number)
                .order_by(Review.created_at.desc())
                .first()
            )
            if not review:
                return

            # Get comments with GitHub IDs that haven't been feedback-checked yet
            comments = (
                session.query(ReviewComment)
                .filter(
                    ReviewComment.review_id == review.id,
                    ReviewComment.github_comment_id.isnot(None),
                    ReviewComment.feedback.is_(None),
                )
                .all()
            )
            if not comments:
                return

            gh = get_github_client()
            pr = gh.get_repo(repo).get_pull(pr_number)

            for comment in comments:
                try:
                    gh_comment = pr.get_review_comment(comment.github_comment_id)
                    reactions = list(gh_comment.get_reactions())

                    has_thumbs_down = any(r.content == "-1" for r in reactions)
                    has_thumbs_up = any(r.content == "+1" for r in reactions)

                    if has_thumbs_down:
                        comment.feedback = "false_positive"
                    elif has_thumbs_up:
                        comment.feedback = "helpful"
                except Exception as exc:
                    logger.debug("reaction_fetch_failed", comment_id=comment.github_comment_id, error=str(exc))
                    continue

            session.commit()
            logger.info("feedback_collected", repo=repo, pr=pr_number)

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as exc:
        logger.warning("collect_feedback_failed", error=str(exc), repo=repo, pr=pr_number)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_feedback.py -v`
Expected: all passed

- [ ] **Step 5: Update query_review_history to include feedback**

In `app/services/tools/knowledge.py`, update `_query_review_history_impl` to show feedback in output:

```python
    for rc in results:
        feedback_tag = f" [FEEDBACK: {rc.feedback}]" if rc.feedback else ""
        output.append(
            f"- PR #{rc.review.pr_number} | {rc.filename}:L{rc.line} [{rc.severity}]{feedback_tag}\n"
            f"  {rc.comment}"
        )
```

- [ ] **Step 6: Commit**

```bash
git add app/services/persistence.py app/services/tools/knowledge.py tests/test_feedback.py
git commit -m "feat: collect developer feedback via GitHub reactions"
```

---

### Task 10: Add secret_findings to graph state

**Files:**
- Modify: `app/agent/state.py`

- [ ] **Step 1: Add secret_findings field**

In `app/agent/state.py`, add after `repo_config`:

```python
    # Per-repo config
    repo_config: dict
    # Pre-graph secret scan findings
    secret_findings: list[dict]
```

- [ ] **Step 2: Run existing graph tests to verify no regression**

Run: `python -m pytest tests/test_agent_graph.py -v`
Expected: all passed (TypedDict fields are optional by default)

- [ ] **Step 3: Commit**

```bash
git add app/agent/state.py
git commit -m "feat: add secret_findings to ReviewState"
```

---

### Task 11: Orchestrate everything in run_review

**Files:**
- Modify: `app/tasks/review.py`
- Modify: `tests/test_review_task.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_review_task.py`:

```python
class TestRunReviewCheckRun:
    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_creates_and_updates_check_run(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-check-run"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        mock_create.assert_called_once_with("org/repo", "abc123")
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1] == 42  # check_run_id

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan")
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_secret_scan_forces_failure(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_scan.return_value = [
            {"filename": "config.py", "line": 5, "description": "AWS access key"},
        ]
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-secret-veto"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        # Check Run should be updated with failure conclusion
        update_args = mock_update.call_args
        assert update_args[0][2] == "failure"  # conclusion

    @patch("app.tasks.review.update_github_comment_ids")
    @patch("app.tasks.review.post_review", return_value=[])
    @patch("app.tasks.review.update_check_run")
    @patch("app.tasks.review.create_check_run", return_value=42)
    @patch("app.tasks.review.collect_feedback")
    @patch("app.tasks.review.run_secret_scan", return_value=[])
    @patch("app.tasks.review.save_review", return_value=1)
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_collects_feedback_before_review(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve,
        mock_save, mock_scan, mock_feedback, mock_create, mock_update,
        mock_post, mock_update_ids,
    ):
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [],
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-feedback"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "org/repo", 42)

        mock_feedback.assert_called_once_with("org/repo", 42)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_review_task.py::TestRunReviewCheckRun -v`
Expected: FAIL — `cannot import name 'create_check_run'`

- [ ] **Step 3: Rewrite run_review with full orchestration**

Update `app/tasks/review.py`:

```python
"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task
from langgraph.errors import GraphRecursionError

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha, get_repo_config
from app.services.persistence import (
    save_review, get_last_review, resolve_comments,
    collect_feedback, update_github_comment_ids,
)
from app.services.check_run import create_check_run, update_check_run, compute_conclusion
from app.services.tools.quality import run_secret_scan

logger = get_logger(__name__)


@celery_app.task(
    name="tasks.run_review",
    bind=True,
    ignore_result=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def run_review(self: Task, repo_full_name: str, pr_number: int):
    """
    End-to-end PR review pipeline:
    1. Collect feedback on prior review (👍/👎 reactions)
    2. Create Check Run (in_progress)
    3. Run independent secret scan (security bypass)
    4. Build graph and invoke with PR context
    5. Compute conclusion and update Check Run
    6. Persist results, post review
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        # 0. Collect feedback from prior review reactions
        collect_feedback(repo_full_name, pr_number)

        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Load per-repo config
        repo_config = get_repo_config(repo_full_name, ref)
        ignore_paths = repo_config.get("ignore_paths", [])
        if ignore_paths:
            log.info("repo_config_ignore_paths", patterns=ignore_paths)

        # 1. Create Check Run (returns None in PAT mode)
        check_run_id = create_check_run(repo_full_name, ref)

        # 2. Independent secret scan (before graph, cannot be overridden by LLM)
        secret_findings = run_secret_scan(repo_full_name, pr_number)
        secret_failed = len(secret_findings) > 0
        if secret_failed:
            log.warning("secrets_detected", count=len(secret_findings))

        # 3. Re-review detection
        last_review = get_last_review(repo_full_name, pr_number)
        prior_comments = []
        last_reviewed_sha = ""

        if last_review:
            last_reviewed_sha = last_review["reviewed_sha"]
            prior_comments = last_review["comments"]
            log.info(
                "re_review_detected",
                last_sha=last_reviewed_sha[:7],
                unresolved_comments=len(prior_comments),
            )

        # 4. Build and invoke graph
        graph = build_review_graph()
        thread_id = f"{repo_full_name}:{pr_number}:{ref}"
        config = {"configurable": {"thread_id": thread_id}}

        initial_state = {
            "messages": [],
            "repo": repo_full_name,
            "pr_number": pr_number,
            "ref": ref,
            "risk_level": "",
            "summary": "",
            "comments": [],
            "escalated": False,
            "escalate_reason": "",
            "round_count": 0,
            "total_input_tokens": 0,
            "tool_call_history": [],
            "traces": [],
            "compress_count": 0,
            "prior_comments": prior_comments,
            "last_reviewed_sha": last_reviewed_sha,
            "repo_config": repo_config,
            "secret_findings": secret_findings,
        }

        try:
            result = graph.invoke(initial_state, config=config)
        except GraphRecursionError:
            log.error("graph_recursion_limit_hit")
            result = {
                "risk_level": "low",
                "summary": "Review terminated: graph recursion limit reached.",
                "comments": [],
                "escalated": False,
                "traces": [],
            }

        log.info(
            "agent_complete",
            risk=result["risk_level"],
            escalated=result["escalated"],
            comments=len(result["comments"]),
            traces=len(result.get("traces", [])),
        )

        # 5. Compute conclusion and update Check Run
        check_policy = repo_config.get("check_policy", "advisory")
        conclusion = compute_conclusion(
            secret_failed=secret_failed,
            risk_level=result["risk_level"],
            check_policy=check_policy,
        )
        if check_run_id:
            update_check_run(
                repo_full_name, check_run_id, conclusion, result,
                secret_findings=secret_findings if secret_failed else None,
            )
            log.info("check_run_completed", conclusion=conclusion)

        # 6. Extract resolved prior comment IDs
        resolved_ids = [
            c["prior_comment_id"]
            for c in result.get("comments", [])
            if c.get("severity") == "resolved" and c.get("prior_comment_id")
        ]

        # 7. Persist to PostgreSQL
        save_result = dict(result)
        save_result["comments"] = [c for c in result.get("comments", []) if c.get("severity") != "resolved"]
        review_id = save_review(repo_full_name, pr_number, ref, save_result)

        if resolved_ids:
            resolve_comments(resolved_ids)
            log.info("prior_comments_resolved", count=len(resolved_ids))

        # 8. Post review to GitHub and store comment IDs
        github_comment_ids = post_review(repo_full_name, pr_number, result)
        if review_id and github_comment_ids:
            update_github_comment_ids(review_id, github_comment_ids)

        log.info("review_posted")

    except Exception as exc:
        # Update Check Run to failure on unexpected error
        try:
            if check_run_id:
                update_check_run(
                    repo_full_name, check_run_id, "failure",
                    {"risk_level": "unknown", "summary": f"Review failed: {exc}", "comments": []},
                )
        except Exception:
            pass
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `python -m pytest tests/test_review_task.py::TestRunReviewCheckRun -v`
Expected: all passed

- [ ] **Step 5: Run existing review task tests to verify no regression**

Run: `python -m pytest tests/test_review_task.py -v`

Note: Existing tests will need additional mock patches for the new imports (`create_check_run`, `update_check_run`, `collect_feedback`, `run_secret_scan`, `update_github_comment_ids`). Add `@patch` decorators for these to each existing test class. Set defaults:
- `create_check_run` → `return_value=None`
- `update_check_run` → no-op
- `collect_feedback` → no-op
- `run_secret_scan` → `return_value=[]`
- `update_github_comment_ids` → no-op
- `post_review` → `return_value=[]` (was no return before)

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/tasks/review.py tests/test_review_task.py
git commit -m "feat: orchestrate Check Run + secret bypass + feedback in review pipeline"
```

---

### Task 12: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all passed

- [ ] **Step 2: Verify private key is gitignored**

Run: `git status`
Expected: `private-key.pem` should NOT appear in untracked files

- [ ] **Step 3: Verify imports are clean**

Run: `python -c "from app.tasks.review import run_review; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "chore: integration fixups for GitHub App migration"
```
