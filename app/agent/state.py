"""Graph state schema — all data flowing through the review pipeline."""

from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage


class ReviewState(TypedDict):
    """State for the review agent graph.

    messages: LangGraph message list with automatic merging.
    repo, pr_number, ref: PR identity (set once at start, read by tools).
    risk_level, summary, comments: Review output (set by finish_review tool or deep_review node).
    escalated, escalate_reason: Escalation signal (set by escalate tool).
    round_count: Current ReAct loop iteration (for max_rounds guard).
    total_input_tokens: Cumulative input tokens (for budget guard).
    """
    messages: Annotated[list[AnyMessage], add_messages]
    repo: str
    pr_number: int
    ref: str
    # Review output
    risk_level: str
    summary: str
    comments: list[dict]
    # Escalation
    escalated: bool
    escalate_reason: str
    # Loop control
    round_count: int
    total_input_tokens: int
    # Dead loop detection
    tool_call_history: list[str]
    # Agent traces (for persistence)
    traces: list[dict]
    # Context compression
    compressed: bool
    # Re-review context
    prior_comments: list[dict]
    last_reviewed_sha: str
