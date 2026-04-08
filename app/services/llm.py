import json
from dataclasses import dataclass, field

from litellm import completion

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.chunker import DiffChunk

logger = get_logger(__name__)

SYSTEM_PROMPT = """\
You are an expert code reviewer. Analyze the PR diff and return ONLY a JSON object \
with this exact structure — no markdown fences, no extra text:

{
  "summary": "<one paragraph: overall quality and key concerns>",
  "reviews": [
    {
      "filename": "<path/to/file>",
      "line": <integer — line number in the NEW version of the file>,
      "severity": "<error|warning|suggestion>",
      "comment": "<concise, actionable comment>"
    }
  ]
}

Rules:
- "line" must reference a line that appears in the diff \
(lines prefixed with + or unchanged context lines).
- severity meanings — "error": bugs / security issues / crashes; \
"warning": code smells / bad practices; "suggestion": improvements / readability.
- Be concise. Skip trivial style nits unless they are significant.
- If the diff looks correct, return an empty "reviews" array.
- Return valid JSON only.\
"""


@dataclass(frozen=True)
class ReviewComment:
    filename: str
    line: int
    severity: str  # "error" | "warning" | "suggestion"
    comment: str


@dataclass
class ReviewResult:
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)


def call_llm(chunk: DiffChunk) -> ReviewResult:
    """Send one diff chunk to the LLM and return a parsed ReviewResult."""
    settings = get_settings()

    kwargs: dict = dict(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": chunk.format()},
        ],
    )
    if settings.ai_gateway_url:
        kwargs["api_base"] = settings.ai_gateway_url

    response = completion(**kwargs)
    raw: str = response.choices[0].message.content

    logger.info("llm_response_received", tokens_used=response.usage.total_tokens if response.usage else None)

    return _parse_response(raw)


def _parse_response(raw: str) -> ReviewResult:
    """Parse the LLM JSON response, tolerating accidental markdown fences."""
    text = raw.strip()

    # Strip ```json … ``` or ``` … ``` if the model ignored the prompt instruction
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    data = json.loads(text)

    comments = [
        ReviewComment(
            filename=c["filename"],
            line=int(c["line"]),
            severity=c.get("severity", "suggestion"),
            comment=c["comment"],
        )
        for c in data.get("reviews", [])
    ]

    return ReviewResult(summary=data.get("summary", ""), comments=comments)
