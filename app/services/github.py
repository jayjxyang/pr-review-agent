import fnmatch
from dataclasses import dataclass
from functools import lru_cache

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


def _should_skip(filename: str) -> bool:
    name = filename.split("/")[-1]  # match against basename only for most patterns
    for pattern in _SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    return False


@lru_cache(maxsize=1)
def _github_client() -> Github:
    """Cached Github client — one instance per worker process."""
    return Github(get_settings().github_app_token)


def get_pr_patches(repo_full_name: str, pr_number: int) -> list[FilePatch]:
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
        if _should_skip(f.filename):
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


def get_pr_head_sha(repo_full_name: str, pr_number: int) -> str:
    """Get the HEAD commit SHA of the PR branch."""
    gh = _github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)
    return pr.head.sha
