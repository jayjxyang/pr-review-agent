# Batch A — P0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the P0 agent production-ready with automated tests, dead loop detection, and object caching.

**Architecture:** Add pytest unit tests for all pure routing/parsing functions. Extend `ReviewState` with `tool_call_history` and add a `post_tool_processing` node for dead loop detection. Cache compiled graph and LLM clients at module level.

**Tech Stack:** Python 3.11, pytest, LangGraph, langchain-core

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/__init__.py` | **NEW** — Test package init |
| `tests/test_agent_graph.py` | **NEW** — Unit tests for routing, parsing, control tools |
| `app/agent/state.py` | **MODIFY** — Add `tool_call_history` field |
| `app/agent/graph.py` | **MODIFY** — Add `post_tool_processing` node, dead loop detection, caching |
| `app/tasks/review.py` | **MODIFY** — Add `tool_call_history` to initial state |

---

### Task 1: Test Infrastructure + Control Tool Tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_agent_graph.py`

- [ ] **Step 1: Create test package**

Create empty `tests/__init__.py`.

- [ ] **Step 2: Install pytest**

Run: `pip install pytest`

- [ ] **Step 3: Write control tool tests**

```python
"""Tests for agent graph routing, parsing, and control tools."""

import json

from app.services.tools.control import (
    finish_review,
    escalate,
    FINISH_REVIEW_SIGNAL,
    ESCALATE_SIGNAL,
)


# ── Control Tools ──────────────────────────────────────


class TestFinishReview:
    def test_returns_finish_signal(self):
        result = json.loads(finish_review.invoke({
            "risk_level": "medium",
            "summary": "Found one issue",
            "comments": [{"filename": "a.py", "line": 10, "severity": "warning", "comment": "bad"}],
        }))
        assert result["signal"] == FINISH_REVIEW_SIGNAL
        assert result["risk_level"] == "medium"
        assert result["summary"] == "Found one issue"
        assert len(result["comments"]) == 1

    def test_empty_comments(self):
        result = json.loads(finish_review.invoke({
            "risk_level": "low",
            "summary": "All good",
            "comments": [],
        }))
        assert result["signal"] == FINISH_REVIEW_SIGNAL
        assert result["comments"] == []


class TestEscalate:
    def test_returns_escalate_signal(self):
        result = json.loads(escalate.invoke({"reason": "touches auth module"}))
        assert result["signal"] == ESCALATE_SIGNAL
        assert result["reason"] == "touches auth module"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: add control tool tests (finish_review, escalate)"
```

---

### Task 2: Router Tests — scan_router

**Files:**
- Modify: `tests/test_agent_graph.py`

- [ ] **Step 1: Add scan_router tests**

Append to `tests/test_agent_graph.py`:

```python
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.graph import scan_router, tools_router, parse_result, _extract_escalate_reason


# ── Helper ─────────────────────────────────────────────


def _make_state(**overrides) -> dict:
    """Create a minimal ReviewState dict for testing."""
    base = {
        "messages": [],
        "repo": "test/repo",
        "pr_number": 1,
        "ref": "abc123",
        "risk_level": "",
        "summary": "",
        "comments": [],
        "escalated": False,
        "escalate_reason": "",
        "round_count": 0,
        "total_input_tokens": 0,
        "tool_call_history": [],
    }
    base.update(overrides)
    return base


# ── scan_router ────────────────────────────────────────


class TestScanRouter:
    def test_has_tool_calls(self):
        msg = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"repo": "x", "path": "y", "ref": "z"}, "id": "1"}])
        state = _make_state(messages=[msg])
        assert scan_router(state) == "has_tool_calls"

    def test_no_tool_calls(self):
        msg = AIMessage(content="Looks good")
        state = _make_state(messages=[msg])
        assert scan_router(state) == "no_tool_calls"

    def test_empty_tool_calls_list(self):
        msg = AIMessage(content="Done", tool_calls=[])
        state = _make_state(messages=[msg])
        assert scan_router(state) == "no_tool_calls"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_agent_graph.py::TestScanRouter -v`
Expected: 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_graph.py
git commit -m "test: add scan_router tests"
```

---

### Task 3: Router Tests — tools_router

**Files:**
- Modify: `tests/test_agent_graph.py`

- [ ] **Step 1: Add tools_router tests**

Append to `tests/test_agent_graph.py`:

```python
# ── tools_router ───────────────────────────────────────


class TestToolsRouter:
    def test_finish_signal(self):
        tool_msg = ToolMessage(
            content=json.dumps({"signal": FINISH_REVIEW_SIGNAL, "risk_level": "low", "summary": "ok", "comments": []}),
            tool_call_id="1",
        )
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        assert tools_router(state) == "finish"

    def test_escalate_signal(self):
        tool_msg = ToolMessage(
            content=json.dumps({"signal": ESCALATE_SIGNAL, "reason": "risky"}),
            tool_call_id="1",
        )
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        assert tools_router(state) == "escalate"

    def test_max_rounds_exceeded(self):
        tool_msg = ToolMessage(content="some result", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg], round_count=15)
        assert tools_router(state) == "finish"

    def test_token_budget_exceeded(self):
        tool_msg = ToolMessage(content="some result", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg], total_input_tokens=60000)
        assert tools_router(state) == "finish"

    def test_continue_normal(self):
        tool_msg = ToolMessage(content="file contents here", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg], round_count=3, total_input_tokens=5000)
        assert tools_router(state) == "continue"

    def test_malformed_json_continues(self):
        tool_msg = ToolMessage(content="not json at all", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg], round_count=1)
        assert tools_router(state) == "continue"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_agent_graph.py::TestToolsRouter -v`
Expected: 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_graph.py
git commit -m "test: add tools_router tests (signals, budget, malformed JSON)"
```

---

### Task 4: Parser Tests — parse_result and _extract_escalate_reason

**Files:**
- Modify: `tests/test_agent_graph.py`

- [ ] **Step 1: Add parse_result tests**

Append to `tests/test_agent_graph.py`:

```python
# ── parse_result ───────────────────────────────────────


class TestParseResult:
    def test_extracts_finish_signal(self):
        tool_msg = ToolMessage(
            content=json.dumps({
                "signal": FINISH_REVIEW_SIGNAL,
                "risk_level": "medium",
                "summary": "Found issues",
                "comments": [{"filename": "a.py", "line": 5, "severity": "error", "comment": "bug"}],
            }),
            tool_call_id="1",
        )
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        result = parse_result(state)
        assert result["risk_level"] == "medium"
        assert result["summary"] == "Found issues"
        assert len(result["comments"]) == 1

    def test_no_finish_signal_returns_default(self):
        tool_msg = ToolMessage(content="just some text", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        result = parse_result(state)
        assert result["risk_level"] == "low"
        assert "terminated early" in result["summary"]
        assert result["comments"] == []

    def test_malformed_json_returns_default(self):
        tool_msg = ToolMessage(content="{invalid json", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        result = parse_result(state)
        assert result["risk_level"] == "low"

    def test_finds_signal_among_multiple_messages(self):
        msgs = [
            AIMessage(content=""),
            ToolMessage(content="file content", tool_call_id="1"),
            AIMessage(content=""),
            ToolMessage(
                content=json.dumps({"signal": FINISH_REVIEW_SIGNAL, "risk_level": "high", "summary": "critical", "comments": []}),
                tool_call_id="2",
            ),
        ]
        state = _make_state(messages=msgs)
        result = parse_result(state)
        assert result["risk_level"] == "high"


# ── _extract_escalate_reason ───────────────────────────


class TestExtractEscalateReason:
    def test_extracts_reason(self):
        tool_msg = ToolMessage(
            content=json.dumps({"signal": ESCALATE_SIGNAL, "reason": "auth module changed"}),
            tool_call_id="1",
        )
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        result = _extract_escalate_reason(state)
        assert result["escalated"] is True
        assert result["escalate_reason"] == "auth module changed"

    def test_no_signal_returns_unknown(self):
        tool_msg = ToolMessage(content="regular content", tool_call_id="1")
        state = _make_state(messages=[AIMessage(content=""), tool_msg])
        result = _extract_escalate_reason(state)
        assert result["escalated"] is True
        assert result["escalate_reason"] == "unknown"
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all tests PASS (3 + 3 + 6 + 6 = 18 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_graph.py
git commit -m "test: add parse_result and _extract_escalate_reason tests"
```

---

### Task 5: Dead Loop Detection — State and Node

**Files:**
- Modify: `app/agent/state.py`
- Modify: `app/agent/graph.py`
- Modify: `app/tasks/review.py`

- [ ] **Step 1: Add `tool_call_history` to ReviewState**

In `app/agent/state.py`, add after the `total_input_tokens` line:

```python
    # Dead loop detection
    tool_call_history: list[str]
```

- [ ] **Step 2: Add `post_tool_processing` node to graph.py**

In `app/agent/graph.py`, add these imports at the top (after `import json`):

```python
import hashlib
```

Then add the node function after the `parse_result` function (before `deep_review`):

```python
def post_tool_processing(state: ReviewState) -> dict:
    """Record tool call fingerprints for dead loop detection."""
    history = list(state.get("tool_call_history", []))

    # Find the last AIMessage with tool_calls (the one that triggered scan_tools)
    for msg in reversed(state["messages"]):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                params_str = json.dumps(tc.get("args", {}), sort_keys=True)
                fingerprint = f"{tc['name']}:{hashlib.md5(params_str.encode()).hexdigest()[:8]}"
                history.append(fingerprint)
            break

    return {"tool_call_history": history}
```

- [ ] **Step 3: Add dead loop check to `tools_router`**

In `app/agent/graph.py`, in the `tools_router` function, add this check after the token budget check and before the final `return "continue"`:

```python
    # Check for dead loop (3 consecutive identical tool calls)
    history = state.get("tool_call_history", [])
    if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
        logger.warning("dead_loop_detected", tool=history[-1])
        return "finish"
```

- [ ] **Step 4: Wire `post_tool_processing` into the graph**

In `app/agent/graph.py`, in the `build_review_graph` function (now `_build_graph`, see Task 6), update the graph wiring:

Add the node:
```python
    graph.add_node("post_tool_processing", post_tool_processing)
```

Change the `scan_tools` edge — instead of connecting `scan_tools` directly to `tools_router`, add `post_tool_processing` in between:

```python
    graph.add_edge("scan_tools", "post_tool_processing")

    graph.add_conditional_edges("post_tool_processing", tools_router, {
        "continue": "scan_call",
        "finish": "parse_result",
        "escalate": "extract_escalation",
    })
```

And remove the old `scan_tools → tools_router` conditional edges.

- [ ] **Step 5: Update initial state in review.py**

In `app/tasks/review.py`, add `"tool_call_history": []` to the `graph.invoke()` dict, after `"total_input_tokens": 0`:

```python
            "total_input_tokens": 0,
            "tool_call_history": [],
```

- [ ] **Step 6: Verify imports still work**

Run: `python -c "from app.agent import build_review_graph; g = build_review_graph(); print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add app/agent/state.py app/agent/graph.py app/tasks/review.py
git commit -m "feat: add dead loop detection (tool_call_history + post_tool_processing node)"
```

---

### Task 6: Dead Loop Detection — Tests

**Files:**
- Modify: `tests/test_agent_graph.py`

- [ ] **Step 1: Add post_tool_processing and dead loop tests**

Add import at the top of `tests/test_agent_graph.py`:

```python
from app.agent.graph import post_tool_processing
```

Append to the file:

```python
# ── post_tool_processing ───────────────────────────────


class TestPostToolProcessing:
    def test_records_fingerprint(self):
        ai_msg = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"repo": "x", "path": "y", "ref": "z"}, "id": "1"}])
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(messages=[ai_msg, tool_msg], tool_call_history=[])
        result = post_tool_processing(state)
        assert len(result["tool_call_history"]) == 1
        assert result["tool_call_history"][0].startswith("read_file:")

    def test_appends_to_existing_history(self):
        ai_msg = AIMessage(content="", tool_calls=[{"name": "search_code", "args": {"repo": "x", "query": "foo"}, "id": "2"}])
        tool_msg = ToolMessage(content="results", tool_call_id="2")
        state = _make_state(messages=[ai_msg, tool_msg], tool_call_history=["read_file:abc12345"])
        result = post_tool_processing(state)
        assert len(result["tool_call_history"]) == 2

    def test_same_params_same_fingerprint(self):
        args = {"repo": "x", "path": "a.py", "ref": "main"}
        ai_msg1 = AIMessage(content="", tool_calls=[{"name": "read_file", "args": args, "id": "1"}])
        tool_msg1 = ToolMessage(content="file", tool_call_id="1")
        state1 = _make_state(messages=[ai_msg1, tool_msg1], tool_call_history=[])
        r1 = post_tool_processing(state1)

        ai_msg2 = AIMessage(content="", tool_calls=[{"name": "read_file", "args": args, "id": "2"}])
        tool_msg2 = ToolMessage(content="file", tool_call_id="2")
        state2 = _make_state(messages=[ai_msg2, tool_msg2], tool_call_history=[])
        r2 = post_tool_processing(state2)

        assert r1["tool_call_history"][0] == r2["tool_call_history"][0]


# ── Dead loop detection in tools_router ────────────────


class TestDeadLoopDetection:
    def test_three_identical_calls_triggers_finish(self):
        fp = "read_file:abc12345"
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            tool_call_history=[fp, fp, fp],
            round_count=3,
        )
        assert tools_router(state) == "finish"

    def test_two_identical_calls_continues(self):
        fp = "read_file:abc12345"
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            tool_call_history=[fp, fp],
            round_count=2,
        )
        assert tools_router(state) == "continue"

    def test_three_different_calls_continues(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            tool_call_history=["read_file:aaa", "search_code:bbb", "read_file:ccc"],
            round_count=3,
        )
        assert tools_router(state) == "continue"
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all tests PASS (18 + 6 = 24 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_graph.py
git commit -m "test: add dead loop detection tests (post_tool_processing, tools_router)"
```

---

### Task 7: Graph and LLM Client Caching

**Files:**
- Modify: `app/agent/graph.py`

- [ ] **Step 1: Add `@lru_cache` to LLM builders**

In `app/agent/graph.py`, add `lru_cache` to the imports:

```python
from functools import lru_cache
```

Add `@lru_cache(maxsize=1)` decorator to both `_build_scan_llm` and `_build_reason_llm`:

```python
@lru_cache(maxsize=1)
def _build_scan_llm() -> ChatOpenAI:
    ...

@lru_cache(maxsize=1)
def _build_reason_llm() -> ChatOpenAI:
    ...
```

- [ ] **Step 2: Cache the compiled graph**

Rename `build_review_graph` to `_build_graph`. Add a new `build_review_graph` that caches:

```python
_compiled_graph = None


def build_review_graph():
    """Return the cached compiled review graph (built once per process)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph


def _build_graph():
    """Build and compile the review agent graph."""
    graph = StateGraph(ReviewState)
    # ... (all existing graph assembly code)
    return graph.compile()
```

- [ ] **Step 3: Verify import and graph compilation**

Run: `python -c "from app.agent import build_review_graph; g1 = build_review_graph(); g2 = build_review_graph(); print(g1 is g2)"`
Expected: `True`

- [ ] **Step 4: Run all tests**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all 24 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py
git commit -m "perf: cache compiled graph and LLM clients (lru_cache + module singleton)"
```

---

### Task 8: Final Smoke Test

- [ ] **Step 1: Full import chain**

Run: `python -c "from app.agent import build_review_graph; from app.services.tools import ALL_TOOLS; print(f'{len(ALL_TOOLS)} tools'); g = build_review_graph(); print('graph compiled')"`
Expected:
```
9 tools
graph compiled
```

- [ ] **Step 2: App starts**

Run: `python -c "import uvicorn, threading, time, urllib.request; t = threading.Thread(target=lambda: uvicorn.run('app.main:app', host='127.0.0.1', port=8005, log_level='error'), daemon=True); t.start(); time.sleep(4); print(urllib.request.urlopen('http://127.0.0.1:8005/health').read().decode())"`
Expected: `{"status":"ok"}`

- [ ] **Step 3: Full test suite**

Run: `pytest tests/ -v`
Expected: all 24 tests PASS
