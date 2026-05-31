"""Tool collection — imports all tool modules and exposes a flat list."""

from app.services.tools.code_read import read_file, search_code, find_references, find_definition
from app.services.tools.pr_context import get_pr_info, get_pr_changed_files, get_pr_diff
from app.services.tools.knowledge import read_repo_rules, query_review_history
from app.services.tools.control import finish_review, escalate
from app.services.tools.git_history import git_log, git_blame
from app.services.tools.quality import scan_secrets, check_test_coverage, get_ci_status, get_ci_logs

ALL_TOOLS = [
    # Code reading
    read_file,
    search_code,
    find_references,
    find_definition,
    # PR context
    get_pr_info,
    get_pr_changed_files,
    get_pr_diff,
    # Git history
    git_log,
    git_blame,
    # Knowledge
    read_repo_rules,
    query_review_history,
    # Quality
    scan_secrets,
    check_test_coverage,
    get_ci_status,
    get_ci_logs,
    # Control
    finish_review,
    escalate,
]
