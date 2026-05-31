# GitHub App Auth + Check Run Migration

**Date:** 2026-05-31
**Scope:** Auth migration, Check Run integration, security bypass, feedback collection

## Background

The agent currently uses a GitHub Personal Access Token (PAT) for all API calls.
Reviews are posted as `pr.create_review(event="COMMENT")` — advisory only, cannot block merge.
`scan_secrets` is one of 16 LLM tools — the LLM decides whether to call it and how to interpret results.

## Goals

1. Migrate authentication from PAT to GitHub App (JWT + installation token)
2. Use Check Run API to provide merge-blocking review status
3. Pull `scan_secrets` out of the LLM tool loop as an independent security bypass
4. Collect developer feedback (👍/👎 reactions) on bot comments

## Non-Goals

- `finish_review` output validation (Pydantic schema, line/file verification)
- pgvector-based similar finding suppression
- Coverage tracking (reviewed vs changed files)
- Prompt injection structural defenses

---

## Design

### 1. Dual-Mode Authentication

**App mode** (production):
- Config: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_APP_INSTALLATION_ID`
- JWT signed with RS256 via `PyJWT` + `cryptography`, 10-minute expiry
- JWT exchanged for installation token via `POST /app/installations/{id}/access_tokens`
- Installation token cached in-memory, refreshed 5 minutes before expiry (tokens last 1 hour)
- `Github(login_or_token=installation_token)` passed to PyGithub

**PAT mode** (fallback for local dev):
- Activates when `GITHUB_APP_ID` is not set but `GITHUB_APP_TOKEN` is
- Existing behavior unchanged
- Check Run calls are skipped (PAT cannot create check runs for an App)
- Log warning at startup: "Running in PAT mode — Check Runs disabled"

**Neither set** → raise on startup.

**Public API from `github.py`:**
- `get_github_client() -> Github` — returns authenticated client (replaces `_github_client()`)
- `is_app_mode() -> bool` — other modules use this to decide whether to call Check Run APIs
- `get_app_headers() -> dict` — returns `Authorization: Bearer <installation_token>` for raw REST calls (Check Run API not fully covered by PyGithub)

### 2. Check Run Lifecycle

**Timing:**
```
webhook received (PR opened / synchronize / ready_for_review)
  → create_check_run(name="CodeLens Review", status="in_progress")
  → run scan_secrets (independent bypass)
  → graph.invoke() (agent review)
  → compute conclusion
  → update_check_run(conclusion, output)
  → post_review() (inline comments, unchanged)
```

**Check Run output structure:**
```python
{
    "name": "CodeLens Review",
    "head_sha": "<pr_head_sha>",
    "status": "completed",
    "conclusion": "success" | "neutral" | "failure",
    "output": {
        "title": "AI Review — risk: {risk_level}",
        "summary": "{review_summary}",
        "annotations": [
            {
                "path": "src/auth.py",
                "start_line": 42,
                "end_line": 42,
                "annotation_level": "warning" | "failure" | "notice",
                "message": "finding text"
            }
        ]
    }
}
```

**Severity → annotation_level mapping:**
- `error` → `failure`
- `warning` → `warning`
- `suggestion` → `notice`

**Conclusion logic:**
```python
def compute_conclusion(secret_failed: bool, risk_level: str, check_policy: str) -> str:
    if secret_failed:
        return "failure"  # unconditional veto
    if check_policy == "enforced":
        return {"high": "failure", "medium": "neutral", "low": "success"}[risk_level]
    return "neutral"  # advisory — never blocks
```

**PAT mode:** All Check Run calls are skipped. Only `post_review()` runs.

**API calls:** Use `requests` or `httpx` with `get_app_headers()` directly against
`POST /repos/{owner}/{repo}/check-runs` and `PATCH /repos/{owner}/{repo}/check-runs/{id}`,
since PyGithub's Check Run support is limited.

### 3. Security Bypass — `scan_secrets` Outside Graph

**Current:** `scan_secrets` is a LangChain `@tool`. LLM chooses when to call it.

**New:** `scan_secrets` logic extracted into a standalone function `run_secret_scan(repo, pr_number) -> list[dict]`.
- Called in `tasks/review.py` **before** `graph.invoke()`
- Returns list of findings (filename, line, description)
- If findings non-empty → `secret_failed = True` → Check Run conclusion forced to `failure`
- Findings also injected into graph initial state as `secret_findings: list[dict]` so LLM can reference them in its summary (but cannot override the conclusion)
- The `@tool scan_secrets` wrapper remains available to the LLM for ad-hoc use, but the authoritative scan is the pre-graph one

**Pre-graph flow in `run_review()`:**
```python
check_run_id = create_check_run(...)  # or None in PAT mode
secret_findings = run_secret_scan(repo, pr_number)
secret_failed = len(secret_findings) > 0

result = graph.invoke({
    ...existing state...,
    "secret_findings": secret_findings,
})

conclusion = compute_conclusion(secret_failed, result["risk_level"], repo_config.get("check_policy", "advisory"))
if check_run_id:
    update_check_run(check_run_id, conclusion, result)
post_review(repo, pr_number, result)
```

### 4. Feedback Collection

**Mechanism:** At the start of each review, before running the graph, collect 👍/👎 reactions on the bot's previous review comments.

**`collect_feedback(repo, pr_number)` flow:**
1. Query `review_comments` table for the most recent review's comments on this PR
2. For each comment, fetch its GitHub comment reactions via REST API
3. If 👎 → set `feedback = "false_positive"`
4. If 👍 → set `feedback = "helpful"`
5. If both → 👎 wins (conservative)
6. Update `review_comments.feedback` in DB

**DB change:** `review_comments` table — add nullable column `feedback VARCHAR(20)`.

**Matching bot comments to DB records:** Bot comments contain the filename and line in their body text. Match by `review_id` (most recent review for this PR) — each ReviewComment maps 1:1 to a posted inline comment.

**Usage:** `query_review_history` tool already queries `review_comments`. Adding `feedback` to its output lets the LLM see "this type of finding was previously marked false-positive by this team." Automatic suppression (pgvector) is out of scope.

---

## Config Changes

### Environment (`.env`)
```
# App mode (production)
GITHUB_APP_ID=3922011
GITHUB_APP_PRIVATE_KEY_PATH=./private-key.pem
GITHUB_APP_INSTALLATION_ID=136973092

# PAT mode (fallback, local dev)
GITHUB_APP_TOKEN=ghp_xxx  # only used if APP_ID not set
```

### Per-Repo (`.ai-review/config.yml`)
```yaml
check_policy: advisory   # advisory (default) | enforced
```

### Settings class additions
```python
github_app_id: str | None = None
github_app_private_key_path: str | None = None
github_app_installation_id: str | None = None
```

## New Dependencies

- `PyJWT>=2.8` — JWT signing
- `cryptography>=42.0` — RS256 private key loading

## DB Migration

```sql
ALTER TABLE review_comments ADD COLUMN feedback VARCHAR(20);
```

## Files Changed

| File | Change |
|------|--------|
| `app/core/config.py` | Add App auth settings |
| `app/services/github.py` | Dual-mode auth, `get_github_client()`, `is_app_mode()`, `get_app_headers()` |
| `app/services/reviewer.py` | Add `create_check_run()`, `update_check_run()`, annotation mapping |
| `app/services/tools/quality.py` | Extract `run_secret_scan()` standalone function, keep `@tool` wrapper |
| `app/services/persistence.py` | `collect_feedback()`, update `query_review_history` to include feedback |
| `app/tasks/review.py` | Orchestrate: check_run → secret_scan → graph → conclusion → update |
| `app/agent/state.py` | Add `secret_findings` field |
| `app/api/webhook.py` | No change (webhook secret verification unchanged) |
| `requirements.txt` | Add PyJWT, cryptography |
| `alembic/versions/` | New migration for feedback column |
