"""Code reading tools — read files and search code via GitHub API."""

import base64

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import _github_client

logger = get_logger(__name__)

_MAX_LINES = 300
_MAX_SEARCH_RESULTS = 20


@tool
def read_file(repo: str, path: str, ref: str, start_line: int = None, end_line: int = None) -> str:
    """Read a file from the repository. Supports optional line range. Returns file content with line numbers."""
    try:
        content_file = _github_client().get_repo(repo).get_contents(path, ref=ref)
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
    q = f"{query} repo:{repo}"
    if path_filter:
        q += f" path:{path_filter}"

    try:
        results = _github_client().search_code(q)
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


_DEFINITION_PATTERNS = [
    "def {symbol}",
    "class {symbol}",
    "function {symbol}",
    "const {symbol}",
    "let {symbol}",
    "var {symbol}",
]

_MAX_DEFINITION_RESULTS = 5


@tool
def find_definition(repo: str, symbol: str, path_filter: str = None) -> str:
    """Find where a symbol (function, class, variable) is defined in the repository.

    Args:
        repo: Repository full name (owner/repo).
        symbol: The symbol name to find the definition of.
        path_filter: Optional path prefix to narrow the search.
    """
    pattern_query = " OR ".join(f'"{p.format(symbol=symbol)}"' for p in _DEFINITION_PATTERNS)
    q = f"{pattern_query} repo:{repo}"
    if path_filter:
        q += f" path:{path_filter}"

    try:
        results = _github_client().search_code(q)
    except Exception as e:
        return f"Error searching for definition: {e}"

    output = []
    for i, item in enumerate(results):
        if i >= _MAX_DEFINITION_RESULTS:
            break
        output.append(f"- {item.path}")

    if not output:
        return f"No definition found for '{symbol}'."
    return f"Possible definitions of '{symbol}':\n" + "\n".join(output)
