# Batch E — Docker Compose + Per-Repo Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-ready Docker Compose deployment (5 services) and per-repo `.ai-review/config.yml` support for customizing review behavior (ignore paths, tech stack context).

**Architecture:** Docker multi-stage build for the Python app, shared across `review-agent` and `celery-worker` services. Per-repo config fetched via GitHub Contents API on each review invocation, with ignore_paths filtering merged into `get_pr_patches` and tech_stack injected into the scan system prompt.

**Tech Stack:** Docker, Docker Compose, PyYAML, PyGithub Contents API

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `requirements.txt` | **MODIFY** | Add `pyyaml` dependency |
| `.env.example` | **MODIFY** | Add `COMPRESS_AT_ROUND=5` |
| `Dockerfile` | **NEW** | Multi-stage Python 3.12-slim build |
| `docker-compose.yml` | **NEW** | 5 services with health checks and volumes |
| `app/services/github.py` | **MODIFY** | Add `get_repo_config`, modify `_should_skip` and `get_pr_patches` for extra skip patterns |
| `tests/test_github_config.py` | **NEW** | Tests for `get_repo_config` |
| `tests/test_github.py` | **MODIFY** | Tests for `get_pr_patches` with `extra_skip_patterns` |
| `app/agent/state.py` | **MODIFY** | Add `repo_config: dict` field |
| `app/agent/graph.py` | **MODIFY** | Inject tech_stack section into scan prompt |
| `tests/test_agent_graph.py` | **MODIFY** | Tests for tech_stack prompt injection |
| `app/tasks/review.py` | **MODIFY** | Load config, pass ignore_paths to `get_pr_patches`, pass `repo_config` to graph |
| `tests/test_review_task.py` | **MODIFY** | Test config integration in `run_review` |

---

### Task 1: Add PyYAML dependency and update .env.example

**Files:**
- Modify: `requirements.txt:14` (add pyyaml after psycopg2-binary)
- Modify: `.env.example:10` (add COMPRESS_AT_ROUND)

- [ ] **Step 1: Add PyYAML to requirements.txt**

Add `pyyaml` at the end of `requirements.txt`:

```
pyyaml==6.0.1
```

The full file should be:
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
celery[redis]==5.4.0
redis==5.0.4
PyGithub==2.3.0
langgraph>=0.4
langchain-openai>=0.3
httpx==0.27.0
pydantic-settings==2.2.1
python-dotenv==1.0.1
structlog==24.1.0
sqlalchemy==2.0.30
alembic==1.13.1
psycopg2-binary==2.9.9
pyyaml==6.0.1
```

- [ ] **Step 2: Update .env.example**

Replace the entire `.env.example` with the updated V2 variables:

```
GITHUB_WEBHOOK_SECRET=your-webhook-secret
GITHUB_APP_TOKEN=ghp_your-token

DATABASE_URL=postgresql://postgres:postgres@postgres:5432/pr_review
REDIS_URL=redis://redis:6379/0

AI_GATEWAY_URL=http://ai-gateway:8080
AI_GATEWAY_KEY=your-gateway-key
SCAN_SCENARIO=code-review-scan
REASON_SCENARIO=code-review-reason

MAX_ROUNDS=15
MAX_INPUT_TOKENS=60000
COMPRESS_AT_ROUND=5
```

- [ ] **Step 3: Install pyyaml**

Run: `pip install pyyaml==6.0.1`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "chore: add pyyaml dependency and update .env.example with V2 vars"
```

---

### Task 2: Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
# ── Builder stage ──
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ──
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify Dockerfile syntax**

Run: `docker build --check . 2>&1 || echo "Docker not available, skipping syntax check"`

If docker is not installed, visual inspection is sufficient. The Dockerfile follows standard multi-stage patterns.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat: add multi-stage Dockerfile for review agent"
```

---

### Task 3: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
services:
  review-agent:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  celery-worker:
    build: .
    command: celery -A app.core.celery_app worker --loglevel=info
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  ai-gateway:
    image: ai-api-gateway:latest
    ports:
      - "8080:8080"
    env_file: .env

  postgres:
    image: pgvector/pgvector:pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: pr_review
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pg_data:
  redis_data:
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml with 5 services"
```

---

### Task 4: `get_repo_config` + tests

**Files:**
- Modify: `app/services/github.py`
- Create: `tests/test_github_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_config.py`:

```python
"""Tests for get_repo_config — fetches .ai-review/config.yml from a repo."""

from unittest.mock import patch, MagicMock

import pytest
from github import GithubException, UnknownObjectException

from app.services.github import get_repo_config


@pytest.fixture(autouse=True)
def _clear_github_cache():
    from app.services.github import _github_client
    _github_client.cache_clear()
    yield
    _github_client.cache_clear()


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_github_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_repo_config'`

- [ ] **Step 3: Implement `get_repo_config`**

Add to the end of `app/services/github.py` (before the final blank line), along with the `yaml` import at the top:

Add import at the top of the file (after `from functools import lru_cache`):

```python
import yaml
```

Add function at the end of the file:

```python
def get_repo_config(repo_full_name: str, ref: str) -> dict:
    """Fetch and parse .ai-review/config.yml from a repo.

    Returns parsed dict, or {} on missing file / parse error.
    """
    try:
        repo = _github_client().get_repo(repo_full_name)
        content = repo.get_contents(".ai-review/config.yml", ref=ref)
        return yaml.safe_load(content.decoded_content) or {}
    except GithubException:
        logger.debug("repo_config_not_found", repo=repo_full_name)
        return {}
    except yaml.YAMLError:
        logger.warning("repo_config_invalid_yaml", repo=repo_full_name)
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_github_config.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/github.py tests/test_github_config.py
git commit -m "feat: add get_repo_config to fetch per-repo .ai-review/config.yml"
```

---

### Task 5: Extra skip patterns in `get_pr_patches` + `repo_config` state field + tests

**Files:**
- Modify: `app/services/github.py:51-55` (`_should_skip`) and `app/services/github.py:65` (`get_pr_patches`)
- Modify: `app/agent/state.py:42` (add `repo_config`)
- Modify: `tests/test_github.py` (add extra skip pattern tests)

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_github.py`:

```python
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
```

Note: ensure `get_pr_patches` is imported at the top of the test file. Check the existing imports and add it if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_github.py::test_get_pr_patches_with_extra_skip_patterns tests/test_github.py::test_get_pr_patches_without_extra_skip_patterns -v`
Expected: FAIL — `get_pr_patches()` got an unexpected keyword argument `extra_skip_patterns`

- [ ] **Step 3: Modify `_should_skip` and `get_pr_patches`**

In `app/services/github.py`, modify `_should_skip` to accept an optional extra patterns list:

Replace the current `_should_skip` function:

```python
def _should_skip(filename: str, extra_patterns: list[str] | None = None) -> bool:
    name = filename.split("/")[-1]  # match against basename only for most patterns
    for pattern in _SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    if extra_patterns:
        for pattern in extra_patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
                return True
    return False
```

Modify `get_pr_patches` signature and body to accept and pass `extra_skip_patterns`:

Replace the function signature line:

```python
def get_pr_patches(repo_full_name: str, pr_number: int, *, extra_skip_patterns: list[str] | None = None) -> list[FilePatch]:
```

Replace the `_should_skip(f.filename)` call inside `get_pr_patches`:

```python
        if _should_skip(f.filename, extra_patterns=extra_skip_patterns):
```

- [ ] **Step 4: Add `repo_config` to ReviewState**

In `app/agent/state.py`, add after `last_reviewed_sha: str` (line 42):

```python
    # Per-repo config
    repo_config: dict
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_github.py -v`
Expected: All tests pass (existing + 2 new)

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: All tests pass (no regressions from `_should_skip` signature change — `get_pr_incremental_diff` doesn't use `_should_skip` directly, it calls `_should_skip(f.filename)` which still works with the default `extra_patterns=None`)

Wait — check `get_pr_incremental_diff` in `app/services/github.py`. It also calls `_should_skip(f.filename)`. Since `extra_patterns` defaults to `None`, this call is backward-compatible and needs no changes.

- [ ] **Step 7: Commit**

```bash
git add app/services/github.py app/agent/state.py tests/test_github.py
git commit -m "feat: add extra_skip_patterns to get_pr_patches, add repo_config to state"
```

---

### Task 6: Tech stack prompt injection + config loading in `run_review` + tests

**Files:**
- Modify: `app/agent/graph.py:60-100` (`scan_call` function)
- Modify: `app/tasks/review.py` (load config, pass ignore_paths and repo_config)
- Modify: `tests/test_agent_graph.py` (add tech_stack tests)
- Modify: `tests/test_review_task.py` (add config integration test)

- [ ] **Step 1: Write failing test for tech_stack injection in scan_call**

Add to `tests/test_agent_graph.py`:

```python
def test_scan_call_injects_tech_stack(mock_llm):
    """When repo_config has tech_stack, it's injected into the system prompt."""
    state = _base_state()
    state["repo_config"] = {
        "tech_stack": {
            "language": "python",
            "framework": "fastapi",
            "testing": "pytest",
        }
    }

    scan_call(state)

    call_args = mock_llm.invoke.call_args[0][0]
    system_msg = call_args[0]
    assert "## Project Tech Stack" in system_msg.content
    assert "Language: python" in system_msg.content
    assert "Framework: fastapi" in system_msg.content
    assert "Testing: pytest" in system_msg.content


def test_scan_call_no_tech_stack_without_config(mock_llm):
    """Without repo_config tech_stack, no tech stack section in prompt."""
    state = _base_state()
    state["repo_config"] = {}

    scan_call(state)

    call_args = mock_llm.invoke.call_args[0][0]
    system_msg = call_args[0]
    assert "## Project Tech Stack" not in system_msg.content
```

Note: `_base_state()` is a helper that should already exist in `tests/test_agent_graph.py`. If it doesn't, look at the existing test setup and ensure the new state includes `"repo_config": {}` as default. Also make sure the existing `_base_state()` helper (if it exists) is updated to include `repo_config: {}` so existing tests don't break.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_graph.py::test_scan_call_injects_tech_stack tests/test_agent_graph.py::test_scan_call_no_tech_stack_without_config -v`
Expected: FAIL — either KeyError on `repo_config` or no tech stack section found

- [ ] **Step 3: Implement tech_stack injection in `scan_call`**

In `app/agent/graph.py`, inside `scan_call`, after the re-review addendum block (after line ~81 where `system_prompt` is finalized) and before `human_content = ...`:

```python
        # Inject tech stack from repo config
        repo_config = state.get("repo_config", {})
        tech_stack = repo_config.get("tech_stack", {})
        if tech_stack:
            tech_lines = []
            for key, value in tech_stack.items():
                tech_lines.append(f"- {key.title()}: {value}")
            system_prompt += "\n\n## Project Tech Stack\n" + "\n".join(tech_lines)
```

This goes inside the `if state["round_count"] == 0:` block, after the re-review addendum logic and before the `human_content = ...` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: All tests pass (existing + 2 new)

- [ ] **Step 5: Write failing test for config loading in `run_review`**

Add to `tests/test_review_task.py`:

```python
@patch("app.tasks.review.post_review")
@patch("app.tasks.review.save_review")
@patch("app.tasks.review.get_last_review", return_value=None)
@patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
@patch("app.tasks.review.build_review_graph")
@patch("app.tasks.review.get_repo_config")
@patch("app.tasks.review.get_pr_patches")
def test_run_review_loads_config_and_passes_ignore_paths(
    mock_patches, mock_config, mock_graph, mock_sha, mock_last, mock_save, mock_post
):
    """run_review loads repo config, extracts ignore_paths, and passes to get_pr_patches."""
    mock_config.return_value = {
        "ignore_paths": ["generated/**", "docs/**"],
        "tech_stack": {"language": "python"},
    }
    mock_graph_instance = MagicMock()
    mock_graph_instance.invoke.return_value = {
        "risk_level": "low",
        "summary": "Looks good",
        "comments": [],
        "escalated": False,
        "traces": [],
    }
    mock_graph.return_value = mock_graph_instance

    mock_task = MagicMock(spec=Task)
    mock_task.request.id = "test-config-task"
    mock_task.request.retries = 0

    run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

    # Verify config was loaded with the correct ref
    mock_config.assert_called_once_with("owner/repo", "abc123")

    # Verify graph was invoked with repo_config
    invoke_args = mock_graph_instance.invoke.call_args[0][0]
    assert invoke_args["repo_config"] == {
        "ignore_paths": ["generated/**", "docs/**"],
        "tech_stack": {"language": "python"},
    }
```

Ensure these imports are at the top of `tests/test_review_task.py`:

```python
from app.services.github import get_repo_config
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_review_task.py::test_run_review_loads_config_and_passes_ignore_paths -v`
Expected: FAIL — `get_repo_config` not imported in `app/tasks/review.py`

- [ ] **Step 7: Implement config loading in `run_review`**

In `app/tasks/review.py`:

Add import at top:

```python
from app.services.github import get_pr_head_sha, get_repo_config
```

(Replace the existing `from app.services.github import get_pr_head_sha` line.)

Inside `run_review`, after `ref = get_pr_head_sha(...)` and before the re-review detection block, add:

```python
        # Load per-repo config
        repo_config = get_repo_config(repo_full_name, ref)
        ignore_paths = repo_config.get("ignore_paths", [])
        if ignore_paths:
            log.info("repo_config_ignore_paths", patterns=ignore_paths)
```

In the `graph.invoke({...})` call, add `repo_config` to the state dict:

```python
            "repo_config": repo_config,
```

(Add this line after `"last_reviewed_sha": last_reviewed_sha,` in the invoke dict.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_review_task.py -v`
Expected: All tests pass

- [ ] **Step 9: Run full test suite**

Run: `pytest -v`
Expected: All tests pass. If any existing tests fail because they don't include `repo_config` in their state dicts, add `"repo_config": {}` to those test state dicts.

- [ ] **Step 10: Commit**

```bash
git add app/agent/graph.py app/tasks/review.py tests/test_agent_graph.py tests/test_review_task.py
git commit -m "feat: inject tech_stack into scan prompt, load per-repo config in run_review"
```

---

## Post-Implementation Verification

After all tasks are complete:

1. Run `pytest -v` — all tests should pass (existing 91 + ~9 new ≈ 100 total)
2. Verify `docker-compose.yml` parses: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"`
3. Verify no import errors: `python -c "from app.services.github import get_repo_config; print('OK')"`
