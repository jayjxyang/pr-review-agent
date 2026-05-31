"""Control tools — signal loop termination or escalation.

These tools return JSON with a signal field. The graph's tools_router
checks for these signals to decide: continue looping, finish, or escalate.
"""

import json

from langchain_core.tools import tool

FINISH_REVIEW_SIGNAL = "__FINISH_REVIEW__"
ESCALATE_SIGNAL = "__ESCALATE__"


@tool
def finish_review(risk_level: str, summary: str, comments: list) -> str:
    """End the review and output findings. Call when you have gathered enough context.

    Args:
        risk_level: Overall risk — "low", "medium", or "high".
        summary: One paragraph summary of the review findings.
        comments: List of {"filename": str, "line": int, "severity": "error"|"warning"|"suggestion", "comment": str}.
    """
    return json.dumps({
        "signal": FINISH_REVIEW_SIGNAL,
        "risk_level": risk_level,
        "summary": summary,
        "comments": comments,
    })


@tool
def escalate(reason: str) -> str:
    """Escalate this PR to deep analysis mode. Call when you detect high-risk changes.

    Args:
        reason: Why this PR needs deep analysis.
    """
    return json.dumps({
        "signal": ESCALATE_SIGNAL,
        "reason": reason,
    })
