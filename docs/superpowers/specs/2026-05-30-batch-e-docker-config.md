# Batch E — Docker Compose + Per-Repo Config

## Goal

Add production-ready Docker Compose deployment (5 services) and per-repo `.ai-review/config.yml` support for customizing review behavior (ignore paths, tech stack context).

---

## 1. Docker Compose

### Dockerfile

Multi-stage build:
- **Builder stage:** `python:3.12-slim`, install dependencies from `requirements.txt`
- **Runtime stage:** `python:3.12-slim`, copy installed packages + app code, expose port 8000
- Default CMD: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Same image used for `review-agent` (default CMD) and `celery-worker` (override CMD).

### docker-compose.yml

5 services:

| Service | Image | Port | Depends On |
|---|---|---|---|
| `review-agent` | Build from `.` | 8000:8000 | postgres, redis |
| `celery-worker` | Build from `.` | — | postgres, redis |
| `ai-gateway` | `ai-api-gateway:latest` | 8080:8080 | — |
| `postgres` | `pgvector/pgvector:pg16` | 5432:5432 | — |
| `redis` | `redis:7-alpine` | 6379:6379 | — |

Volumes:
- `pg_data:/var/lib/postgresql/data`
- `redis_data:/data`

Health checks:
- postgres: `pg_isready -U postgres`
- redis: `redis-cli ping`
- review-agent and celery-worker: `depends_on` with `condition: service_healthy`

Environment variables loaded from `.env` file.

### .env.example

Updated with all V2 variables:

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

---

## 2. Per-Repo Config

### Config File

Repositories can place a `.ai-review/config.yml` file to customize review behavior:

```yaml
ignore_paths:
  - "generated/**"
  - "docs/**"
  - "*.pb.go"

tech_stack:
  language: python
  framework: fastapi
  testing: pytest
```

All fields are optional. Missing file or invalid YAML results in empty config (no error).

### Loading

New function in `app/services/github.py`:

```python
def get_repo_config(repo: str, ref: str) -> dict:
```

- Fetches `.ai-review/config.yml` via GitHub Contents API
- Parses with `yaml.safe_load`
- Returns parsed dict, or `{}` on missing file / parse error

### State Change

Add to `ReviewState`:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `repo_config` | `dict` | `{}` | Parsed `.ai-review/config.yml` contents |

### Ignore Paths Integration

In `run_review`, before invoking the graph:

1. Load config via `get_repo_config`
2. Extract `ignore_paths` from config (list of glob patterns)
3. Pass to `get_pr_patches` as extra skip patterns

Modify `get_pr_patches` to accept an optional `extra_skip_patterns: list[str]` parameter. These are merged with the existing `_SKIP_PATTERNS` when filtering files.

### Tech Stack Prompt Injection

In `scan_call`, when `repo_config` has a `tech_stack` key, append a short section to the system prompt:

```
## Project Tech Stack
- Language: {language}
- Framework: {framework}
- Testing: {testing}
```

This gives the agent context about the project's technology without requiring it to discover this via tool calls.

---

## 3. File Structure

| File | Change |
|---|---|
| `Dockerfile` | **NEW** — multi-stage Python build |
| `docker-compose.yml` | **NEW** — 5 services with health checks |
| `.env.example` | **MODIFY** — update with all V2 env vars |
| `app/services/github.py` | **MODIFY** — add `get_repo_config`, modify `get_pr_patches` for extra skip patterns |
| `app/agent/state.py` | **MODIFY** — add `repo_config: dict` |
| `app/agent/graph.py` | **MODIFY** — inject tech_stack into scan prompt |
| `app/tasks/review.py` | **MODIFY** — load config, pass ignore_paths, pass repo_config to graph |
| `tests/test_github_config.py` | **NEW** — tests for `get_repo_config` |
| `tests/test_github.py` | **MODIFY** — tests for `get_pr_patches` with extra skip patterns |
| `tests/test_agent_graph.py` | **MODIFY** — tests for tech_stack prompt injection |

---

## 4. Testing Strategy

- `get_repo_config`: returns parsed dict on success, `{}` on missing file, `{}` on invalid YAML
- `get_pr_patches` with `extra_skip_patterns`: correctly filters files matching extra patterns
- `scan_call` with `repo_config` containing `tech_stack`: injects tech stack section into prompt
- `scan_call` without `repo_config`: no tech stack section (no regression)
- Docker: manual validation with `docker compose up` (not unit tested)

---

## 5. Scope Exclusions

- AI gateway scenario configuration (routes.yml) — separate concern, managed by the gateway itself
- `.ai-review/rules/` — already handled by existing `read_repo_rules` tool
- `.ai-review/known-issues/` — deferred to future batch
- Per-repo config hot-reload — config is fetched fresh on each review invocation
