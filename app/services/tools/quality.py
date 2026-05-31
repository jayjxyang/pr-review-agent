"""Quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import get_github_client

logger = get_logger(__name__)

_SECRET_PATTERNS = [
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[\w!@#$%^&*]{8,}', "password assignment"),
    (r'(?:sk-|sk_live_|sk_test_)[a-zA-Z0-9]{20,}', "OpenAI/Stripe key"),
    (r'ghp_[a-zA-Z0-9]{36,}', "GitHub personal access token"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', "private key"),
    (r'(?:bearer|authorization)\s*[=:]\s*["\']?[a-zA-Z0-9\-_.]{20,}', "bearer token"),
    (r'(?:secret|api_key|apikey|token)\s*[=:]\s*["\']?[\w\-]{16,}', "secret/key assignment"),
]


def run_secret_scan(repo: str, pr_number: int) -> list[dict]:
    """Standalone secret scan — returns list of finding dicts.
    Called before the agent graph as an independent security bypass.
    Returns: [{"filename": str, "line": int, "description": str}, ...]
    """
    try:
        pr = get_github_client().get_repo(repo).get_pull(pr_number)
    except Exception as e:
        logger.warning("secret_scan_error", error=str(e))
        return []

    findings = []
    for f in pr.get_files():
        patch = f.patch or ""
        for line_num, line in enumerate(patch.splitlines(), 1):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, description in _SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "filename": f.filename,
                        "line": line_num,
                        "description": description,
                    })
                    break
    return findings


@tool
def scan_secrets(repo: str, pr_number: int) -> str:
    """Scan the PR diff for potential hardcoded secrets, API keys, tokens, or passwords.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number to scan.
    """
    findings = run_secret_scan(repo, pr_number)
    if not findings:
        return "No secrets detected in the PR diff."
    lines = [f"- {f['filename']}:L{f['line']}: {f['description']}" for f in findings]
    return f"Potential secrets found ({len(findings)}):\n" + "\n".join(lines)


@tool
def check_test_coverage(repo: str, source_path: str, ref: str) -> str:
    """Check if a source file has test references. Searches for imports/usages in test files.

    Args:
        repo: Repository full name (owner/repo).
        source_path: Path to the source file to check.
        ref: Git ref (branch or SHA).
    """
    module_name = source_path.split("/")[-1].replace(".py", "").replace(".ts", "").replace(".js", "")

    q = f"{module_name} repo:{repo} path:test"
    try:
        results = get_github_client().search_code(q)
    except Exception as e:
        return f"Error searching for test references: {e}"

    output = []
    for i, item in enumerate(results):
        if i >= 10:
            break
        output.append(f"- {item.path}")

    if not output:
        return f"No test references found for '{source_path}'."
    return f"Test files referencing '{module_name}':\n" + "\n".join(output)


@tool
def get_ci_status(repo: str, pr_number: int) -> str:
    """Get CI check run statuses for the PR's HEAD commit.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number.
    """
    try:
        repo_obj = get_github_client().get_repo(repo)
        pr = repo_obj.get_pull(pr_number)
        commit = repo_obj.get_commit(pr.head.sha)
        checks = commit.get_check_runs()
    except Exception as e:
        return f"Error fetching CI status: {e}"

    output = []
    for check in checks:
        status = check.status
        conclusion = check.conclusion or "pending"
        output.append(f"- {check.name}: {status}/{conclusion}")

    if not output:
        return "No CI checks found for this PR."
    return "\n".join(output)


_MAX_LOG_LINES = 100


@tool
def get_ci_logs(repo: str, pr_number: int, check_name: str) -> str:
    """Get failure details for a specific CI check. Use get_ci_status first to see check names.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number.
        check_name: Name of the CI check to get logs for.
    """
    try:
        repo_obj = get_github_client().get_repo(repo)
        pr = repo_obj.get_pull(pr_number)
        commit = repo_obj.get_commit(pr.head.sha)
        checks = commit.get_check_runs()
    except Exception as e:
        return f"Error fetching CI logs: {e}"

    target_check = None
    for check in checks:
        if check.name == check_name:
            target_check = check
            break

    if not target_check:
        return f"Check '{check_name}' not found."

    if target_check.conclusion == "success":
        return f"Check '{check_name}' passed — no failure logs."

    try:
        annotations = target_check.get_annotations()
    except Exception:
        annotations = []

    output = [f"Check '{check_name}' — conclusion: {target_check.conclusion}"]
    for ann in annotations:
        if len(output) >= _MAX_LOG_LINES:
            output.append(f"\n[truncated — showing first {_MAX_LOG_LINES} entries]")
            break
        output.append(f"  {ann.path}:{ann.start_line} [{ann.annotation_level}] {ann.message}")

    if len(output) == 1:
        output.append("  No annotations available. Check the CI run URL for full logs.")

    return "\n".join(output)
