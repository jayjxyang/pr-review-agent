# Batch C — Context Compression

## Goal

Prevent token budget blowup in long agent loops by compressing early-round tool results into a structured summary at round 5. Also improve the deep_review escalation handoff by using the same compression approach instead of raw ToolMessage concatenation.

---

## 1. Problem

Currently `scan_call` sends the full `state["messages"]` to the LLM every round. Each tool call adds ~2-5K tokens of raw tool output. By round 8-10, the accumulated context reaches 30-50K tokens, leaving little budget for actual reasoning and pushing against the 60K token limit.

---

## 2. Compression Mechanism

### When

At the transition from `tools_router → continue`, check if `round_count >= compress_at_round` (default 5) AND `compressed == False`. If both true, route through `compress_context` node before returning to `scan_call`.

### How

1. Extract all ToolMessage content from `state["messages"]`
2. Call scan LLM (Flash) with a compression prompt asking for a structured summary
3. Replace the message history with: original SystemMessage + HumanMessage + a new SystemMessage containing the summary + the most recent round's raw tool results (preserved for continuity)
4. Set `compressed = True` to prevent re-triggering

### Compression Prompt

```
Summarize the following tool call results collected during a code review.

Preserve ALL of the following in your summary:
- File names and paths mentioned
- Line numbers referenced
- Function/class signatures found
- Risk signals or concerns identified
- Specific findings or issues discovered
- PR metadata (title, author, changed files list)

Output a structured summary. Be concise but do not drop any actionable detail.
```

### Expected Token Savings

- Typical rounds 1-4 tool output: ~15-20K tokens
- Compressed summary: ~2-3K tokens
- Net savings per subsequent round: ~12-17K input tokens
- Cost of compression call: ~2-3K input tokens (one-time)

---

## 3. Deep Review Handoff Improvement

Current `deep_review` node concatenates the last 10 raw ToolMessage contents. Replace this with:

1. Use the same compression approach to summarize all collected tool results
2. Include the PR diff and escalation reason
3. Target payload size: 15-20K tokens (per V2 spec)

This is a code-level compression (not an LLM call) for the handoff — extract structured fields from tool results and format them, since `deep_review` already makes its own LLM call.

---

## 4. State Changes

Add to `ReviewState`:

| Field | Type | Purpose |
|---|---|---|
| `compressed` | `bool` | Flag to prevent re-triggering compression |

---

## 5. Config Changes

Add to `Settings`:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `compress_at_round` | `int` | `5` | Round number at which to trigger compression |

---

## 6. Graph Changes

Updated flow:

```
scan_call → scan_router
  ├─ has_tool_calls → scan_tools → post_tool_processing → tools_router
  │     ├─ finish       → parse_result → END
  │     ├─ escalate     → extract_escalation → deep_review → END
  │     └─ continue     → compress_router
  │                         ├─ needs_compression → compress_context → scan_call
  │                         └─ no_compression    → scan_call
  └─ no_tool_calls → parse_result → END
```

New components:
- `compress_router(state)` — returns `"needs_compression"` if `round_count >= compress_at_round and not compressed`, else `"no_compression"`
- `compress_context(state)` — performs LLM-based compression, returns updated messages + `compressed = True`

---

## 7. File Structure

| File | Change |
|---|---|
| `app/agent/state.py` | **MODIFY** — Add `compressed: bool` field |
| `app/agent/prompts.py` | **MODIFY** — Add `COMPRESS_PROMPT` |
| `app/core/config.py` | **MODIFY** — Add `compress_at_round` setting |
| `app/agent/graph.py` | **MODIFY** — Add `compress_context` node, `compress_router` function, update graph edges |
| `tests/test_agent_graph.py` | **MODIFY** — Add compression tests |

---

## 8. Testing

- `compress_router` returns `"needs_compression"` when `round_count >= 5` and `compressed == False`
- `compress_router` returns `"no_compression"` when `round_count < 5`
- `compress_router` returns `"no_compression"` when `compressed == True` (already compressed)
- `compress_context` sets `compressed = True` in returned state
- `compress_context` replaces messages with summary + recent round's messages
- Compression only triggers once per review (flag prevents re-trigger)
- `deep_review` produces structured handoff instead of raw concatenation
