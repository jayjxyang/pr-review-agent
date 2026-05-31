# Batch D1 — P1 Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 P1-priority tools (git_log, git_blame, find_definition, scan_secrets, check_test_coverage, get_ci_status, get_ci_logs, query_review_history) to the review agent.

**Architecture:** Each tool is a standalone `@tool`-decorated function following the existing pattern in `app/services/tools/`. Tools use `_github_client()` from `app/services/github` for GitHub API access. `git_blame` requires a new `graphql_query` helper. `query_review_history` queries PostgreSQL via SQLAlchemy. All tools return formatted strings and are registered in `ALL_TOOLS`.

**Tech Stack:** LangChain `@tool`, PyGithub, SQLAlchemy, GitHub REST API + GraphQL API

---

### Task 1: Add GraphQL helper to github.py

**Files:**
- Modify: `app/services/github.py`
- Test: `tests/test_github_graphql.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_graphql.py`:

```python
"""Tests for GitHub GraphQL helper."""

from unittest.mock import patch, MagicMock

from app.services.github import graphql_query


class TestGraphqlQuery:
    @patch("app.services.github.requests.post")
    def test_returns_data_on_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"repository": {"name": "test"}}}
        mock_post.return_value = mock_response

        result = graphql_query("query { repository { name } }", {})
        assert result == {"repository": {"name": "test"}}

    @patch("app.services.github.requests.post")
    def test_raises_on_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
        mock_post.return_value = mock_response

        try:
            graphql_query("query { }", {})
            assert False, "Should have raised"
        except Exception as e:
            assert "401" in str(e)

    @patch("app.services.github.requests.post")
    def test_raises_on_graphql_errors(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": [{"message": "Not found"}]}
        mock_post.return_value = mock_response

        try:
            graphql_query("query { }", {})
            assert False, "Should have raised"
        except Exception as e:
            assert "Not found" in str(e)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_github_graphql.py -v`
Expected: FAIL with `ImportError: cannot import name 'graphql_query'`

- [ ] **Step 3: Implement graphql_query**

Add to `app/services/github.py` (add `import requests` at the top):

```python
import requests


def graphql_query(query: str, variables: dict) -> dict:
    """Execute a GitHub GraphQL query. Returns the 'data' portion of the response."""
    settings = get_settings()
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {settings.github_app_token}",
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_github_graphql.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/github.py tests/test_github_graphql.py
git commit -m "feat: add graphql_query helper for GitHub GraphQL API"
```

---

### Task 2: Implement git_log and git_blame

**Files:**
- Create: `app/services/tools/git_history.py`
- Test: `tests/test_tools_git_history.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_git_history.py`:

```python
"""Tests for git history tools — git_log and git_blame."""

import json
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.services.tools.git_history import git_log, git_blame


class TestGitLog:
    @patch("app.services.tools.git_history._github_client")
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

    @patch("app.services.tools.git_history._github_client")
    def test_with_path_filter(self, mock_client):
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = []
        mock_client.return_value.get_repo.return_value = mock_repo

        result = git_log.invoke({"repo": "org/repo", "ref": "main", "path": "src/auth.py"})
        mock_repo.get_commits.assert_called_once()
        call_kwargs = mock_repo.get_commits.call_args
        assert call_kwargs[1].get("path") == "src/auth.py" or call_kwargs.kwargs.get("path") == "src/auth.py"

    @patch("app.services.tools.git_history._github_client")
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
        # Should only show 5 commits
        assert result.count("sha") == 5

    @patch("app.services.tools.git_history._github_client")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_git_history.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement git_log and git_blame**

Create `app/services/tools/git_history.py`:

```python
"""Git history tools — git_log and git_blame via GitHub API."""

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import _github_client, graphql_query

logger = get_logger(__name__)

_MAX_COMMITS = 10

_BLAME_QUERY = """
query($owner: String!, $name: String!, $ref: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $ref) {
      ... on Commit {
        blame(path: $path) {
          ranges {
            startingLine
            endingLine
            commit {
              oid
              message
              author {
                name
                date
              }
            }
          }
        }
      }
    }
  }
}
"""


@tool
def git_log(repo: str, ref: str = "HEAD", path: str = None, limit: int = 10) -> str:
    """Get recent commit history. Optionally filter by file path.

    Args:
        repo: Repository full name (owner/repo).
        ref: Git ref (branch or SHA). Defaults to HEAD.
        path: Optional file path to filter commits.
        limit: Max number of commits to return (default 10, max 10).
    """
    limit = min(limit, _MAX_COMMITS)
    try:
        repo_obj = _github_client().get_repo(repo)
        kwargs = {"sha": ref}
        if path:
            kwargs["path"] = path
        commits = repo_obj.get_commits(**kwargs)
    except Exception as e:
        return f"Error fetching git log: {e}"

    output = []
    for i, commit in enumerate(commits):
        if i >= limit:
            break
        sha = commit.sha[:7]
        msg = commit.commit.message.split("\n")[0]  # first line only
        author = commit.commit.author.name
        date = commit.commit.author.date.strftime("%Y-%m-%d")
        files = [f.filename for f in (commit.files or [])]
        files_str = ", ".join(files[:5])
        if len(files) > 5:
            files_str += f" (+{len(files) - 5} more)"
        output.append(f"{sha} {date} [{author}] {msg}\n  files: {files_str}")

    if not output:
        return "No commits found."
    return "\n".join(output)


@tool
def git_blame(repo: str, path: str, ref: str, start_line: int, end_line: int) -> str:
    """Get blame information for a line range in a file. Shows who last modified each line.

    Args:
        repo: Repository full name (owner/repo).
        path: File path in the repository.
        ref: Git ref (branch or SHA).
        start_line: Start line number (1-based).
        end_line: End line number (1-based, inclusive).
    """
    owner, name = repo.split("/", 1)
    try:
        data = graphql_query(_BLAME_QUERY, {
            "owner": owner,
            "name": name,
            "ref": ref,
            "path": path,
        })
    except Exception as e:
        return f"Error fetching blame: {e}"

    blame_obj = data.get("repository", {}).get("object", {})
    if not blame_obj or "blame" not in blame_obj:
        return f"Error: could not retrieve blame for {path} at {ref}"

    ranges = blame_obj["blame"]["ranges"]
    output = []
    for r in ranges:
        r_start = r["startingLine"]
        r_end = r["endingLine"]
        # Filter to requested line range
        if r_end < start_line or r_start > end_line:
            continue
        commit = r["commit"]
        sha = commit["oid"][:7]
        author = commit["author"]["name"]
        date = commit["author"]["date"][:10]
        msg = commit["message"].split("\n")[0]
        output.append(f"L{r_start}-{r_end}: {sha} [{author} {date}] {msg}")

    if not output:
        return f"No blame data for lines {start_line}-{end_line} in {path}"
    return "\n".join(output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_git_history.py -v`
Expected: All 6 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/tools/git_history.py tests/test_tools_git_history.py
git commit -m "feat: add git_log and git_blame tools"
```

---

### Task 3: Implement find_definition

**Files:**
- Modify: `app/services/tools/code_read.py`
- Test: `tests/test_tools_code_read.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_code_read.py`:

```python
"""Tests for find_definition tool."""

from unittest.mock import patch, MagicMock

from app.services.tools.code_read import find_definition


class TestFindDefinition:
    @patch("app.services.tools.code_read._github_client")
    def test_finds_python_def(self, mock_client):
        mock_item = MagicMock()
        mock_item.path = "src/auth.py"
        mock_item.html_url = "https://github.com/org/repo/blob/main/src/auth.py"
        # search_code returns paginated results
        mock_client.return_value.search_code.return_value = [mock_item]

        result = find_definition.invoke({"repo": "org/repo", "symbol": "login"})
        assert "auth.py" in result

    @patch("app.services.tools.code_read._github_client")
    def test_no_results(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        result = find_definition.invoke({"repo": "org/repo", "symbol": "nonexistent"})
        assert "No definition found" in result

    @patch("app.services.tools.code_read._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.search_code.side_effect = Exception("API error")
        result = find_definition.invoke({"repo": "org/repo", "symbol": "login"})
        assert "Error" in result

    @patch("app.services.tools.code_read._github_client")
    def test_with_path_filter(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        find_definition.invoke({"repo": "org/repo", "symbol": "login", "path_filter": "src/"})
        call_args = mock_client.return_value.search_code.call_args[0][0]
        assert "path:src/" in call_args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_code_read.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_definition'` or attribute error

- [ ] **Step 3: Implement find_definition**

Add to `app/services/tools/code_read.py` (replace the existing `find_references` wrapper-based `find_definition` — note: the existing code has `find_references`, not `find_definition`):

```python
_DEFINITION_PATTERNS = [
    "def {symbol}",
    "class {symbol}",
    "function {symbol}",
    "const {symbol}",
    "let {symbol}",
    "var {symbol}",
]

_MAX_DEFINITION_RESULTS = 5


@tool
def find_definition(repo: str, symbol: str, path_filter: str = None) -> str:
    """Find where a symbol (function, class, variable) is defined in the repository.

    Args:
        repo: Repository full name (owner/repo).
        symbol: The symbol name to find the definition of.
        path_filter: Optional path prefix to narrow the search.
    """
    # Build query with definition patterns
    pattern_query = " OR ".join(f'"{p.format(symbol=symbol)}"' for p in _DEFINITION_PATTERNS)
    q = f"{pattern_query} repo:{repo}"
    if path_filter:
        q += f" path:{path_filter}"

    try:
        results = _github_client().search_code(q)
    except Exception as e:
        return f"Error searching for definition: {e}"

    output = []
    for i, item in enumerate(results):
        if i >= _MAX_DEFINITION_RESULTS:
            break
        output.append(f"- {item.path}")

    if not output:
        return f"No definition found for '{symbol}'."
    return f"Possible definitions of '{symbol}':\n" + "\n".join(output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_code_read.py -v`
Expected: All 4 PASS

- [ ] **Step 5: Run all existing tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/tools/code_read.py tests/test_tools_code_read.py
git commit -m "feat: add find_definition tool"
```

---

### Task 4: Implement scan_secrets

**Files:**
- Create: `app/services/tools/quality.py`
- Test: `tests/test_tools_quality.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_quality.py`:

```python
"""Tests for quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re
from unittest.mock import patch, MagicMock

from app.services.tools.quality import scan_secrets


class TestScanSecrets:
    @patch("app.services.tools.quality._github_client")
    def test_detects_api_key(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        mock_file.patch = '+API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "config.py" in result
        assert "secret" in result.lower() or "key" in result.lower()

    @patch("app.services.tools.quality._github_client")
    def test_detects_github_token(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "deploy.sh"
        mock_file.patch = '+GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "deploy.sh" in result
        assert "GitHub" in result or "ghp_" in result.lower() or "token" in result.lower()

    @patch("app.services.tools.quality._github_client")
    def test_detects_private_key(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "certs/key.pem"
        mock_file.patch = '+-----BEGIN RSA PRIVATE KEY-----'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "key.pem" in result

    @patch("app.services.tools.quality._github_client")
    def test_clean_diff(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "app.py"
        mock_file.patch = '+def hello():\n+    return "world"'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality._github_client")
    def test_only_scans_added_lines(self, mock_client):
        mock_file = MagicMock()
        mock_file.filename = "config.py"
        # Removed line should NOT trigger, only added lines
        mock_file.patch = '-OLD_KEY = "sk-removed123456789012345678901234"\n+# key removed'
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file]
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No secrets detected" in result

    @patch("app.services.tools.quality._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("Not found")
        result = scan_secrets.invoke({"repo": "org/repo", "pr_number": 1})
        assert "Error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_quality.py::TestScanSecrets -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement scan_secrets**

Create `app/services/tools/quality.py`:

```python
"""Quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import _github_client

logger = get_logger(__name__)

_SECRET_PATTERNS = [
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[\w!@#$%^&*]{8,}', "password assignment"),
    (r'(?:secret|api_key|apikey|token)\s*[=:]\s*["\']?[\w\-]{16,}', "secret/key assignment"),
    (r'(?:sk-|sk_live_|sk_test_)[a-zA-Z0-9]{20,}', "OpenAI/Stripe key"),
    (r'ghp_[a-zA-Z0-9]{36,}', "GitHub personal access token"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', "private key"),
    (r'(?:bearer|authorization)\s*[=:]\s*["\']?[a-zA-Z0-9\-_.]{20,}', "bearer token"),
]


@tool
def scan_secrets(repo: str, pr_number: int) -> str:
    """Scan the PR diff for potential hardcoded secrets, API keys, tokens, or passwords.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number to scan.
    """
    try:
        pr = _github_client().get_repo(repo).get_pull(pr_number)
    except Exception as e:
        return f"Error fetching PR: {e}"

    findings = []
    for f in pr.get_files():
        patch = f.patch or ""
        for line_num, line in enumerate(patch.splitlines(), 1):
            # Only scan added lines (start with +, not +++ header)
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, description in _SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(f"- {f.filename}:L{line_num}: {description}")
                    break  # one finding per line

    if not findings:
        return "No secrets detected in the PR diff."
    return f"Potential secrets found ({len(findings)}):\n" + "\n".join(findings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_quality.py::TestScanSecrets -v`
Expected: All 6 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/tools/quality.py tests/test_tools_quality.py
git commit -m "feat: add scan_secrets tool"
```

---

### Task 5: Implement check_test_coverage

**Files:**
- Modify: `app/services/tools/quality.py`
- Test: `tests/test_tools_quality.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_tools_quality.py`:

```python
from app.services.tools.quality import check_test_coverage


class TestCheckTestCoverage:
    @patch("app.services.tools.quality._github_client")
    def test_finds_test_references(self, mock_client):
        mock_item = MagicMock()
        mock_item.path = "tests/test_auth.py"
        mock_client.return_value.search_code.return_value = [mock_item]

        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "test_auth.py" in result

    @patch("app.services.tools.quality._github_client")
    def test_no_test_references(self, mock_client):
        mock_client.return_value.search_code.return_value = []
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/utils.py", "ref": "main",
        })
        assert "No test references found" in result

    @patch("app.services.tools.quality._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.search_code.side_effect = Exception("API error")
        result = check_test_coverage.invoke({
            "repo": "org/repo", "source_path": "src/auth.py", "ref": "main",
        })
        assert "Error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_quality.py::TestCheckTestCoverage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement check_test_coverage**

Add to `app/services/tools/quality.py`:

```python
@tool
def check_test_coverage(repo: str, source_path: str, ref: str) -> str:
    """Check if a source file has test references. Searches for imports/usages in test files.

    Args:
        repo: Repository full name (owner/repo).
        source_path: Path to the source file to check.
        ref: Git ref (branch or SHA).
    """
    # Extract module name from path (e.g., "src/auth.py" → "auth")
    module_name = source_path.split("/")[-1].replace(".py", "").replace(".ts", "").replace(".js", "")

    q = f"{module_name} repo:{repo} path:test"
    try:
        results = _github_client().search_code(q)
    except Exception as e:
        return f"Error searching for test references: {e}"

    output = []
    for i, item in enumerate(results):
        if i >= 10:
            break
        output.append(f"- {item.path}")

    if not output:
        return f"No test references found for '{source_path}'."
    return f"Test files referencing '{module_name}':\n" + "\n".join(output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_quality.py -v`
Expected: All 9 PASS (6 scan_secrets + 3 check_test_coverage)

- [ ] **Step 5: Commit**

```bash
git add app/services/tools/quality.py tests/test_tools_quality.py
git commit -m "feat: add check_test_coverage tool"
```

---

### Task 6: Implement get_ci_status and get_ci_logs

**Files:**
- Modify: `app/services/tools/quality.py`
- Test: `tests/test_tools_quality.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_tools_quality.py`:

```python
from app.services.tools.quality import get_ci_status, get_ci_logs


class TestGetCiStatus:
    @patch("app.services.tools.quality._github_client")
    def test_returns_check_statuses(self, mock_client):
        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.status = "completed"
        mock_check.conclusion = "success"
        mock_check.html_url = "https://github.com/org/repo/actions/runs/123"

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "CI / test" in result
        assert "success" in result

    @patch("app.services.tools.quality._github_client")
    def test_no_checks(self, mock_client):
        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = []
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "No CI checks found" in result

    @patch("app.services.tools.quality._github_client")
    def test_error_handling(self, mock_client):
        mock_client.return_value.get_repo.side_effect = Exception("API error")
        result = get_ci_status.invoke({"repo": "org/repo", "pr_number": 1})
        assert "Error" in result


class TestGetCiLogs:
    @patch("app.services.tools.quality._github_client")
    def test_returns_failure_annotations(self, mock_client):
        mock_annotation = MagicMock()
        mock_annotation.path = "src/auth.py"
        mock_annotation.start_line = 10
        mock_annotation.annotation_level = "failure"
        mock_annotation.message = "AssertionError: expected True"

        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.conclusion = "failure"
        mock_check.output.annotations_count = 1
        mock_check.get_annotations.return_value = [mock_annotation]

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "CI / test",
        })
        assert "AssertionError" in result

    @patch("app.services.tools.quality._github_client")
    def test_check_not_found(self, mock_client):
        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = []
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "nonexistent",
        })
        assert "not found" in result.lower()

    @patch("app.services.tools.quality._github_client")
    def test_check_passed(self, mock_client):
        mock_check = MagicMock()
        mock_check.name = "CI / test"
        mock_check.conclusion = "success"

        mock_pr = MagicMock()
        mock_pr.head.sha = "abc123"
        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_commit = MagicMock()
        mock_commit.get_check_runs.return_value = [mock_check]
        mock_repo.get_commit.return_value = mock_commit
        mock_client.return_value.get_repo.return_value = mock_repo

        result = get_ci_logs.invoke({
            "repo": "org/repo", "pr_number": 1, "check_name": "CI / test",
        })
        assert "passed" in result.lower() or "success" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_quality.py::TestGetCiStatus tests/test_tools_quality.py::TestGetCiLogs -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement get_ci_status and get_ci_logs**

Add to `app/services/tools/quality.py`:

```python
@tool
def get_ci_status(repo: str, pr_number: int) -> str:
    """Get CI check run statuses for the PR's HEAD commit.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number.
    """
    try:
        repo_obj = _github_client().get_repo(repo)
        pr = repo_obj.get_pull(pr_number)
        commit = repo_obj.get_commit(pr.head.sha)
        checks = commit.get_check_runs()
    except Exception as e:
        return f"Error fetching CI status: {e}"

    output = []
    for check in checks:
        status = check.status
        conclusion = check.conclusion or "pending"
        output.append(f"- {check.name}: {status}/{conclusion}")

    if not output:
        return "No CI checks found for this PR."
    return "\n".join(output)


_MAX_LOG_LINES = 100


@tool
def get_ci_logs(repo: str, pr_number: int, check_name: str) -> str:
    """Get failure details for a specific CI check. Use get_ci_status first to see check names.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number.
        check_name: Name of the CI check to get logs for.
    """
    try:
        repo_obj = _github_client().get_repo(repo)
        pr = repo_obj.get_pull(pr_number)
        commit = repo_obj.get_commit(pr.head.sha)
        checks = commit.get_check_runs()
    except Exception as e:
        return f"Error fetching CI logs: {e}"

    target_check = None
    for check in checks:
        if check.name == check_name:
            target_check = check
            break

    if not target_check:
        return f"Check '{check_name}' not found."

    if target_check.conclusion == "success":
        return f"Check '{check_name}' passed — no failure logs."

    # Get annotations (error details)
    try:
        annotations = target_check.get_annotations()
    except Exception:
        annotations = []

    output = [f"Check '{check_name}' — conclusion: {target_check.conclusion}"]
    for ann in annotations:
        if len(output) >= _MAX_LOG_LINES:
            output.append(f"\n[truncated — showing first {_MAX_LOG_LINES} entries]")
            break
        output.append(f"  {ann.path}:{ann.start_line} [{ann.annotation_level}] {ann.message}")

    if len(output) == 1:
        output.append("  No annotations available. Check the CI run URL for full logs.")

    return "\n".join(output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_quality.py -v`
Expected: All 15 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/tools/quality.py tests/test_tools_quality.py
git commit -m "feat: add get_ci_status and get_ci_logs tools"
```

---

### Task 7: Implement query_review_history

**Files:**
- Modify: `app/services/tools/knowledge.py`
- Test: `tests/test_tools_knowledge.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_knowledge.py`:

```python
"""Tests for query_review_history tool."""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.review import Review, ReviewComment


# Set up in-memory SQLite for testing
_test_engine = create_engine("sqlite:///:memory:")
_TestSession = sessionmaker(bind=_test_engine)


def _setup_test_db():
    """Create tables and seed test data."""
    # Swap JSONB to JSON for SQLite compatibility
    from app.models.review import AgentTrace
    from sqlalchemy import JSON
    AgentTrace.tool_params.type = JSON()

    Base.metadata.create_all(_test_engine)
    session = _TestSession()

    review = Review(
        repo="org/repo",
        pr_number=42,
        risk_level="medium",
        summary="Found issues",
        reviewed_sha="abc123",
    )
    session.add(review)
    session.flush()

    session.add(ReviewComment(
        review_id=review.id,
        filename="src/auth.py",
        line=10,
        severity="warning",
        comment="Missing null check on token",
    ))
    session.add(ReviewComment(
        review_id=review.id,
        filename="src/db.py",
        line=25,
        severity="error",
        comment="SQL injection risk in query builder",
    ))
    session.commit()
    return session


class TestQueryReviewHistory:
    def setup_method(self):
        Base.metadata.drop_all(_test_engine)
        self.session = _setup_test_db()

    def teardown_method(self):
        self.session.close()

    def test_query_by_repo(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo")
        assert "auth.py" in result
        assert "db.py" in result

    def test_query_by_file_path(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", file_path="auth")
        assert "auth.py" in result
        assert "db.py" not in result

    def test_query_by_keyword(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", keyword="SQL injection")
        assert "SQL injection" in result
        assert "null check" not in result

    def test_no_results(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "other/repo")
        assert "No review history found" in result

    def test_combined_filters(self):
        from app.services.tools.knowledge import _query_review_history_impl
        result = _query_review_history_impl(self.session, "org/repo", file_path="auth", keyword="token")
        assert "null check on token" in result
        assert "SQL injection" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools_knowledge.py -v`
Expected: FAIL with `ImportError: cannot import name '_query_review_history_impl'`

- [ ] **Step 3: Implement query_review_history**

Add to `app/services/tools/knowledge.py`:

```python
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.review import Review, ReviewComment

logger = get_logger(__name__)

_MAX_HISTORY_RESULTS = 5


def _query_review_history_impl(session: Session, repo: str, file_path: str = None, keyword: str = None) -> str:
    """Core implementation — accepts a session for testability."""
    query = (
        session.query(ReviewComment)
        .join(Review)
        .filter(Review.repo == repo)
    )
    if file_path:
        query = query.filter(ReviewComment.filename.like(f"%{file_path}%"))
    if keyword:
        query = query.filter(ReviewComment.comment.like(f"%{keyword}%"))

    query = query.order_by(ReviewComment.created_at.desc()).limit(_MAX_HISTORY_RESULTS)
    results = query.all()

    if not results:
        return f"No review history found for '{repo}'" + (f" matching filters" if file_path or keyword else "") + "."

    output = []
    for rc in results:
        output.append(
            f"- PR #{rc.review.pr_number} | {rc.filename}:L{rc.line} [{rc.severity}]\n"
            f"  {rc.comment}"
        )
    return "\n".join(output)


@tool
def query_review_history(repo: str, file_path: str = None, keyword: str = None) -> str:
    """Search past review comments for this repository. Useful for finding recurring issues.

    Args:
        repo: Repository full name (owner/repo).
        file_path: Optional file path substring to filter by.
        keyword: Optional keyword to search in comment text.
    """
    try:
        with SessionLocal() as session:
            return _query_review_history_impl(session, repo, file_path, keyword)
    except Exception as e:
        logger.warning("query_review_history_failed", error=str(e))
        return f"Error querying review history: {e}"
```

Note: the existing imports at top of `knowledge.py` are `import base64` and `from langchain_core.tools import tool` and `from app.services.github import _github_client`. Add the new imports after the existing ones.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools_knowledge.py -v`
Expected: All 5 PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/tools/knowledge.py tests/test_tools_knowledge.py
git commit -m "feat: add query_review_history tool"
```

---

### Task 8: Register all new tools in ALL_TOOLS

**Files:**
- Modify: `app/services/tools/__init__.py`

- [ ] **Step 1: Update __init__.py**

Replace the contents of `app/services/tools/__init__.py`:

```python
"""Tool collection — imports all tool modules and exposes a flat list."""

from app.services.tools.code_read import read_file, search_code, find_references, find_definition
from app.services.tools.pr_context import get_pr_info, get_pr_changed_files, get_pr_diff
from app.services.tools.knowledge import read_repo_rules, query_review_history
from app.services.tools.control import finish_review, escalate
from app.services.tools.git_history import git_log, git_blame
from app.services.tools.quality import scan_secrets, check_test_coverage, get_ci_status, get_ci_logs

ALL_TOOLS = [
    # Code reading
    read_file,
    search_code,
    find_references,
    find_definition,
    # PR context
    get_pr_info,
    get_pr_changed_files,
    get_pr_diff,
    # Git history
    git_log,
    git_blame,
    # Knowledge
    read_repo_rules,
    query_review_history,
    # Quality
    scan_secrets,
    check_test_coverage,
    get_ci_status,
    get_ci_logs,
    # Control
    finish_review,
    escalate,
]
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add app/services/tools/__init__.py
git commit -m "feat: register all 8 P1 tools in ALL_TOOLS (9→17 total)"
```
