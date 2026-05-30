# Batch C — Context Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress accumulated tool results at round 5 to prevent token budget blowup, and improve deep_review escalation handoff.

**Architecture:** Add a `compress_context` node and `compress_router` conditional edge to the existing LangGraph StateGraph. When `round_count >= compress_at_round` and `compressed == False`, route through compression before continuing the scan loop. The compression node calls the scan LLM (Flash) to summarize early-round tool results into a structured summary, then replaces the message history.

**Tech Stack:** LangGraph, LangChain, ChatOpenAI (Flash for compression)

---

### Task 1: Add `compressed` field to ReviewState and `compress_at_round` to Settings

**Files:**
- Modify: `app/agent/state.py:10-37`
- Modify: `app/core/config.py:5-23`
- Modify: `app/tasks/review.py:40-54`
- Test: `tests/test_agent_graph.py`

- [ ] **Step 1: Add `compressed` field to ReviewState**

In `app/agent/state.py`, add the `compressed` field after the `traces` line:

```python
    # Agent traces (for persistence)
    traces: list[dict]
    # Context compression
    compressed: bool
```

- [ ] **Step 2: Add `compress_at_round` to Settings**

In `app/core/config.py`, add after the `max_input_tokens` line:

```python
    # Agent loop constraints
    max_rounds: int = 15
    max_input_tokens: int = 60000
    compress_at_round: int = 5
```

- [ ] **Step 3: Add `compressed` to initial state in review task**

In `app/tasks/review.py`, add `"compressed": False` to the `graph.invoke()` dict:

```python
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
        })
```

- [ ] **Step 4: Update test helper `_make_state` to include new fields**

In `tests/test_agent_graph.py`, update the `_make_state` helper:

```python
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
        "traces": [],
        "compressed": False,
    }
    base.update(overrides)
    return base
```

- [ ] **Step 5: Run existing tests to verify nothing breaks**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py -v`
Expected: All 24 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/agent/state.py app/core/config.py app/tasks/review.py tests/test_agent_graph.py
git commit -m "feat: add compressed state field and compress_at_round config"
```

---

### Task 2: Add `COMPRESS_PROMPT` to prompts

**Files:**
- Modify: `app/agent/prompts.py:57`

- [ ] **Step 1: Add COMPRESS_PROMPT**

In `app/agent/prompts.py`, add after the `DEEP_REVIEW_PROMPT` string:

```python
COMPRESS_PROMPT = """\
Summarize the following tool call results collected during a code review.

Preserve ALL of the following in your summary:
- File names and paths mentioned
- Line numbers referenced
- Function/class signatures found
- Risk signals or concerns identified
- Specific findings or issues discovered
- PR metadata (title, author, changed files list)

Output a structured summary organized by topic (PR overview, code changes, findings). \
Be concise but do not drop any actionable detail.
"""
```

- [ ] **Step 2: Commit**

```bash
git add app/agent/prompts.py
git commit -m "feat: add COMPRESS_PROMPT for context compression"
```

---

### Task 3: Implement `compress_router`

**Files:**
- Modify: `app/agent/graph.py`
- Test: `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing tests for compress_router**

In `tests/test_agent_graph.py`, add at the bottom:

```python
# ── compress_router ───────────────────────────────────


class TestCompressRouter:
    def test_needs_compression_at_round_5(self):
        from app.agent.graph import compress_router
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=5,
            compressed=False,
        )
        assert compress_router(state) == "needs_compression"

    def test_no_compression_before_round_5(self):
        from app.agent.graph import compress_router
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=3,
            compressed=False,
        )
        assert compress_router(state) == "no_compression"

    def test_no_compression_if_already_compressed(self):
        from app.agent.graph import compress_router
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=7,
            compressed=True,
        )
        assert compress_router(state) == "no_compression"

    def test_needs_compression_at_round_above_5(self):
        from app.agent.graph import compress_router
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=6,
            compressed=False,
        )
        assert compress_router(state) == "needs_compression"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py::TestCompressRouter -v`
Expected: FAIL with `ImportError: cannot import name 'compress_router'`

- [ ] **Step 3: Implement compress_router**

In `app/agent/graph.py`, add after the `_extract_escalate_reason` function (before `# ── Graph Assembly`):

```python
def compress_router(state: ReviewState) -> str:
    """After tools_router returns 'continue': check if context compression is needed."""
    settings = get_settings()
    if state["round_count"] >= settings.compress_at_round and not state.get("compressed", False):
        return "needs_compression"
    return "no_compression"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py::TestCompressRouter -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: add compress_router for context compression gating"
```

---

### Task 4: Implement `compress_context` node

**Files:**
- Modify: `app/agent/graph.py`
- Test: `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing tests for compress_context**

In `tests/test_agent_graph.py`, add at the bottom:

```python
from unittest.mock import patch, MagicMock


class TestCompressContext:
    def _build_multi_round_state(self):
        """Build a state simulating 5 rounds of tool calls."""
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content="You are a reviewer"),
            HumanMessage(content="Review PR #1"),
        ]
        # Simulate 4 rounds of tool calls
        for i in range(1, 5):
            messages.append(AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": f"file{i}.py"}, "id": str(i)}]))
            messages.append(ToolMessage(content=f"Content of file{i}.py: def func{i}(): pass\n" * 50, tool_call_id=str(i)))
        # Round 5 (most recent — should be preserved)
        messages.append(AIMessage(content="", tool_calls=[{"name": "get_pr_info", "args": {}, "id": "5"}]))
        messages.append(ToolMessage(content="PR #1: Fix auth bug", tool_call_id="5"))
        return _make_state(messages=messages, round_count=5, compressed=False)

    @patch("app.agent.graph._build_scan_llm")
    def test_sets_compressed_flag(self, mock_build_llm):
        from app.agent.graph import compress_context
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary: reviewed 4 files, found no issues"
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = self._build_multi_round_state()
        result = compress_context(state)
        assert result["compressed"] is True

    @patch("app.agent.graph._build_scan_llm")
    def test_reduces_message_count(self, mock_build_llm):
        from app.agent.graph import compress_context
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary: reviewed 4 files, found no issues"
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = self._build_multi_round_state()
        original_count = len(state["messages"])
        result = compress_context(state)
        # Should have fewer messages than original (compressed)
        assert len(result["messages"]) < original_count

    @patch("app.agent.graph._build_scan_llm")
    def test_preserves_recent_round_messages(self, mock_build_llm):
        from app.agent.graph import compress_context
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary: reviewed 4 files"
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = self._build_multi_round_state()
        result = compress_context(state)
        # Last ToolMessage (round 5) should be preserved
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert any("PR #1: Fix auth bug" in m.content for m in tool_msgs)

    @patch("app.agent.graph._build_scan_llm")
    def test_calls_llm_with_tool_results(self, mock_build_llm):
        from app.agent.graph import compress_context
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary of tool results"
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = self._build_multi_round_state()
        compress_context(state)
        # Verify LLM was called
        mock_llm.invoke.assert_called_once()
        # Verify the call included tool result content
        call_args = mock_llm.invoke.call_args[0][0]
        messages_text = " ".join(m.content for m in call_args)
        assert "file1.py" in messages_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py::TestCompressContext -v`
Expected: FAIL with `ImportError: cannot import name 'compress_context'`

- [ ] **Step 3: Implement compress_context**

In `app/agent/graph.py`, add after the `compress_router` function:

```python
def compress_context(state: ReviewState) -> dict:
    """Compress early-round tool results into a structured summary via LLM."""
    from langchain_core.messages import SystemMessage, HumanMessage, RemoveMessage
    from app.agent.prompts import COMPRESS_PROMPT

    llm = _build_scan_llm()

    # Separate messages into categories
    messages = list(state["messages"])

    # Find the boundary: messages from the most recent round (after last AIMessage with tool_calls)
    recent_start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            recent_start = i
            break

    # Collect tool results from earlier rounds (before recent_start)
    early_tool_contents = []
    for msg in messages[:recent_start]:
        if isinstance(msg, ToolMessage) and msg.content:
            early_tool_contents.append(msg.content)

    if not early_tool_contents:
        return {"compressed": True}

    # Call LLM to compress
    tool_results_text = "\n\n---\n\n".join(early_tool_contents)
    response = llm.invoke([
        SystemMessage(content=COMPRESS_PROMPT),
        HumanMessage(content=tool_results_text),
    ])

    summary = response.content or ""
    logger.info("context_compressed", original_parts=len(early_tool_contents), summary_len=len(summary))

    # Build new message list: SystemMessage + HumanMessage + summary + recent round messages
    # Use RemoveMessage to clear old messages, then add new ones
    remove_msgs = [RemoveMessage(id=msg.id) for msg in messages if hasattr(msg, "id") and msg.id]

    # Reconstruct: system prompt + human prompt + compressed summary + recent messages
    new_messages = remove_msgs + [
        SystemMessage(content=state["messages"][0].content if messages and hasattr(messages[0], "content") else ""),
        HumanMessage(content=messages[1].content if len(messages) > 1 and hasattr(messages[1], "content") else ""),
        SystemMessage(content=f"[COMPRESSED CONTEXT FROM ROUNDS 1-{state['round_count'] - 1}]\n\n{summary}"),
    ] + list(messages[recent_start:])

    return {"messages": new_messages, "compressed": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py::TestCompressContext -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run all tests**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: add compress_context node for LLM-based context compression"
```

---

### Task 5: Wire compression into the graph

**Files:**
- Modify: `app/agent/graph.py:261-293`

- [ ] **Step 1: Update `_build_graph` to include compression nodes and edges**

In `app/agent/graph.py`, replace the `_build_graph` function:

```python
def _build_graph() -> StateGraph:
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
        "finish": "parse_result",
        "escalate": "extract_escalation",
    })

    graph.add_edge("extract_escalation", "deep_review")
    graph.add_edge("deep_review", END)
    graph.add_edge("parse_result", END)

    return graph.compile()
```

Wait — the spec says `tools_router` returns `"continue"` and then we check compression. But we can't add a second router after `tools_router` returns `"continue"` because LangGraph conditional edges map to specific nodes. We need to change the `"continue"` target to point to `compress_router` instead of `scan_call`.

Updated approach: change `tools_router`'s `"continue"` edge to point to a `compress_router` conditional edge node. But LangGraph doesn't support conditional edges from conditional edges. Instead, we make `compress_router` a regular node that routes.

Simpler approach: just integrate the compression check directly into `tools_router` or use a wrapper node. The simplest: change `tools_router`'s `"continue"` to go to a new `"check_compression"` node, which is a conditional edge source.

Actually the cleanest approach: change `tools_router` to return 4 values: `"continue"`, `"compress"`, `"finish"`, `"escalate"`. Merge the compression check into `tools_router`.

Replace the `_build_graph` function with:

```python
def _build_graph() -> StateGraph:
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

    return graph.compile()
```

- [ ] **Step 2: Merge `compress_router` logic into `tools_router`**

In `app/agent/graph.py`, update `tools_router` to return `"compress"` instead of `"continue"` when compression is needed. Replace the final `return "continue"` block:

```python
def tools_router(state: ReviewState) -> str:
    """After scan_tools: check for control signals, budget, dead loops, and compression."""
    settings = get_settings()

    # Check for control signals in the latest tool messages
    for msg in reversed(state["messages"]):
        if not isinstance(msg, ToolMessage):
            break
        try:
            data = json.loads(msg.content)
            signal = data.get("signal")
            if signal == FINISH_REVIEW_SIGNAL:
                return "finish"
            if signal == ESCALATE_SIGNAL:
                return "escalate"
        except (json.JSONDecodeError, TypeError):
            continue

    # Check round budget
    if state["round_count"] >= settings.max_rounds:
        logger.warning("agent_max_rounds", rounds=state["round_count"])
        return "finish"

    # Check token budget
    if state["total_input_tokens"] >= settings.max_input_tokens:
        logger.warning("agent_token_budget_exceeded", tokens=state["total_input_tokens"])
        return "finish"

    # Check for dead loop (3 consecutive identical tool calls)
    history = state.get("tool_call_history", [])
    if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
        logger.warning("dead_loop_detected", tool=history[-1])
        return "finish"

    # Check if context compression is needed
    if state["round_count"] >= settings.compress_at_round and not state.get("compressed", False):
        return "compress"

    return "continue"
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/ -v`
Expected: All tests PASS (compress_router tests need updating — see Step 4)

- [ ] **Step 4: Update compress_router tests to test tools_router compress path**

The standalone `compress_router` function is no longer needed (logic merged into `tools_router`). Replace `TestCompressRouter` in `tests/test_agent_graph.py`:

```python
class TestCompressRouter:
    """Test compression routing integrated into tools_router."""

    def test_compress_at_round_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=5,
            compressed=False,
        )
        assert tools_router(state) == "compress"

    def test_no_compress_before_round_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=3,
            compressed=False,
        )
        assert tools_router(state) == "continue"

    def test_no_compress_if_already_compressed(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=7,
            compressed=True,
        )
        assert tools_router(state) == "continue"

    def test_compress_at_round_above_5(self):
        tool_msg = ToolMessage(content="file content", tool_call_id="1")
        state = _make_state(
            messages=[AIMessage(content=""), tool_msg],
            round_count=6,
            compressed=False,
        )
        assert tools_router(state) == "compress"
```

Also remove the standalone `compress_router` function from `app/agent/graph.py` since its logic is now in `tools_router`.

- [ ] **Step 5: Remove import of compress_router from tests**

Remove `from app.agent.graph import compress_router` if present in the test imports. The `TestCompressRouter` tests now use `tools_router` directly (already imported).

- [ ] **Step 6: Run all tests**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: wire compress_context into graph with tools_router compress path"
```

---

### Task 6: Improve deep_review escalation handoff

**Files:**
- Modify: `app/agent/graph.py:140-183`
- Test: `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing test for improved deep_review handoff**

In `tests/test_agent_graph.py`, add at the bottom:

```python
class TestDeepReviewHandoff:
    def test_deep_review_builds_structured_context(self):
        """Verify deep_review extracts structured content from tool messages."""
        from app.agent.graph import deep_review
        from langchain_core.messages import SystemMessage, HumanMessage

        state = _make_state(
            messages=[
                SystemMessage(content="You are a reviewer"),
                HumanMessage(content="Review PR"),
                AIMessage(content="", tool_calls=[{"name": "get_pr_info", "args": {}, "id": "1"}]),
                ToolMessage(content='{"title": "Fix auth", "author": "dev1"}', tool_call_id="1"),
                AIMessage(content="", tool_calls=[{"name": "get_pr_diff", "args": {}, "id": "2"}]),
                ToolMessage(content="diff --git a/auth.py\n+new_code()", tool_call_id="2"),
                AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "auth.py"}, "id": "3"}]),
                ToolMessage(content="def login():\n    return token", tool_call_id="3"),
            ],
            escalate_reason="auth module changed",
            escalated=True,
        )

        with patch("app.agent.graph._build_reason_llm") as mock_build:
            mock_llm = MagicMock()
            mock_response = MagicMock()
            mock_response.content = json.dumps({
                "summary": "Auth module has security issue",
                "comments": [{"filename": "auth.py", "line": 2, "severity": "error", "comment": "Token not validated"}],
            })
            mock_llm.invoke.return_value = mock_response
            mock_build.return_value = mock_llm

            result = deep_review(state)

            # Verify LLM was called with context
            call_args = mock_llm.invoke.call_args[0][0]
            context_text = " ".join(m.content for m in call_args)
            assert "auth" in context_text.lower()
            assert result["risk_level"] == "high"
            assert len(result["comments"]) == 1
```

- [ ] **Step 2: Run test to verify it passes (or fails if refactoring changes behavior)**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/test_agent_graph.py::TestDeepReviewHandoff -v`
Expected: PASS (this test validates current behavior first)

- [ ] **Step 3: Refactor deep_review to build structured handoff**

In `app/agent/graph.py`, replace the `deep_review` function:

```python
def deep_review(state: ReviewState) -> dict:
    """One-shot deep review using the reason scenario (stronger model)."""
    llm = _build_reason_llm()

    # Build structured context from tool messages
    context_sections = []
    for msg in state["messages"]:
        if isinstance(msg, ToolMessage) and msg.content:
            content = msg.content.strip()
            # Truncate individual results to keep handoff under control
            if len(content) > 2000:
                content = content[:2000] + "\n[truncated]"
            context_sections.append(content)

    # Keep last 15 tool results max, prioritizing recent ones
    context_sections = context_sections[-15:]
    context = "\n\n---\n\n".join(context_sections)

    # Truncate total context to ~18K chars (~15-20K tokens target)
    if len(context) > 18000:
        context = context[:18000] + "\n\n[context truncated to fit budget]"

    prompt = DEEP_REVIEW_PROMPT.format(reason=state.get("escalate_reason", "unknown"))

    from langchain_core.messages import SystemMessage, HumanMessage
    response = llm.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ])

    raw = response.content or ""

    # Parse JSON (with markdown fence tolerance)
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    try:
        data = json.loads(text)
        return {
            "risk_level": "high",
            "summary": data.get("summary", ""),
            "comments": data.get("comments", []),
        }
    except json.JSONDecodeError:
        logger.error("deep_review_parse_failed", raw=raw[:500])
        return {
            "risk_level": "high",
            "summary": f"Deep review escalated: {state.get('escalate_reason', '')}. (Parse error)",
            "comments": [],
        }
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_agent_graph.py
git commit -m "feat: improve deep_review handoff with structured context and size limits"
```

---

### Task 7: Update graph docstring and verify full integration

**Files:**
- Modify: `app/agent/graph.py:1-11`

- [ ] **Step 1: Update module docstring**

Replace the docstring at the top of `app/agent/graph.py`:

```python
"""LangGraph StateGraph — ReAct loop with risk-based escalation and context compression.

Graph flow:
  START → scan_call → scan_router
    ├─ has_tool_calls → scan_tools → post_tool_processing → tools_router
    │     ├─ continue       → scan_call (loop)
    │     ├─ compress       → compress_context → scan_call (loop with compressed context)
    │     ├─ finish         → parse_result → END
    │     ├─ escalate       → extract_escalation → deep_review → END
    │     └─ budget/loop    → parse_result → END
    └─ no_tool_calls → parse_result → END
"""
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/JsonY/dev/pr-review-agent/.worktrees/agent-core-p0 && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add app/agent/graph.py
git commit -m "docs: update graph docstring with compression flow"
```
