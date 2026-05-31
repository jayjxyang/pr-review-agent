import fnmatch
import requests
from dataclasses import dataclass
from functools import lru_cache
import yaml

from github import Github, GithubException

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Files that carry no meaningful signal for a code review.
_SKIP_PATTERNS = [
    # Lock files
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Pipfile.lock",
    "poetry.lock",
    "go.sum",
    "Cargo.lock",
    "*.lock",
    # Minified / generated assets
    "*.min.js",
    "*.min.css",
    "*.pb.go",
    "*.pb.py",
    # Images & binary assets
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.webp",
    "*.pdf",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
]


@dataclass(frozen=True)
class FilePatch:
    filename: str
    patch: str  # unified diff content for this file


def _should_skip(filename: str, extra_patterns: list[str] | None = None) -> bool:
    name = filename.split("/")[-1]  # match against basename only for most patterns
    for pattern in _SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    if extra_patterns:
        for pattern in extra_patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
                return True
    return False


@lru_cache(maxsize=1)
def _github_client() -> Github:
    """Cached Github client — one instance per worker process."""
    return Github(get_settings().github_app_token)


def get_pr_patches(repo_full_name: str, pr_number: int, *, extra_skip_patterns: list[str] | None = None) -> list[FilePatch]:
    """Fetch the diff for every reviewable file in the PR.

    Returns a list of FilePatch objects, one per file.  Binary files (no patch)
    and files matching _SKIP_PATTERNS are excluded.

    Raises GithubException on API errors — the caller (Celery task) handles retries.
    """
    try:
        repo = _github_client().get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
    except GithubException as exc:
        logger.error(
            "github_fetch_failed",
            repo=repo_full_name,
            pr=pr_number,
            status=exc.status,
            error=str(exc.data),
        )
        raise

    patches: list[FilePatch] = []
    skipped = 0

    for f in pr.get_files():
        if _should_skip(f.filename, extra_patterns=extra_skip_patterns):
            skipped += 1
            continue
        if not f.patch:  # binary files have no patch attribute
            skipped += 1
            continue
        patches.append(FilePatch(filename=f.filename, patch=f.patch))

    logger.info(
        "pr_files_fetched",
        repo=repo_full_name,
        pr=pr_number,
        reviewable=len(patches),
        skipped=skipped,
    )
    return patches


def get_pr_incremental_diff(repo_full_name: str, base_sha: str, head_sha: str) -> list[FilePatch]:
    """Fetch the diff between two commits (base_sha..head_sha).

    Used for re-review: compares last-reviewed commit to current HEAD.
    Returns a list of FilePatch objects. Binary files and skip-pattern files excluded.

    Raises GithubException on API errors.
    """
    repo = _github_client().get_repo(repo_full_name)
    comparison = repo.compare(base_sha, head_sha)

    patches: list[FilePatch] = []
    for f in comparison.files:
        if _should_skip(f.filename):
            continue
        if not f.patch:
            continue
        patches.append(FilePatch(filename=f.filename, patch=f.patch))

    logger.info(
        "incremental_diff_fetched",
        repo=repo_full_name,
        base=base_sha[:7],
        head=head_sha[:7],
        files=len(patches),
    )
    return patches


def get_pr_head_sha(repo_full_name: str, pr_number: int) -> str:
    """Get the HEAD commit SHA of the PR branch."""
    gh = _github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)
    return pr.head.sha


def get_repo_config(repo_full_name: str, ref: str) -> dict:
    """Fetch and parse .ai-review/config.yml from a repo.

    Returns parsed dict, or {} on missing file / parse error.
    """
    try:
        repo = _github_client().get_repo(repo_full_name)
        content = repo.get_contents(".ai-review/config.yml", ref=ref)
        return yaml.safe_load(content.decoded_content) or {}
    except GithubException:
        logger.debug("repo_config_not_found", repo=repo_full_name)
        return {}
    except yaml.YAMLError:
        logger.warning("repo_config_invalid_yaml", repo=repo_full_name)
        return {}


def graphql_query(query: str, variables: dict) -> dict:
    """Execute a GitHub GraphQL query. Returns the 'data' portion of the response."""
    settings = get_settings()
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {settings.github_app_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL error: {result['errors'][0]['message']}")
    return result["data"]
