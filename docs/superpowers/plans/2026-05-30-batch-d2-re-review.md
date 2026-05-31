# Batch D2 — Re-review Incremental Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an author pushes new commits to a reviewed PR, perform incremental re-review: evaluate prior findings, mark resolved issues, flag new problems, avoid duplicate comments.

**Architecture:** `run_review` queries PostgreSQL for the last review of a PR. If found, it fetches the incremental diff and passes prior unresolved comments + last reviewed SHA into the graph state. `scan_call` injects a re-review addendum into the system prompt. After the agent finishes, `save_review` marks old comments as resolved. `post_review` only posts new comments and includes a resolution summary.

**Tech Stack:** LangGraph, SQLAlchemy, PyGithub, GitHub Compare API

---

### Task 1: Add `get_last_review` to persistence.py

**Files:**
- Modify: `app/services/persistence.py`
- Test: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_persistence.py`:

```python
from app.services.persistence import get_last_review


class TestGetLastReview:
    def test_returns_last_review_with_unresolved_comments(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        # Seed a review with 2 comments (1 resolved, 1 unresolved)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="medium",
            summary="Found issues", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        session.add(ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="Missing check", resolved=True,
        ))
        session.add(ReviewComment(
            review_id=review.id, filename="b.py", line=20,
            severity="error", comment="SQL injection risk", resolved=False,
        ))
        session.commit()
        session.close()

        result = get_last_review("org/repo", 42)
        assert result is not None
        assert result["reviewed_sha"] == "abc123"
        assert len(result["comments"]) == 1
        assert result["comments"][0]["filename"] == "b.py"
        assert result["comments"][0]["comment"] == "SQL injection risk"

    def test_returns_none_when_no_prior_review(self, monkeypatch):
        _setup_test_db(monkeypatch)
        result = get_last_review("org/repo", 999)
        assert result is None

    def test_returns_latest_review(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        old_review = Review(
            repo="org/repo", pr_number=42, risk_level="low",
            summary="First review", reviewed_sha="old111",
        )
        session.add(old_review)
        session.flush()
        new_review = Review(
            repo="org/repo", pr_number=42, risk_level="medium",
            summary="Second review", reviewed_sha="new222",
        )
        session.add(new_review)
        session.commit()
        session.close()

        result = get_last_review("org/repo", 42)
        assert result["reviewed_sha"] == "new222"

    def test_caps_unresolved_comments_at_20(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="high",
            summary="Many issues", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        for i in range(25):
            session.add(ReviewComment(
                review_id=review.id, filename=f"file{i}.py", line=i,
                severity="warning", comment=f"Issue {i}", resolved=False,
            ))
        session.commit()
        session.close()

        result = get_last_review("org/repo", 42)
        assert len(result["comments"]) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_persistence.py::TestGetLastReview -v`
Expected: FAIL with `ImportError: cannot import name 'get_last_review'`

- [ ] **Step 3: Implement get_last_review**

Add to `app/services/persistence.py` after the existing `save_review` function:

```python
_MAX_PRIOR_COMMENTS = 20


def get_last_review(repo: str, pr_number: int) -> dict | None:
    """Get the most recent review for a PR with its unresolved comments.

    Returns:
        Dict with 'reviewed_sha' and 'comments' (list of dicts), or None if no prior review.
    """
    try:
        session = SessionLocal()
        try:
            review = (
                session.query(Review)
                .filter(Review.repo == repo, Review.pr_number == pr_number)
                .order_by(Review.created_at.desc())
                .first()
            )
            if not review:
                return None

            unresolved = (
                session.query(ReviewComment)
                .filter(
                    ReviewComment.review_id == review.id,
                    ReviewComment.resolved == False,
                )
                .order_by(ReviewComment.created_at.asc())
                .limit(_MAX_PRIOR_COMMENTS)
                .all()
            )

            return {
                "reviewed_sha": review.reviewed_sha,
                "comments": [
                    {
                        "id": c.id,
                        "filename": c.filename,
                        "line": c.line,
                        "severity": c.severity,
                        "comment": c.comment,
                    }
                    for c in unresolved
                ],
            }
        finally:
            session.close()

    except Exception as exc:
        logger.warning("get_last_review_failed", error=str(exc), repo=repo, pr=pr_number)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_persistence.py -v`
Expected: All PASS (4 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add app/services/persistence.py tests/test_persistence.py
git commit -m "feat: add get_last_review for re-review detection"
```

---

### Task 2: Add `resolve_comments` to persistence.py

**Files:**
- Modify: `app/services/persistence.py`
- Test: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_persistence.py`:

```python
from app.services.persistence import resolve_comments


class TestResolveComments:
    def test_marks_comments_as_resolved(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        session = session_factory()
        review = Review(
            repo="org/repo", pr_number=42, risk_level="medium",
            summary="Issues", reviewed_sha="abc123",
        )
        session.add(review)
        session.flush()
        c1 = ReviewComment(
            review_id=review.id, filename="a.py", line=10,
            severity="warning", comment="Issue 1", resolved=False,
        )
        c2 = ReviewComment(
            review_id=review.id, filename="b.py", line=20,
            severity="error", comment="Issue 2", resolved=False,
        )
        session.add_all([c1, c2])
        session.commit()
        c1_id, c2_id = c1.id, c2.id
        session.close()

        resolve_comments([c1_id])

        session = session_factory()
        assert session.get(ReviewComment, c1_id).resolved is True
        assert session.get(ReviewComment, c2_id).resolved is False
        session.close()

    def test_empty_list_is_noop(self, monkeypatch):
        _setup_test_db(monkeypatch)
        resolve_comments([])  # Should not raise

    def test_ignores_invalid_ids(self, monkeypatch):
        _setup_test_db(monkeypatch)
        resolve_comments([9999])  # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_persistence.py::TestResolveComments -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_comments'`

- [ ] **Step 3: Implement resolve_comments**

Add to `app/services/persistence.py` after `get_last_review`:

```python
def resolve_comments(comment_ids: list[int]) -> None:
    """Mark review comments as resolved by their IDs."""
    if not comment_ids:
        return
    try:
        session = SessionLocal()
        try:
            session.query(ReviewComment).filter(
                ReviewComment.id.in_(comment_ids)
            ).update({"resolved": True}, synchronize_session="fetch")
            session.commit()
            logger.info("comments_resolved", count=len(comment_ids))
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:
        logger.warning("resolve_comments_failed", error=str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_persistence.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/persistence.py tests/test_persistence.py
git commit -m "feat: add resolve_comments for marking prior issues fixed"
```

---

### Task 3: Add `get_pr_incremental_diff` to github.py

**Files:**
- Modify: `app/services/github.py`
- Test: `tests/test_github_incremental.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_github_incremental.py`:

```python
"""Tests for get_pr_incremental_diff."""

from unittest.mock import patch, MagicMock

from app.services.github import get_pr_incremental_diff


class TestGetPrIncrementalDiff:
    @patch("app.services.github._github_client")
    def test_returns_file_patches(self, mock_client):
        mock_file1 = MagicMock()
        mock_file1.filename = "src/auth.py"
        mock_file1.patch = "@@ -1,3 +1,4 @@\n+new line"

        mock_file2 = MagicMock()
        mock_file2.filename = "src/db.py"
        mock_file2.patch = "@@ -5,3 +5,4 @@\n+another change"

        mock_comparison = MagicMock()
        mock_comparison.files = [mock_file1, mock_file2]
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert len(result) == 2
        assert result[0].filename == "src/auth.py"
        assert result[1].filename == "src/db.py"

    @patch("app.services.github._github_client")
    def test_skips_binary_and_filtered_files(self, mock_client):
        mock_code = MagicMock()
        mock_code.filename = "src/app.py"
        mock_code.patch = "+change"

        mock_binary = MagicMock()
        mock_binary.filename = "logo.png"
        mock_binary.patch = None

        mock_lock = MagicMock()
        mock_lock.filename = "package-lock.json"
        mock_lock.patch = "+lots of stuff"

        mock_comparison = MagicMock()
        mock_comparison.files = [mock_code, mock_binary, mock_lock]
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert len(result) == 1
        assert result[0].filename == "src/app.py"

    @patch("app.services.github._github_client")
    def test_empty_diff(self, mock_client):
        mock_comparison = MagicMock()
        mock_comparison.files = []
        mock_client.return_value.get_repo.return_value.compare.return_value = mock_comparison

        result = get_pr_incremental_diff("org/repo", "abc123", "def456")
        assert result == []

    @patch("app.services.github._github_client")
    def test_raises_on_api_error(self, mock_client):
        from github import GithubException
        mock_client.return_value.get_repo.return_value.compare.side_effect = GithubException(
            404, {"message": "Not Found"}, {}
        )

        try:
            get_pr_incremental_diff("org/repo", "abc123", "def456")
            assert False, "Should have raised"
        except GithubException:
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_github_incremental.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_pr_incremental_diff'`

- [ ] **Step 3: Implement get_pr_incremental_diff**

Add to `app/services/github.py` after the existing `get_pr_head_sha` function:

```python
def get_pr_incremental_diff(repo_full_name: str, base_sha: str, head_sha: str) -> list[FilePatch]:
    """Fetch the diff between two commits (base_sha..head_sha).

    Used for re-review: compares last-reviewed commit to current HEAD.
    Returns a list of FilePatch objects. Binary files and skip-pattern files excluded.

    Raises GithubException on API errors.
    """
    repo = _github_client().get_repo(repo_full_name)
    comparison = repo.compare(base_sha, head_sha)

    patches: list[FilePatch] = []
    for f in comparison.files:
        if _should_skip(f.filename):
            continue
        if not f.patch:
            continue
        patches.append(FilePatch(filename=f.filename, patch=f.patch))

    logger.info(
        "incremental_diff_fetched",
        repo=repo_full_name,
        base=base_sha[:7],
        head=head_sha[:7],
        files=len(patches),
    )
    return patches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_github_incremental.py -v`
Expected: All 4 PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/github.py tests/test_github_incremental.py
git commit -m "feat: add get_pr_incremental_diff for re-review"
```

---

### Task 4: Add re-review state fields and prompt

**Files:**
- Modify: `app/agent/state.py`
- Modify: `app/agent/prompts.py`

- [ ] **Step 1: Add state fields**

Add to `ReviewState` in `app/agent/state.py` after the `compressed: bool` field:

```python
    # Re-review context
    prior_comments: list[dict]
    last_reviewed_sha: str
```

- [ ] **Step 2: Add RE_REVIEW_ADDENDUM prompt**

Add to `app/agent/prompts.py` after the existing `COMPRESS_PROMPT`:

```python
RE_REVIEW_ADDENDUM = """\

## Re-review Context

This PR was previously reviewed at commit {last_reviewed_sha}. The author has pushed new commits.
Below you will find the incremental diff (changes since last review).

### Prior Unresolved Comments
{prior_comments}

For EACH prior comment listed above, evaluate whether the new changes address it:
- If FIXED: include in your finish_review comments as {{"filename": "...", "line": ..., "severity": "resolved", "comment": "Previously flagged issue has been addressed.", "prior_comment_id": <id>}}
- If NOT FIXED and still relevant: re-flag it with the original severity
- If the fix introduced a NEW problem: flag with a new comment explaining the regression

Also review the new changes for any NEW issues not covered by prior comments.
"""
```

- [ ] **Step 3: Run all tests to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add app/agent/state.py app/agent/prompts.py
git commit -m "feat: add re-review state fields and prompt addendum"
```

---

### Task 5: Modify `scan_call` to inject re-review context

**Files:**
- Modify: `app/agent/graph.py`
- Test: `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_graph.py`:

```python
class TestReReviewInjection:
    def test_scan_call_injects_re_review_addendum(self, monkeypatch):
        """When prior_comments is non-empty, system prompt includes re-review addendum."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            return _make_ai_response("Reviewing...")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state()
        state["prior_comments"] = [
            {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Missing check"},
        ]
        state["last_reviewed_sha"] = "old123"

        scan_call(state)

        system_content = captured["messages"][0].content
        assert "Re-review Context" in system_content
        assert "old123" in system_content
        assert "Missing check" in system_content

    def test_scan_call_includes_incremental_diff_in_human_msg(self, monkeypatch):
        """When prior_comments is non-empty, human message includes incremental diff marker."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            return _make_ai_response("Reviewing...")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state()
        state["prior_comments"] = [
            {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
        ]
        state["last_reviewed_sha"] = "old123"

        scan_call(state)

        human_content = captured["messages"][1].content
        assert "PR #" in human_content

    def test_scan_call_no_addendum_on_first_review(self, monkeypatch):
        """When prior_comments is empty, system prompt has no re-review addendum."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            return _make_ai_response("Reviewing...")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state()
        state["prior_comments"] = []
        state["last_reviewed_sha"] = ""

        scan_call(state)

        system_content = captured["messages"][0].content
        assert "Re-review Context" not in system_content
```

Note: the existing `_make_state()` helper in `tests/test_agent_graph.py` needs to include the new fields. Update it to add:

```python
"prior_comments": [],
"last_reviewed_sha": "",
```

Also ensure `_make_ai_response` exists (it should already from prior tests — it creates an AIMessage).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_graph.py::TestReReviewInjection -v`
Expected: FAIL (scan_call doesn't inject re-review context yet)

- [ ] **Step 3: Modify scan_call in graph.py**

Update the `scan_call` function in `app/agent/graph.py`. Replace the existing prompt injection block (the `if state["round_count"] == 0:` block) with:

```python
def scan_call(state: ReviewState) -> dict:
    """Invoke the scan LLM with tools. Tracks round count and token usage."""
    llm = _build_scan_llm().bind_tools(ALL_TOOLS)

    # Inject system prompt on first round
    messages = list(state["messages"])
    if state["round_count"] == 0:
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.agent.prompts import RE_REVIEW_ADDENDUM

        # Build system prompt, with re-review addendum if applicable
        system_prompt = SCAN_SYSTEM_PROMPT
        prior_comments = state.get("prior_comments", [])
        if prior_comments:
            formatted_comments = "\n".join(
                f"- [{c['severity']}] {c['filename']}:L{c['line']} — {c['comment']} (id: {c['id']})"
                for c in prior_comments
            )
            system_prompt += RE_REVIEW_ADDENDUM.format(
                last_reviewed_sha=state.get("last_reviewed_sha", "unknown"),
                prior_comments=formatted_comments,
            )

        human_content = f"Review PR #{state['pr_number']} in repository {state['repo']} (branch ref: {state['ref']})."

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content),
        ] + messages

    response = llm.invoke(messages)
    logger.info("scan_call", round=state["round_count"] + 1)

    token_usage = response.usage_metadata or {}
    input_tokens = token_usage.get("input_tokens", 0)

    return {
        "messages": [response],
        "round_count": state["round_count"] + 1,
        "total_input_tokens": state["total_input_tokens"] + input_tokens,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_graph.py -v`
Expected: All PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: inject re-review context into scan_call prompt"
```

---

### Task 6: Modify `run_review` for re-review detection

**Files:**
- Modify: `app/tasks/review.py`
- Test: `tests/test_review_task.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_review_task.py`:

```python
"""Tests for run_review re-review detection logic."""

from unittest.mock import patch, MagicMock, call


class TestRunReviewReReview:
    @patch("app.tasks.review.post_review")
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_pr_incremental_diff")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_pr_head_sha")
    def test_re_review_passes_prior_comments_to_graph(
        self, mock_sha, mock_last, mock_diff, mock_graph, mock_resolve, mock_save, mock_post,
    ):
        mock_sha.return_value = "newsha456"
        mock_last.return_value = {
            "reviewed_sha": "oldsha123",
            "comments": [
                {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
            ],
        }
        mock_diff.return_value = []

        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 3, "total_input_tokens": 5000,
            "traces": [], "prior_comments": [
                {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
            ], "last_reviewed_sha": "oldsha123",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        # Import and call directly (bypass Celery decorator)
        from app.tasks.review import run_review
        run_review("org/repo", 42)

        # Verify graph was invoked with prior_comments
        invoke_args = mock_graph.return_value.invoke.call_args[0][0]
        assert invoke_args["prior_comments"] == mock_last.return_value["comments"]
        assert invoke_args["last_reviewed_sha"] == "oldsha123"

    @patch("app.tasks.review.post_review")
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_pr_head_sha")
    def test_first_review_has_empty_prior_comments(
        self, mock_sha, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
    ):
        mock_sha.return_value = "abc123"
        mock_last.return_value = None  # No prior review

        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        from app.tasks.review import run_review
        run_review("org/repo", 42)

        invoke_args = mock_graph.return_value.invoke.call_args[0][0]
        assert invoke_args["prior_comments"] == []
        assert invoke_args["last_reviewed_sha"] == ""

    @patch("app.tasks.review.post_review")
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review")
    @patch("app.tasks.review.get_pr_head_sha")
    def test_resolved_comments_are_persisted(
        self, mock_sha, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
    ):
        mock_sha.return_value = "newsha"
        mock_last.return_value = {
            "reviewed_sha": "oldsha",
            "comments": [
                {"id": 5, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Issue"},
            ],
        }

        mock_result = {
            "risk_level": "low", "summary": "OK",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 5},
            ],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "oldsha",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        from app.tasks.review import run_review
        run_review("org/repo", 42)

        mock_resolve.assert_called_once_with([5])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_review_task.py -v`
Expected: FAIL with `ImportError` (new imports not yet in review.py)

- [ ] **Step 3: Modify run_review**

Replace `app/tasks/review.py` with:

```python
"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha, get_pr_incremental_diff
from app.services.persistence import save_review, get_last_review, resolve_comments

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
    End-to-end PR review using LangGraph agent:
    1. Detect re-review (query PostgreSQL for prior review)
    2. Build graph and invoke with PR context + prior comments
    3. Persist results, resolve old comments
    4. Post review to GitHub
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Re-review detection
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

        # Build and invoke graph
        graph = build_review_graph()
        result = graph.invoke({
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
            "compressed": False,
            "prior_comments": prior_comments,
            "last_reviewed_sha": last_reviewed_sha,
        })

        log.info(
            "agent_complete",
            risk=result["risk_level"],
            escalated=result["escalated"],
            comments=len(result["comments"]),
            traces=len(result.get("traces", [])),
        )

        # Extract resolved prior comment IDs from agent output
        resolved_ids = [
            c["prior_comment_id"]
            for c in result.get("comments", [])
            if c.get("severity") == "resolved" and c.get("prior_comment_id")
        ]

        # Persist new review to PostgreSQL
        save_review(repo_full_name, pr_number, ref, result)

        # Mark old comments as resolved
        if resolved_ids:
            resolve_comments(resolved_ids)
            log.info("prior_comments_resolved", count=len(resolved_ids))

        # Post review to GitHub (filter out resolved-status comments)
        post_review(repo_full_name, pr_number, result)
        log.info("review_posted")

    except Exception as exc:
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_review_task.py -v`
Expected: All 3 PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add app/tasks/review.py tests/test_review_task.py
git commit -m "feat: add re-review detection and resolution to run_review"
```

---

### Task 7: Modify `post_review` for re-review summary

**Files:**
- Modify: `app/services/reviewer.py`
- Test: `tests/test_reviewer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reviewer.py`:

```python
"""Tests for post_review re-review behavior."""

from unittest.mock import patch, MagicMock


class TestPostReviewReReview:
    @patch("app.services.reviewer._github_client")
    def test_filters_resolved_comments_from_posting(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Some issues",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "New issue"},
                {"filename": "b.py", "line": 20, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 5},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        # Only non-resolved comments should be posted as inline comments
        call_args = mock_pr.create_review.call_args
        gh_comments = call_args[1].get("comments") or call_args.kwargs.get("comments")
        assert len(gh_comments) == 1
        assert "a.py" in gh_comments[0]["path"]

    @patch("app.services.reviewer._github_client")
    def test_includes_resolution_summary(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "Re-reviewed",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 1},
                {"filename": "b.py", "line": 20, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 2},
                {"filename": "c.py", "line": 30, "severity": "warning", "comment": "New issue"},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        call_args = mock_pr.create_review.call_args
        body = call_args[1].get("body") or call_args.kwargs.get("body")
        assert "2" in body  # 2 resolved
        assert "resolved" in body.lower()

    @patch("app.services.reviewer._github_client")
    def test_all_resolved_no_new_issues(self, mock_client):
        mock_pr = MagicMock()
        mock_client.return_value.get_repo.return_value.get_pull.return_value = mock_pr

        result = {
            "risk_level": "low",
            "summary": "All fixed",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "resolved", "comment": "Fixed", "prior_comment_id": 1},
            ],
        }

        from app.services.reviewer import post_review
        post_review("org/repo", 42, result)

        # Should post as issue comment (no inline comments)
        mock_pr.create_issue_comment.assert_called_once()
        body = mock_pr.create_issue_comment.call_args[0][0]
        assert "resolved" in body.lower() or "addressed" in body.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_reviewer.py -v`
Expected: FAIL (post_review doesn't filter resolved comments yet)

- [ ] **Step 3: Modify post_review**

Replace `app/services/reviewer.py` with:

```python
"""Post review results to GitHub as PR review comments."""

from app.core.logging import get_logger
from app.services.github import _github_client

logger = get_logger(__name__)

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🔵"}


def post_review(repo_full_name: str, pr_number: int, result: dict) -> None:
    """Post the agent's review to GitHub as a PR review with inline comments.

    Args:
        result: Graph output dict with keys: risk_level, summary, comments.
               Comments with severity="resolved" are filtered out of inline posting.
    """
    gh = _github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)

    summary = result.get("summary", "")
    all_comments = result.get("comments", [])
    risk_level = result.get("risk_level", "low")

    # Separate resolved vs new/open comments
    resolved = [c for c in all_comments if c.get("severity") == "resolved"]
    active_comments = [c for c in all_comments if c.get("severity") != "resolved"]

    if not active_comments and not summary and not resolved:
        return

    # Build body with resolution summary if applicable
    body = f"## AI Review (risk: {risk_level})\n\n{summary}"
    if resolved:
        body += f"\n\n**Re-review:** {len(resolved)} prior issue(s) resolved."

    gh_comments = []
    for c in active_comments:
        emoji = _SEVERITY_EMOJI.get(c.get("severity", "suggestion"), "🔵")
        gh_comments.append({
            "path": c.get("filename", "unknown"),
            "line": c.get("line", 1),
            "side": "RIGHT",
            "body": f"{emoji} **{c.get('severity', 'suggestion')}**: {c.get('comment', '')}",
        })

    try:
        if gh_comments:
            pr.create_review(body=body, event="COMMENT", comments=gh_comments)
        else:
            pr.create_issue_comment(body)
    except Exception as exc:
        logger.warning("inline_review_failed_fallback", error=str(exc))
        fallback = body + "\n\n### Findings\n\n"
        for c in active_comments:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "🔵")
            fallback += f"- {emoji} **{c.get('filename', 'unknown')}:{c.get('line', '?')}** — {c.get('comment', '')}\n"
        pr.create_issue_comment(fallback)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reviewer.py -v`
Expected: All 3 PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/reviewer.py tests/test_reviewer.py
git commit -m "feat: filter resolved comments and add resolution summary in post_review"
```
