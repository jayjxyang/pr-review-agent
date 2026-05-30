"""Tests for agent graph routing, parsing, and control tools."""

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.services.tools.control import (
    finish_review,
    escalate,
    FINISH_REVIEW_SIGNAL,
    ESCALATE_SIGNAL,
)
from app.agent.graph import scan_router, tools_router, parse_result, _extract_escalate_reason, post_tool_processing


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
