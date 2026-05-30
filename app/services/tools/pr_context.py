"""PR context tools — fetch PR metadata and diffs via GitHub API."""

from github import Github
from langchain_core.tools import tool

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_DIFF_LINES = 500


def _github_client() -> Github:
    return Github(get_settings().github_app_token)


@tool
def get_pr_info(repo: str, pr_number: int) -> str:
    """Get PR metadata: title, description, author, labels, and linked issues."""
    gh = _github_client()
    pr = gh.get_repo(repo).get_pull(pr_number)

    labels = ", ".join(l.name for l in pr.labels) or "none"
    body = pr.body or "(no description)"
    if len(body) > 2000:
        body = body[:2000] + "\n[truncated]"

    return (
        f"Title: {pr.title}\n"
        f"Author: {pr.user.login}\n"
        f"Branch: {pr.head.ref} → {pr.base.ref}\n"
        f"Labels: {labels}\n"
        f"State: {pr.state}\n"
        f"Commits: {pr.commits}\n"
        f"Changed files: {pr.changed_files}\n"
        f"Additions: +{pr.additions}, Deletions: -{pr.deletions}\n"
        f"\nDescription:\n{body}"
    )


@tool
def get_pr_changed_files(repo: str, pr_number: int) -> str:
    """Get the list of changed files in the PR with addition/deletion counts."""
    gh = _github_client()
    pr = gh.get_repo(repo).get_pull(pr_number)
    files = pr.get_files()

    output = []
    for f in files:
        status = f.status
        output.append(f"- [{status}] {f.filename} (+{f.additions}/-{f.deletions})")

    return "\n".join(output) if output else "No changed files."


@tool
def get_pr_diff(repo: str, pr_number: int, file_path: str) -> str:
    """Get the unified diff for a specific file in the PR. Use get_pr_changed_files first to see what files changed."""
    gh = _github_client()
    pr = gh.get_repo(repo).get_pull(pr_number)

    for f in pr.get_files():
        if f.filename == file_path:
            patch = f.patch or "(binary file or no changes)"
            lines = patch.splitlines()
            if len(lines) > _MAX_DIFF_LINES:
                patch = "\n".join(lines[:_MAX_DIFF_LINES])
                patch += f"\n\n[truncated — showing first {_MAX_DIFF_LINES} of {len(lines)} lines. Use read_file for full content.]"
            return f"```diff\n{patch}\n```"

    return f"Error: file '{file_path}' not found in this PR's changes."
