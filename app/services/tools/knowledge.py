"""Knowledge tools — read project-specific review rules."""

import base64

from langchain_core.tools import tool

from app.services.github import _github_client

_RULES_DIR = ".ai-review/rules"


@tool
def read_repo_rules(repo: str, ref: str) -> str:
    """Read the project's AI review rules from .ai-review/rules/ directory. Returns all rule files concatenated."""
    try:
        contents = _github_client().get_repo(repo).get_contents(_RULES_DIR, ref=ref)
    except Exception:
        return "No .ai-review/rules/ directory found in this repository."

    if not isinstance(contents, list):
        return "No rule files found."

    output = []
    for item in contents:
        if item.type == "file" and item.name.endswith(".md"):
            raw = base64.b64decode(item.content).decode("utf-8", errors="replace")
            output.append(f"## {item.name}\n\n{raw}")

    return "\n\n---\n\n".join(output) if output else "No rule files found."
