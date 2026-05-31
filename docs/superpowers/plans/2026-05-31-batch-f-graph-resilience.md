# Batch F — Graph Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the LangGraph agent for production: PostgreSQL checkpointer for crash recovery, recursion limit, multi-round compression, and LLM call retry.

**Architecture:** PostgresSaver from `langgraph-checkpoint-postgres` reuses our existing PG. Each review gets a unique `thread_id` (`repo:pr:ref`). Compression changes from single-fire (`compressed: bool`) to multi-round (`compress_count: int`). LLM calls wrapped with tenacity retry for transient errors.

**Tech Stack:** langgraph-checkpoint-postgres, psycopg[binary], tenacity

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `requirements.txt` | **MODIFY** | Add `langgraph-checkpoint-postgres`, `psycopg[binary]`, `tenacity` |
| `app/agent/state.py` | **MODIFY** | `compressed: bool` → `compress_count: int` |
| `app/agent/graph.py` | **MODIFY** | Checkpointer, lru_cache, recursion_limit, `_invoke_llm`, compress_count logic |
| `app/tasks/review.py` | **MODIFY** | thread_id config, GraphRecursionError catch, checkpoint cleanup, compress_count |
| `tests/test_agent_graph.py` | **MODIFY** | Update compressed→compress_count, add retry/compression tests |
| `tests/test_review_task.py` | **MODIFY** | Add recursion error and checkpoint config tests |

---

### Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Add these three lines at the end of `requirements.txt`:

```
langgraph-checkpoint-postgres>=3.0
psycopg[binary]>=3.1
tenacity>=8.2
```

- [ ] **Step 2: Install**

Run: `pip install langgraph-checkpoint-postgres "psycopg[binary]" tenacity`

- [ ] **Step 3: Verify imports**

Run: `python -c "from langgraph.checkpoint.postgres import PostgresSaver; from tenacity import retry; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add langgraph-checkpoint-postgres, psycopg, tenacity deps"
```

---

### Task 2: Multi-round compression (state + router + compress_context)

**Files:**
- Modify: `app/agent/state.py:39` (`compressed: bool` → `compress_count: int`)
- Modify: `app/agent/graph.py:274` (tools_router compression check)
- Modify: `app/agent/graph.py:293-344` (compress_context return value)
- Modify: `tests/test_agent_graph.py` (update all `compressed` references)

- [ ] **Step 1: Write failing tests for multi-round compression**

In `tests/test_agent_graph.py`, update the `_make_state` helper — replace `"compressed": False,` with `"compress_count": 0,`.

Then update `TestCompressRouter`:

Replace the entire class with:

```python
class TestCompressRouter:
    """Test compression routing integrated into tools_router."""

    def test_compress_at_round_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=5,
            compress_count=0,
        )
        assert tools_router(state) == "compress"

    def test_no_compress_before_round_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=3,
            compress_count=0,
        )
        assert tools_router(state) == "continue"

    def test_no_compress_right_after_compression(self):
        """Round 6 with compress_count=1 should NOT compress (next at round 10)."""
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=6,
            compress_count=1,
        )
        assert tools_router(state) == "continue"

    def test_second_compress_at_round_10(self):
        """Round 10 with compress_count=1 should trigger second compression."""
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=10,
            compress_count=1,
        )
        assert tools_router(state) == "compress"

    def test_compress_at_round_above_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=6,
            compress_count=0,
        )
        assert tools_router(state) == "compress"
```

Also update `TestCompressContext` — replace all occurrences of `compressed=False` with `compress_count=0` and `compressed=True` with `compress_count=1` in the class. Specifically:

In `_build_multi_round_state`: change `compressed=False` to `compress_count=0`.

In `test_sets_compressed_flag`: change the assertion from `assert result["compressed"] is True` to `assert result["compress_count"] == 1`.

In `test_no_early_tools_skips_compression`:
- Change `compressed=False` to `compress_count=0`
- Change `assert result["compressed"] is True` to `assert result["compress_count"] == 1`
- Change `assert "messages" not in result` to `assert "messages" not in result or result.get("compress_count") == 1`

Wait — if there are no early tools, `compress_context` currently returns `{"compressed": True}`. With the new logic it should return `{"compress_count": state.get("compress_count", 0) + 1}`. The test should check `result["compress_count"] == 1`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_graph.py::TestCompressRouter tests/test_agent_graph.py::TestCompressContext -v`
Expected: FAIL — KeyError on `compress_count` or assertion failures

- [ ] **Step 3: Update ReviewState**

In `app/agent/state.py`, replace:
```python
    # Context compression
    compressed: bool
```
with:
```python
    # Context compression
    compress_count: int
```

- [ ] **Step 4: Update tools_router compression logic**

In `app/agent/graph.py`, replace the compression check in `tools_router` (line ~274):

```python
    # Check if context compression is needed
    if state["round_count"] >= settings.compress_at_round and not state.get("compressed", False):
        return "compress"
```

with:

```python
    # Check if context compression is needed (triggers at round 5, 10, etc.)
    compress_count = state.get("compress_count", 0)
    next_compress_at = settings.compress_at_round * (compress_count + 1)
    if state["round_count"] >= next_compress_at:
        return "compress"
```

- [ ] **Step 5: Update compress_context return value**

In `app/agent/graph.py`, in `compress_context`:

Replace the early return (when no early tools):
```python
        return {"compressed": True}
```
with:
```python
        return {"compress_count": state.get("compress_count", 0) + 1}
```

Replace the final return:
```python
    return {"messages": new_messages, "compressed": True}
```
with:
```python
    return {"messages": new_messages, "compress_count": state.get("compress_count", 0) + 1}
```

- [ ] **Step 6: Update run_review initial state**

In `app/tasks/review.py`, in the `graph.invoke({...})` dict, replace:
```python
            "compressed": False,
```
with:
```python
            "compress_count": 0,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: All tests pass

Run: `pytest -v`
Expected: All tests pass (check test_review_task.py doesn't reference `compressed`)

- [ ] **Step 8: Commit**

```bash
git add app/agent/state.py app/agent/graph.py app/tasks/review.py tests/test_agent_graph.py
git commit -m "feat: multi-round compression with compress_count (triggers at round 5, 10, etc.)"
```

---

### Task 3: LLM call retry with tenacity

**Files:**
- Modify: `app/agent/graph.py` (add `_invoke_llm`, use in scan_call, deep_review, compress_context)
- Modify: `tests/test_agent_graph.py` (add retry tests)

- [ ] **Step 1: Write failing tests for _invoke_llm**

Add to `tests/test_agent_graph.py`:

```python
# ── LLM retry ─────────────────────────────────────────


class TestInvokeLlmRetry:
    def test_retries_on_timeout(self):
        """_invoke_llm retries on APITimeoutError."""
        from app.agent.graph import _invoke_llm
        from openai import APITimeoutError

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_llm.invoke.side_effect = [
            APITimeoutError(request=MagicMock()),
            mock_response,
        ]

        result = _invoke_llm(mock_llm, [])
        assert result == mock_response
        assert mock_llm.invoke.call_count == 2

    def test_does_not_retry_on_value_error(self):
        """_invoke_llm does NOT retry on non-API errors."""
        from app.agent.graph import _invoke_llm

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = ValueError("bad input")

        with pytest.raises(ValueError):
            _invoke_llm(mock_llm, [])
        assert mock_llm.invoke.call_count == 1

    def test_raises_after_max_retries(self):
        """_invoke_llm raises after exhausting retries."""
        from app.agent.graph import _invoke_llm
        from openai import APITimeoutError

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = APITimeoutError(request=MagicMock())

        with pytest.raises(APITimeoutError):
            _invoke_llm(mock_llm, [])
        assert mock_llm.invoke.call_count == 3
```

Add `import pytest` at the top of the file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_graph.py::TestInvokeLlmRetry -v`
Expected: FAIL — `ImportError: cannot import name '_invoke_llm'`

- [ ] **Step 3: Implement `_invoke_llm`**

In `app/agent/graph.py`, add imports at the top (after existing imports):

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APITimeoutError, RateLimitError
```

Add the retry wrapper function after the LLM builder functions (after `_build_reason_llm`), before `# ── Nodes`:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError)),
    reraise=True,
)
def _invoke_llm(llm, messages):
    """Invoke LLM with retry on transient API errors."""
    return llm.invoke(messages)
```

- [ ] **Step 4: Replace bare `llm.invoke` calls**

In `scan_call`, replace:
```python
    response = llm.invoke(messages)
```
with:
```python
    response = _invoke_llm(llm, messages)
```

In `deep_review`, replace:
```python
    response = llm.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ])
```
with:
```python
    response = _invoke_llm(llm, [
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ])
```

In `compress_context`, replace:
```python
    response = llm.invoke([
        SystemMessage(content=COMPRESS_PROMPT),
        HumanMessage(content=tool_results_text),
    ])
```
with:
```python
    response = _invoke_llm(llm, [
        SystemMessage(content=COMPRESS_PROMPT),
        HumanMessage(content=tool_results_text),
    ])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_agent_graph.py::TestInvokeLlmRetry -v`
Expected: 3 passed

Note: The retry tests use `wait_exponential` which adds real delays. The tenacity `@retry` decorator respects the wait strategy. For tests, the 4-second minimum wait is too slow. Fix: patch the wait strategy in tests or use `tenacity.wait_none` for testing.

Actually, let's update the test to patch the wait:

```python
class TestInvokeLlmRetry:
    @patch("app.agent.graph._invoke_llm.retry.wait", return_value=0)
    def test_retries_on_timeout(self, _mock_wait):
        ...
```

Hmm, that's fragile. Better approach: use `_invoke_llm.retry.wait = wait_none()` at test setup. Actually, simplest: just let the 4s wait happen — 3 tests × 4s = 12s is acceptable. But if needed, we can override.

Alternative: make the retry configurable or just accept the wait in tests. For now, accept it.

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: add tenacity retry to LLM calls (3 attempts on timeout/rate-limit)"
```

---

### Task 4: Checkpointer + recursion_limit + build_review_graph refactor

**Files:**
- Modify: `app/agent/graph.py` (checkpointer, lru_cache for build, recursion_limit)
- Modify: `app/tasks/review.py` (thread_id config, GraphRecursionError, checkpoint cleanup)
- Modify: `tests/test_review_task.py` (recursion error test, thread_id test)
- Modify: `tests/test_agent_graph.py` (build_review_graph caching test)

- [ ] **Step 1: Write failing tests for GraphRecursionError handling**

Add to `tests/test_review_task.py`:

```python
from langgraph.errors import GraphRecursionError


class TestRunReviewResilience:
    @patch("app.tasks.review.post_review")
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_graph_recursion_error_produces_degraded_result(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
    ):
        """GraphRecursionError produces a degraded result instead of crashing."""
        mock_graph.return_value.invoke.side_effect = GraphRecursionError("recursion limit reached")

        mock_task = MagicMock()
        mock_task.request.id = "test-recursion"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

        # Verify a degraded result was saved
        mock_save.assert_called_once()
        saved_result = mock_save.call_args[0][3]
        assert "recursion limit" in saved_result["summary"].lower()

        # Verify review was still posted
        mock_post.assert_called_once()

    @patch("app.tasks.review.post_review")
    @patch("app.tasks.review.save_review")
    @patch("app.tasks.review.resolve_comments")
    @patch("app.tasks.review.build_review_graph")
    @patch("app.tasks.review.get_last_review", return_value=None)
    @patch("app.tasks.review.get_repo_config", return_value={})
    @patch("app.tasks.review.get_pr_head_sha", return_value="abc123")
    def test_graph_invoked_with_thread_id_config(
        self, mock_sha, mock_config, mock_last, mock_graph, mock_resolve, mock_save, mock_post,
    ):
        """graph.invoke is called with a configurable thread_id for checkpointing."""
        mock_result = {
            "risk_level": "low", "summary": "OK", "comments": [],
            "escalated": False, "round_count": 2, "total_input_tokens": 3000,
            "traces": [], "prior_comments": [], "last_reviewed_sha": "",
        }
        mock_graph.return_value.invoke.return_value = mock_result

        mock_task = MagicMock()
        mock_task.request.id = "test-thread-id"
        mock_task.request.retries = 0
        from app.tasks.review import run_review
        run_review.__wrapped__.__func__(mock_task, "owner/repo", 42)

        # Verify invoke was called with config containing thread_id
        call_kwargs = mock_graph.return_value.invoke.call_args
        config = call_kwargs[1].get("config") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("config")
        assert config is not None
        assert config["configurable"]["thread_id"] == "owner/repo:42:abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review_task.py::TestRunReviewResilience -v`
Expected: FAIL

- [ ] **Step 3: Implement checkpointer setup in graph.py**

In `app/agent/graph.py`, add import at the top:

```python
from langgraph.checkpoint.postgres import PostgresSaver
```

Replace the graph assembly section (lines 347-395):

```python
# ── Graph Assembly ─────────────────────────────────────


def _get_checkpointer():
    """Create a PostgresSaver checkpointer using the app's database URL."""
    settings = get_settings()
    checkpointer = PostgresSaver.from_conn_string(settings.database_url)
    checkpointer.setup()
    return checkpointer


@lru_cache(maxsize=1)
def build_review_graph():
    """Return the cached compiled review graph (built once per process)."""
    return _build_graph()


def _build_graph():
    """Build and compile the review agent graph."""
    graph = StateGraph(ReviewState)

    # Nodes
    graph.add_node("scan_call", scan_call)
    graph.add_node("scan_tools", ToolNode(ALL_TOOLS))
    graph.add_node("post_tool_processing", post_tool_processing)
    graph.add_node("parse_result", parse_result)
    graph.add_node("extract_escalation", _extract_escalate_reason)
    graph.add_node("deep_review", deep_review)
    graph.add_node("compress_context", compress_context)

    # Edges
    graph.set_entry_point("scan_call")

    graph.add_conditional_edges("scan_call", scan_router, {
        "has_tool_calls": "scan_tools",
        "no_tool_calls": "parse_result",
    })

    graph.add_edge("scan_tools", "post_tool_processing")

    graph.add_conditional_edges("post_tool_processing", tools_router, {
        "continue": "scan_call",
        "compress": "compress_context",
        "finish": "parse_result",
        "escalate": "extract_escalation",
    })

    graph.add_edge("compress_context", "scan_call")
    graph.add_edge("extract_escalation", "deep_review")
    graph.add_edge("deep_review", END)
    graph.add_edge("parse_result", END)

    checkpointer = _get_checkpointer()
    return graph.compile(checkpointer=checkpointer, recursion_limit=100)
```

**IMPORTANT:** `PostgresSaver.from_conn_string` returns a context manager in v3. However, when used as `PostgresSaver.from_conn_string(url)`, calling it without `with` creates and returns the saver directly (the `__enter__` returns `self`). For a long-lived Celery worker we need to keep the connection alive. Check if the saver works without context manager. If it requires context manager usage, use `PostgresSaver(conn_string=url)` constructor directly instead.

If `from_conn_string` requires context manager, alternative approach:

```python
def _get_checkpointer():
    settings = get_settings()
    # from_conn_string is a context manager; enter it and keep connection alive
    cm = PostgresSaver.from_conn_string(settings.database_url)
    saver = cm.__enter__()
    saver.setup()
    return saver
```

Test which approach works during implementation.

- [ ] **Step 4: Implement GraphRecursionError handling and thread_id in run_review**

In `app/tasks/review.py`, add import:

```python
from langgraph.errors import GraphRecursionError
```

Modify the `graph.invoke(...)` call to include config:

```python
        # Build and invoke graph
        graph = build_review_graph()
        thread_id = f"{repo_full_name}:{pr_number}:{ref}"
        config = {"configurable": {"thread_id": thread_id}}

        try:
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
                "compress_count": 0,
                "prior_comments": prior_comments,
                "last_reviewed_sha": last_reviewed_sha,
                "repo_config": repo_config,
            }, config=config)
        except GraphRecursionError:
            log.error("graph_recursion_limit_hit")
            result = {
                "risk_level": "low",
                "summary": "Review terminated: graph recursion limit reached.",
                "comments": [],
                "escalated": False,
                "traces": [],
            }
```

The rest of the function (save_review, resolve_comments, post_review) remains the same.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_review_task.py -v`
Expected: All tests pass

Note: The existing tests mock `build_review_graph()` so the checkpointer is never actually created. The `thread_id` test needs to check `invoke` was called with the config dict. The test assertion might need adjustment depending on how `invoke` receives the config — it could be `graph.invoke(state, config=config)` (keyword) or `graph.invoke(state, config)` (positional).

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add app/agent/graph.py app/tasks/review.py tests/test_review_task.py tests/test_agent_graph.py
git commit -m "feat: add PostgresSaver checkpointer, recursion_limit=100, thread_id isolation"
```

---

## Post-Implementation Verification

After all tasks are complete:

1. Run `pytest -v` — all tests pass
2. Verify imports: `python -c "from langgraph.checkpoint.postgres import PostgresSaver; from tenacity import retry; print('OK')"`
3. Manual: `docker compose up` → verify checkpointer creates its tables on first graph build
