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

import json
import hashlib
from functools import lru_cache

from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception_type
from openai import APITimeoutError, RateLimitError
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres import PostgresSaver

from app.core.config import get_settings
from app.core.logging import get_logger
from app.agent.state import ReviewState
from app.agent.prompts import SCAN_SYSTEM_PROMPT, DEEP_REVIEW_PROMPT
from app.services.tools import ALL_TOOLS
from app.services.tools.control import FINISH_REVIEW_SIGNAL, ESCALATE_SIGNAL

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _build_scan_llm() -> ChatOpenAI:
    """Create ChatOpenAI pointing at the gateway's scan scenario."""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.scan_scenario,
        base_url=settings.ai_gateway_url,
        api_key=settings.ai_gateway_key,
        temperature=0,
    )


@lru_cache(maxsize=1)
def _build_reason_llm() -> ChatOpenAI:
    """Create ChatOpenAI for the reason scenario (deep review)."""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.reason_scenario,
        base_url=settings.ai_gateway_url,
        api_key=settings.ai_gateway_key,
        temperature=0,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_none(),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError)),
    reraise=True,
)
def _invoke_llm(llm, messages):
    """Invoke LLM with retry on transient API errors."""
    return llm.invoke(messages)


# ── Nodes ──────────────────────────────────────────────


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

        # Inject per-repo config into prompt
        repo_config = state.get("repo_config", {})
        ignore_paths = repo_config.get("ignore_paths", [])
        if ignore_paths:
            system_prompt += "\n\n## Ignored Paths\nDo NOT review files matching these patterns — they are excluded by repo config:\n"
            system_prompt += "\n".join(f"- `{p}`" for p in ignore_paths)

        tech_stack = repo_config.get("tech_stack", {})
        if tech_stack:
            tech_lines = []
            for key, value in tech_stack.items():
                tech_lines.append(f"- {key.title()}: {value}")
            system_prompt += "\n\n## Project Tech Stack\n" + "\n".join(tech_lines)

        human_content = f"Review PR #{state['pr_number']} in repository {state['repo']} (branch ref: {state['ref']})."

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content),
        ] + messages

    response = _invoke_llm(llm, messages)
    logger.info("scan_call", round=state["round_count"] + 1)

    token_usage = response.usage_metadata or {}
    input_tokens = token_usage.get("input_tokens", 0)

    return {
        "messages": [response],
        "round_count": state["round_count"] + 1,
        "total_input_tokens": state["total_input_tokens"] + input_tokens,
    }


def parse_result(state: ReviewState) -> dict:
    """Extract review result from the last finish_review tool message, or force a partial result."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content)
                if data.get("signal") == FINISH_REVIEW_SIGNAL:
                    return {
                        "risk_level": data.get("risk_level", "low"),
                        "summary": data.get("summary", ""),
                        "comments": data.get("comments", []),
                    }
            except (json.JSONDecodeError, TypeError):
                continue

    # No finish signal found — force partial result
    return {
        "risk_level": "low",
        "summary": "Review terminated early (budget/round limit). Partial analysis based on collected context.",
        "comments": [],
    }


def post_tool_processing(state: ReviewState) -> dict:
    """Record tool call fingerprints for dead loop detection and collect traces."""
    history = list(state.get("tool_call_history", []))
    traces = list(state.get("traces", []))

    # Find the last AIMessage with tool_calls (the one that triggered scan_tools)
    for msg in reversed(state["messages"]):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                params = tc.get("args", {})
                params_str = json.dumps(params, sort_keys=True)
                fingerprint = f"{tc['name']}:{hashlib.md5(params_str.encode()).hexdigest()[:8]}"
                history.append(fingerprint)

                # Find matching ToolMessage result for this tool call
                result_summary = ""
                for tmsg in state["messages"]:
                    if isinstance(tmsg, ToolMessage) and tmsg.tool_call_id == tc.get("id"):
                        result_summary = (tmsg.content or "")[:500]
                        break

                traces.append({
                    "round_number": state["round_count"],
                    "tool_name": tc["name"],
                    "tool_params": params,
                    "tool_result_summary": result_summary,
                })
            break

    return {"tool_call_history": history, "traces": traces}


def deep_review(state: ReviewState) -> dict:
    """One-shot deep review using the reason scenario (stronger model)."""
    llm = _build_reason_llm()

    # Build structured context from tool messages
    context_sections = []
    for msg in state["messages"]:
        if isinstance(msg, ToolMessage) and msg.content:
            content = msg.content.strip()
            if len(content) > 2000:
                content = content[:2000] + "\n[truncated]"
            context_sections.append(content)

    # Keep last 15 tool results, prioritizing recent ones
    context_sections = context_sections[-15:]
    context = "\n\n---\n\n".join(context_sections)

    # Truncate total context to ~18K chars
    if len(context) > 18000:
        context = context[:18000] + "\n\n[context truncated to fit budget]"

    prompt = DEEP_REVIEW_PROMPT.format(reason=state.get("escalate_reason", "unknown"))

    from langchain_core.messages import SystemMessage, HumanMessage
    response = _invoke_llm(llm, [
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


# ── Routers (conditional edges) ───────────────────────


def scan_router(state: ReviewState) -> str:
    """After scan_call: route based on whether LLM made tool calls."""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "has_tool_calls"
    return "no_tool_calls"


def tools_router(state: ReviewState) -> str:
    """After scan_tools: check for control signals, budget, and dead loops."""
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
                # Store escalation reason in state (via parse step)
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

    # Check if context compression is needed (triggers at round 5, 10, etc.)
    compress_count = state.get("compress_count", 0)
    next_compress_at = settings.compress_at_round * (compress_count + 1)
    if state["round_count"] >= next_compress_at:
        return "compress"

    return "continue"


def _extract_escalate_reason(state: ReviewState) -> dict:
    """Intermediate node: extract escalation reason from tool messages before deep_review."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content)
                if data.get("signal") == ESCALATE_SIGNAL:
                    return {"escalated": True, "escalate_reason": data.get("reason", "")}
            except (json.JSONDecodeError, TypeError):
                continue
    return {"escalated": True, "escalate_reason": "unknown"}


def compress_context(state: ReviewState) -> dict:
    """Compress early-round tool results into a structured summary via LLM."""
    from langchain_core.messages import SystemMessage, HumanMessage, RemoveMessage
    from app.agent.prompts import COMPRESS_PROMPT

    llm = _build_scan_llm()
    messages = list(state["messages"])

    # Find boundary: last AIMessage with tool_calls marks start of recent round
    recent_start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if hasattr(messages[i], "tool_calls") and messages[i].tool_calls:
            recent_start = i
            break

    # Collect tool results from earlier rounds
    early_tool_contents = []
    for msg in messages[:recent_start]:
        if isinstance(msg, ToolMessage) and msg.content:
            early_tool_contents.append(msg.content)

    if not early_tool_contents:
        return {"compress_count": state.get("compress_count", 0) + 1}

    # Call LLM to compress (cap input to prevent blowing context window)
    tool_results_text = "\n\n---\n\n".join(early_tool_contents)
    if len(tool_results_text) > 30000:
        tool_results_text = tool_results_text[:30000] + "\n\n[input truncated for compression]"

    response = _invoke_llm(llm, [
        SystemMessage(content=COMPRESS_PROMPT),
        HumanMessage(content=tool_results_text),
    ])

    summary = response.content or ""
    logger.info("context_compressed", original_parts=len(early_tool_contents), summary_len=len(summary))

    # Build new message list using RemoveMessage to clear old, then add new
    remove_msgs = [RemoveMessage(id=msg.id) for msg in messages if hasattr(msg, "id") and msg.id]

    # Reconstruct: system prompt + human prompt with compressed context + recent round
    # Use HumanMessage for summary (not SystemMessage) to maintain valid role alternation
    compressed_human = (
        f"Review PR #{state['pr_number']} in repository {state['repo']} (branch ref: {state['ref']}).\n\n"
        f"[COMPRESSED CONTEXT FROM ROUNDS 1-{state['round_count']}]\n\n{summary}"
    )
    new_messages = remove_msgs + [
        SystemMessage(content=SCAN_SYSTEM_PROMPT),
        HumanMessage(content=compressed_human),
    ] + list(messages[recent_start:])

    return {"messages": new_messages, "compress_count": state.get("compress_count", 0) + 1}


# ── Graph Assembly ─────────────────────────────────────


def _get_checkpointer():
    """Create a PostgresSaver checkpointer using the app's database URL.

    Uses from_conn_string context manager, entering it to keep connection alive
    for the lifetime of the worker process.
    """
    settings = get_settings()
    cm = PostgresSaver.from_conn_string(settings.database_url)
    saver = cm.__enter__()
    saver.setup()
    return saver


@lru_cache(maxsize=1)
def build_review_graph():
    """Return the cached compiled review graph (built once per process)."""
    return _build_graph()


def _build_graph():
    """Build and compile the review agent graph with checkpointer."""
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
