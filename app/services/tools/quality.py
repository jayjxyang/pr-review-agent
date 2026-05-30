"""Quality tools — scan_secrets, check_test_coverage, get_ci_status, get_ci_logs."""

import re

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import _github_client

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


@tool
def scan_secrets(repo: str, pr_number: int) -> str:
    """Scan the PR diff for potential hardcoded secrets, API keys, tokens, or passwords.

    Args:
        repo: Repository full name (owner/repo).
        pr_number: PR number to scan.
    """
    try:
        pr = _github_client().get_repo(repo).get_pull(pr_number)
    except Exception as e:
        return f"Error fetching PR: {e}"

    findings = []
    for f in pr.get_files():
        patch = f.patch or ""
        for line_num, line in enumerate(patch.splitlines(), 1):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, description in _SECRET_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(f"- {f.filename}:L{line_num}: {description}")
                    break

    if not findings:
        return "No secrets detected in the PR diff."
    return f"Potential secrets found ({len(findings)}):\n" + "\n".join(findings)


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
        results = _github_client().search_code(q)
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
