"""Knowledge tools — read project-specific review rules."""

import base64

from github import Github
from langchain_core.tools import tool

from app.core.config import get_settings

_RULES_DIR = ".ai-review/rules"


def _github_client() -> Github:
    return Github(get_settings().github_app_token)


@tool
def read_repo_rules(repo: str, ref: str) -> str:
    """Read the project's AI review rules from .ai-review/rules/ directory. Returns all rule files concatenated."""
    gh = _github_client()
    try:
        contents = gh.get_repo(repo).get_contents(_RULES_DIR, ref=ref)
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
