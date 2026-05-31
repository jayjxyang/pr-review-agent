"""Tests for agent graph routing, parsing, and control tools."""

import json
import pytest
from unittest.mock import patch, MagicMock

from langchain_core.messages import AIMessage, ToolMessage

from app.services.tools.control import (
    finish_review,
    escalate,
    FINISH_REVIEW_SIGNAL,
    ESCALATE_SIGNAL,
)
from app.agent.graph import scan_router, tools_router, parse_result, _extract_escalate_reason, post_tool_processing, scan_call


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
        "traces": [],
        "compress_count": 0,
        "prior_comments": [],
        "last_reviewed_sha": "",
        "repo_config": {},
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


# ── Compression routing in tools_router ────────────────


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


# ── compress_context ───────────────────────────────────


class TestCompressContext:
    def _build_multi_round_state(self):
        """Build a state simulating 5 rounds of tool calls.

        Note: SystemMessage and HumanMessage are injected inline by scan_call,
        NOT stored in state. State messages start with AIMessage responses.
        """
        messages = []
        # Simulate 4 rounds of tool calls (early — will be compressed)
        for i in range(1, 5):
            messages.append(AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": f"file{i}.py"}, "id": str(i)}]))
            messages.append(ToolMessage(content=f"Content of file{i}.py: def func{i}(): pass\n" * 50, tool_call_id=str(i)))
        # Round 5 (most recent — should be preserved)
        messages.append(AIMessage(content="", tool_calls=[{"name": "get_pr_info", "args": {}, "id": "5"}]))
        messages.append(ToolMessage(content="PR #1: Fix auth bug", tool_call_id="5"))
        return _make_state(messages=messages, round_count=5, compress_count=0)

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
        assert result["compress_count"] == 1

    @patch("app.agent.graph._build_scan_llm")
    def test_reduces_message_count(self, mock_build_llm):
        from app.agent.graph import compress_context
        from langchain_core.messages import RemoveMessage
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary: reviewed 4 files, found no issues"
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = self._build_multi_round_state()
        original_count = len(state["messages"])
        result = compress_context(state)
        # Count non-RemoveMessage messages (the actual content)
        content_msgs = [m for m in result["messages"] if not isinstance(m, RemoveMessage)]
        # Should be: SystemMessage + HumanMessage(with summary) + 2 recent = 4
        # Original was 10 (5 rounds * 2 messages each)
        assert len(content_msgs) < original_count
        assert len(content_msgs) == 4  # sys + human(+summary) + recent AI + recent Tool

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
        # Last ToolMessage (round 5, "PR #1: Fix auth bug") should be in result
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

    @patch("app.agent.graph._build_scan_llm")
    def test_no_early_tools_skips_compression(self, mock_build_llm):
        from app.agent.graph import compress_context
        from langchain_core.messages import SystemMessage, HumanMessage
        # State with only recent round messages (no early tool results)
        messages = [
            SystemMessage(content="You are a reviewer"),
            HumanMessage(content="Review PR #1"),
            AIMessage(content="", tool_calls=[{"name": "get_pr_info", "args": {}, "id": "1"}]),
            ToolMessage(content="PR info here", tool_call_id="1"),
        ]
        state = _make_state(messages=messages, round_count=1, compress_count=0)
        result = compress_context(state)
        assert result["compress_count"] == 1
        assert "messages" not in result  # No message replacement needed
        mock_build_llm.return_value.invoke.assert_not_called()


# ── deep_review handoff ────────────────────────────────


class TestDeepReviewHandoff:
    @patch("app.agent.graph._build_reason_llm")
    def test_truncates_long_tool_results(self, mock_build_llm):
        from app.agent.graph import deep_review
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "summary": "Found issue",
            "comments": [],
        })
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        # Create a state with a very long tool message
        long_content = "x" * 5000
        state = _make_state(
            messages=[
                AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "1"}]),
                ToolMessage(content=long_content, tool_call_id="1"),
            ],
            escalate_reason="large change",
            escalated=True,
        )
        deep_review(state)

        # Verify the context sent to LLM was truncated
        call_args = mock_llm.invoke.call_args[0][0]
        from langchain_core.messages import HumanMessage
        human_msg = [m for m in call_args if isinstance(m, HumanMessage)][0]
        assert len(human_msg.content) <= 2100  # 2000 + "[truncated]" + margin
        assert "[truncated]" in human_msg.content

    @patch("app.agent.graph._build_reason_llm")
    def test_returns_high_risk_result(self, mock_build_llm):
        from app.agent.graph import deep_review
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "summary": "Critical auth issue",
            "comments": [{"filename": "auth.py", "line": 10, "severity": "error", "comment": "Token leak"}],
        })
        mock_llm.invoke.return_value = mock_response
        mock_build_llm.return_value = mock_llm

        state = _make_state(
            messages=[
                AIMessage(content="", tool_calls=[{"name": "get_pr_diff", "args": {}, "id": "1"}]),
                ToolMessage(content="diff content", tool_call_id="1"),
            ],
            escalate_reason="auth module",
            escalated=True,
        )
        result = deep_review(state)
        assert result["risk_level"] == "high"
        assert result["summary"] == "Critical auth issue"
        assert len(result["comments"]) == 1


# ── scan_call re-review injection ─────────────────────


class TestReReviewInjection:
    def test_scan_call_injects_re_review_addendum(self, monkeypatch):
        """When prior_comments is non-empty, system prompt includes re-review addendum."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state(
            prior_comments=[
                {"id": 1, "filename": "a.py", "line": 10, "severity": "warning", "comment": "Missing check"},
            ],
            last_reviewed_sha="old123",
        )

        scan_call(state)

        system_content = captured["messages"][0].content
        assert "Re-review Context" in system_content
        assert "old123" in system_content
        assert "Missing check" in system_content

    def test_scan_call_no_addendum_on_first_review(self, monkeypatch):
        """When prior_comments is empty, system prompt has no re-review addendum."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state()  # prior_comments=[] by default

        scan_call(state)

        system_content = captured["messages"][0].content
        assert "Re-review Context" not in system_content


# ── Tech stack prompt injection ──────────────────────


class TestTechStackInjection:
    def test_scan_call_injects_tech_stack(self, monkeypatch):
        """When repo_config has tech_stack, it's injected into the system prompt."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state(
            repo_config={
                "tech_stack": {
                    "language": "python",
                    "framework": "fastapi",
                    "testing": "pytest",
                }
            }
        )

        scan_call(state)

        system_msg = captured["messages"][0]
        assert "## Project Tech Stack" in system_msg.content
        assert "Language: python" in system_msg.content
        assert "Framework: fastapi" in system_msg.content
        assert "Testing: pytest" in system_msg.content

    def test_scan_call_no_tech_stack_without_config(self, monkeypatch):
        """Without repo_config tech_stack, no tech stack section in prompt."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state(repo_config={})

        scan_call(state)

        system_msg = captured["messages"][0]
        assert "## Project Tech Stack" not in system_msg.content


# ── Ignore paths prompt injection ────────────────────


class TestIgnorePathsInjection:
    def test_scan_call_injects_ignore_paths(self, monkeypatch):
        """When repo_config has ignore_paths, they're injected into the system prompt."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state(
            repo_config={"ignore_paths": ["generated/**", "docs/**"]}
        )

        scan_call(state)

        system_msg = captured["messages"][0]
        assert "## Ignored Paths" in system_msg.content
        assert "generated/**" in system_msg.content
        assert "docs/**" in system_msg.content

    def test_scan_call_no_ignore_paths_without_config(self, monkeypatch):
        """Without ignore_paths, no ignored paths section in prompt."""
        captured = {}

        def mock_invoke(messages, **kwargs):
            captured["messages"] = messages
            resp = AIMessage(content="Reviewing...")
            resp.usage_metadata = {"input_tokens": 100}
            return resp

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value.invoke = mock_invoke
        monkeypatch.setattr("app.agent.graph._build_scan_llm", lambda: mock_llm)

        state = _make_state(repo_config={})

        scan_call(state)

        system_msg = captured["messages"][0]
        assert "## Ignored Paths" not in system_msg.content


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
