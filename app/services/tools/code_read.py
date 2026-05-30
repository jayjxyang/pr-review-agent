"""Code reading tools — read files and search code via GitHub API."""

import base64

from github import Github
from langchain_core.tools import tool

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_LINES = 300
_MAX_SEARCH_RESULTS = 20


def _github_client() -> Github:
    return Github(get_settings().github_app_token)


@tool
def read_file(repo: str, path: str, ref: str, start_line: int = None, end_line: int = None) -> str:
    """Read a file from the repository. Supports optional line range. Returns file content with line numbers."""
    gh = _github_client()
    try:
        content_file = gh.get_repo(repo).get_contents(path, ref=ref)
    except Exception as e:
        return f"Error: could not read {path}: {e}"

    if isinstance(content_file, list):
        return f"Error: {path} is a directory"

    raw = base64.b64decode(content_file.content).decode("utf-8", errors="replace")
    lines = raw.splitlines()

    if start_line or end_line:
        start = max(0, (start_line or 1) - 1)
        end = min(len(lines), end_line or len(lines))
        lines = lines[start:end]
    elif len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        lines.append(f"\n[truncated — showing first {_MAX_LINES} of {len(raw.splitlines())} lines]")

    return "\n".join(f"{i + (start_line or 1)}| {line}" for i, line in enumerate(lines))


@tool
def search_code(repo: str, query: str, path_filter: str = None) -> str:
    """Search for code in the repository using a keyword. Returns matching files with scores."""
    gh = _github_client()
    q = f"{query} repo:{repo}"
    if path_filter:
        q += f" path:{path_filter}"

    try:
        results = gh.search_code(q)
    except Exception as e:
        return f"Error searching: {e}"

    output = []
    for i, item in enumerate(results):
        if i >= _MAX_SEARCH_RESULTS:
            output.append(f"\n[showing first {_MAX_SEARCH_RESULTS} results]")
            break
        output.append(f"- {item.path} (score: {item.score:.0f})")

    if not output:
        return "No results found."
    return "\n".join(output)


@tool
def find_references(repo: str, symbol: str, path_filter: str = None) -> str:
    """Find all files that reference a given symbol (function, class, variable name)."""
    return search_code.invoke({"repo": repo, "query": symbol, "path_filter": path_filter})
