# Batch F — Graph Resilience

## Goal

Harden the LangGraph agent for production: add PostgreSQL-backed checkpointer for crash recovery, recursion limit as last-resort guard, multi-round compression, and LLM call retry with tenacity.

---

## 1. Checkpointer (PostgresSaver)

### Setup

Add `langgraph-checkpoint-postgres` to dependencies. Use `PostgresSaver` with the existing `DATABASE_URL`.

### Thread ID Strategy

Each graph invocation uses a unique thread ID:

```python
thread_id = f"{repo}:{pr_number}:{ref}"
```

This binds the checkpoint to a specific review of a specific commit. Re-reviews (different `ref`) get their own checkpoint.

### Celery Retry Integration

On Celery retry, the graph resumes from the last checkpoint automatically (LangGraph handles this when the same `thread_id` is used).

On the final retry attempt (`self.request.retries >= 2`), delete the checkpoint and start fresh to avoid repeating a corrupted state.

### Cleanup

After `run_review` completes successfully, delete the checkpoint for that `thread_id`. No stale data accumulates.

### Graph Compile

```python
graph.compile(checkpointer=checkpointer, recursion_limit=100)
```

The checkpointer is created once (module-level singleton) using `PostgresSaver.from_conn_string(settings.database_url)`.

---

## 2. Recursion Limit

Set `recursion_limit=100` in `graph.compile()`.

Normal execution: 15 rounds × ~4 nodes = ~60 node executions. 100 provides safe headroom.

When triggered, LangGraph raises `GraphRecursionError`. Catch in `run_review` and produce a degraded result:

```python
from langgraph.errors import GraphRecursionError

try:
    result = graph.invoke(state, config=config)
except GraphRecursionError:
    result = {
        "risk_level": "low",
        "summary": "Review terminated: graph recursion limit reached.",
        "comments": [], "escalated": False, "traces": [],
    }
```

---

## 3. Build Graph Refactor

Replace the `global _compiled_graph` pattern with `@lru_cache`:

```python
@lru_cache(maxsize=1)
def build_review_graph():
    return _build_graph()
```

The checkpointer is passed into `_build_graph()`, created from a module-level factory.

---

## 4. Multi-Round Compression

### State Change

Replace `compressed: bool` with `compress_count: int` (default `0`).

### Router Change

In `tools_router`, trigger compression when:

```python
compress_count = state.get("compress_count", 0)
next_compress_at = settings.compress_at_round * (compress_count + 1)
if state["round_count"] >= next_compress_at and not already_compressing:
    return "compress"
```

This triggers compression at round 5, 10, etc. With max_rounds=15, at most 2 compressions per review.

### compress_context Change

Return `compress_count` instead of `compressed`:

```python
return {"messages": new_messages, "compress_count": state.get("compress_count", 0) + 1}
```

### Backwards Compatibility

Existing tests that use `compressed: bool` must be updated to `compress_count: int`.

---

## 5. LLM Call Retry (tenacity)

### Retry Wrapper

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APITimeoutError, RateLimitError

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError)),
)
def _invoke_llm(llm, messages):
    return llm.invoke(messages)
```

### Usage

Replace `llm.invoke(messages)` with `_invoke_llm(llm, messages)` in:
- `scan_call` (main loop LLM call)
- `deep_review` (escalation LLM call)
- `compress_context` (compression LLM call)

### Retry Scope

- Retry: `APITimeoutError`, `RateLimitError` (transient)
- Do NOT retry: 4xx client errors, auth failures, non-API errors
- 3 attempts with exponential backoff: 4s → 8s → 16s

### Retry Budget

Total worst case: 3 (tenacity) × 3 (Celery) = 9 LLM call attempts. Acceptable because tenacity retries are fast (seconds) and Celery retries include checkpoint recovery.

---

## 6. File Structure

| File | Change |
|---|---|
| `requirements.txt` | **MODIFY** — add `langgraph-checkpoint-postgres`, `tenacity` |
| `app/agent/graph.py` | **MODIFY** — checkpointer, lru_cache, recursion_limit, _invoke_llm, compress_count |
| `app/agent/state.py` | **MODIFY** — `compressed: bool` → `compress_count: int` |
| `app/tasks/review.py` | **MODIFY** — thread_id config, GraphRecursionError catch, checkpoint cleanup |
| `tests/test_agent_graph.py` | **MODIFY** — update compressed→compress_count, add recursion/retry tests |
| `tests/test_review_task.py` | **MODIFY** — add checkpoint recovery and recursion error tests |

---

## 7. Testing Strategy

- `tools_router` with `compress_count`: triggers at round 5, 10; does not trigger at round 6 if already compressed once
- `compress_context`: returns incremented `compress_count`
- `_invoke_llm`: retries on `APITimeoutError`, does not retry on `ValueError`
- `run_review` with `GraphRecursionError`: produces degraded result, does not crash
- `build_review_graph`: returns same instance on repeated calls (lru_cache)
- Checkpointer integration: manual validation (not unit tested — requires live PG)

---

## 8. Scope Exclusions

- Checkpointer table migration (PostgresSaver auto-creates its tables via `setup()`)
- Async graph execution (`ainvoke`) — current sync model is fine for Celery workers
- Circuit breaker pattern — tenacity retry is sufficient for current scale
- Checkpoint TTL / cron cleanup — manual cleanup in run_review is sufficient
