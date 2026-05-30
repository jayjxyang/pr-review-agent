# Batch A — P0 Hardening: Tests, Dead Loop Detection, Caching

## Goal

Make the P0 agent production-ready: add automated tests for core routing logic, implement dead loop detection, and cache expensive objects.

---

## 1. Automated Tests

### Scope

Unit test the pure-function routing and parsing logic in the agent graph. No LLM mocking needed — these functions operate on state dicts and message objects.

### Test Cases

**`scan_router`**
- AIMessage with `tool_calls` → returns `"has_tool_calls"`
- AIMessage without `tool_calls` → returns `"no_tool_calls"`

**`tools_router`**
- ToolMessage with `FINISH_REVIEW_SIGNAL` → returns `"finish"`
- ToolMessage with `ESCALATE_SIGNAL` → returns `"escalate"`
- `round_count >= max_rounds` → returns `"finish"`
- `total_input_tokens >= max_input_tokens` → returns `"finish"`
- Normal state (no signals, within budget) → returns `"continue"`
- Dead loop detected (3 consecutive identical tool calls) → returns `"finish"` *(after Task 2)*

**`parse_result`**
- State with finish_review ToolMessage → extracts `risk_level`, `summary`, `comments`
- State with no finish signal → returns default (`"low"`, partial summary, `[]`)
- State with malformed JSON ToolMessage → returns default

**`_extract_escalate_reason`**
- State with escalate ToolMessage → extracts `reason`, sets `escalated=True`
- State with no escalate signal → returns `escalated=True, escalate_reason="unknown"`

**Control tools**
- `finish_review(...)` → returns JSON with `FINISH_REVIEW_SIGNAL`
- `escalate(...)` → returns JSON with `ESCALATE_SIGNAL`

### Implementation

- Framework: pytest
- Test file: `tests/test_agent_graph.py`
- Construct fake `ReviewState` dicts with `langchain_core.messages.AIMessage` / `ToolMessage` objects
- No external dependencies (no GitHub API, no LLM, no Redis)

---

## 2. Dead Loop Detection

### Mechanism

Add a `tool_call_history` field to `ReviewState` that records a fingerprint of each tool call. After `scan_tools` executes, `tools_router` checks if the last 3 fingerprints are identical.

### State Change

```python
# In ReviewState (state.py)
tool_call_history: list[str]  # ["tool_name:params_hash", ...]
```

### Fingerprint Format

`f"{tool_name}:{hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]}"`

### Recording

Add a new node `record_tool_calls` between `scan_tools` and `tools_router`, or integrate into `scan_tools` output processing. Since `ToolNode` is a prebuilt node we can't modify, add a wrapper node `post_tool_processing` that:
1. Reads the latest AIMessage (before tool execution) to extract tool call names/params
2. Appends fingerprints to `tool_call_history`
3. Returns updated state

Graph flow changes:
```
scan_call → scan_router → scan_tools → post_tool_processing → tools_router
```

### Detection in `tools_router`

```python
history = state.get("tool_call_history", [])
if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
    logger.warning("dead_loop_detected", tool=history[-1])
    return "finish"
```

---

## 3. Graph/LLM Client Caching

### Graph Caching

Cache the compiled graph at module level in `graph.py`:

```python
_compiled_graph = None

def build_review_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph
```

Rename current `build_review_graph` → `_build_graph` (internal builder).

### LLM Client Caching

Apply `@lru_cache` to `_build_scan_llm()` and `_build_reason_llm()`. Since `Settings` is already cached via `@lru_cache` on `get_settings()`, the LLM params are stable per process.

```python
@lru_cache(maxsize=1)
def _build_scan_llm() -> ChatOpenAI:
    ...

@lru_cache(maxsize=1)
def _build_reason_llm() -> ChatOpenAI:
    ...
```

---

## Files Affected

| File | Change |
|---|---|
| `tests/test_agent_graph.py` | **NEW** — Unit tests for routing/parsing |
| `app/agent/state.py` | **MODIFY** — Add `tool_call_history` field |
| `app/agent/graph.py` | **MODIFY** — Add `post_tool_processing` node, dead loop detection in `tools_router`, graph/LLM caching |
