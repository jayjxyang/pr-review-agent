# Batch D1 — P1 Tools

## Goal

Add 8 P1-priority tools to the review agent, expanding its ability to inspect git history, find definitions, scan for secrets, check test coverage, query CI status, and search past review history.

---

## 1. Tool Specifications

### git_log

```python
@tool
def git_log(repo: str, path: str = None, ref: str = "HEAD", limit: int = 10) -> str:
```

- Returns recent commits (message + author + SHA + changed files list)
- Truncate to `limit` entries (max 10)
- GitHub API: `GET /repos/{owner}/{repo}/commits?path={path}&sha={ref}`

### git_blame

```python
@tool
def git_blame(repo: str, path: str, ref: str, start_line: int, end_line: int) -> str:
```

- Returns line-by-line blame (commit SHA, author, date, line content)
- Uses GitHub GraphQL API (`blame` field on `Repository.object`)
- Line range required (no full-file blame — too expensive)

### find_definition

```python
@tool
def find_definition(repo: str, symbol: str, path_filter: str = None) -> str:
```

- Finds where a symbol is defined (function, class, variable)
- Uses GitHub Code Search API with regex patterns: `def {symbol}`, `class {symbol}`, `function {symbol}`, `const {symbol}`, `let {symbol}`, `var {symbol}`
- Returns top 5 matches with file path, line number, and surrounding context
- Does NOT call the existing `search_code` tool — implements its own search with definition-specific patterns

### scan_secrets

```python
@tool
def scan_secrets(repo: str, pr_number: int) -> str:
```

- Scans PR diff for potential secrets (API keys, tokens, passwords)
- Pattern matching: high-entropy strings (base64/hex > 20 chars), known prefixes (`sk-`, `ghp_`, `AKIA`, `-----BEGIN`), assignment patterns (`password=`, `secret=`, `api_key=`, `token=`)
- Returns list of findings with file, line, pattern matched
- Returns "No secrets detected" if clean

### check_test_coverage

```python
@tool
def check_test_coverage(repo: str, source_path: str, ref: str) -> str:
```

- Checks if a source file has corresponding test references
- Strategy: search for imports/references to the source module in `test*` files
- Uses GitHub Code Search API with path filter `test`
- Returns test files found + which functions are referenced, or "No test references found"

### get_ci_status

```python
@tool
def get_ci_status(repo: str, pr_number: int) -> str:
```

- Returns CI check run statuses for the PR's HEAD commit
- GitHub API: `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`
- Returns: check name, status (queued/in_progress/completed), conclusion (success/failure/etc), URL

### get_ci_logs

```python
@tool
def get_ci_logs(repo: str, pr_number: int, check_name: str) -> str:
```

- Returns logs for a specific failed CI check
- GitHub API: Get check run → download logs (annotations or log text)
- Truncate to first 100 lines of failure output
- Returns "Check not found" or "Check passed — no failure logs" if not applicable

### query_review_history

```python
@tool
def query_review_history(repo: str, file_path: str = None, keyword: str = None) -> str:
```

- Queries PostgreSQL `reviews` + `review_comments` tables from Batch B
- Filters: `repo` (required), `file_path` (optional LIKE match), `keyword` (optional text search in comment)
- Returns top 5 most recent matching review comments with: PR number, file, line, severity, comment text, date
- Uses SQLAlchemy session from `app.core.database`

---

## 2. File Structure

| File | Change |
|---|---|
| `app/services/tools/git_history.py` | **NEW** — `git_log`, `git_blame` |
| `app/services/tools/code_read.py` | **MODIFY** — Add `find_definition` |
| `app/services/tools/quality.py` | **NEW** — `scan_secrets`, `check_test_coverage`, `get_ci_status`, `get_ci_logs` |
| `app/services/tools/knowledge.py` | **MODIFY** — Add `query_review_history` |
| `app/services/tools/__init__.py` | **MODIFY** — Register all 8 new tools in ALL_TOOLS |
| `tests/test_tools_git_history.py` | **NEW** — Tests for git_log, git_blame |
| `tests/test_tools_code_read.py` | **NEW** — Tests for find_definition |
| `tests/test_tools_quality.py` | **NEW** — Tests for scan_secrets, check_test_coverage, get_ci_status, get_ci_logs |
| `tests/test_tools_knowledge.py` | **NEW** — Tests for query_review_history |

---

## 3. Implementation Patterns

All tools follow the existing pattern established in P0:

- `@tool` decorator from `langchain_core.tools`
- Parameters as typed function arguments (repo, pr_number, ref, etc.)
- `_github_client()` from `app.services.github` for GitHub API access
- Return `str` — formatted text for content, JSON string for structured data
- Error handling: `try/except GithubException` → return `"Error: {description}"`
- Truncation with helpful messages when output exceeds limits

### GitHub Client Extensions

`git_blame` requires the GraphQL API. Add a helper to `app/services/github.py`:

```python
def graphql_query(query: str, variables: dict) -> dict:
    """Execute a GitHub GraphQL query."""
    # Uses requests + github_app_token against https://api.github.com/graphql
```

### Database Access for query_review_history

Uses `SessionLocal` from `app.core.database` (established in Batch B):

```python
from app.core.database import SessionLocal
from app.models.review import Review, ReviewComment

with SessionLocal() as session:
    query = session.query(ReviewComment).join(Review).filter(Review.repo == repo)
    # ... filters ...
```

---

## 4. Secret Scanning Patterns

```python
SECRET_PATTERNS = [
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[\w!@#$%^&*]{8,}', "password assignment"),
    (r'(?:secret|api_key|apikey|token)\s*[=:]\s*["\']?[\w\-]{16,}', "secret/key assignment"),
    (r'(?:sk-|sk_live_|sk_test_)[a-zA-Z0-9]{20,}', "Stripe/OpenAI key"),
    (r'ghp_[a-zA-Z0-9]{36,}', "GitHub personal access token"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', "private key"),
    (r'(?:bearer|authorization)\s*[=:]\s*["\']?[a-zA-Z0-9\-_.]{20,}', "bearer token"),
]
```

---

## 5. Testing Strategy

All tools are tested by mocking `_github_client()` (or `graphql_query` for git_blame). Tests verify:

- Correct GitHub API calls are made with right parameters
- Output formatting (line numbers, truncation)
- Error handling (API failures return error strings)
- Edge cases (empty results, missing data)
- `query_review_history`: uses SQLite in-memory DB (same pattern as Batch B persistence tests)
- `scan_secrets`: tests with known patterns embedded in mock diff data
