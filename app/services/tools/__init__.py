"""Tool collection — imports all tool modules and exposes a flat list."""

from app.services.tools.code_read import read_file, search_code, find_references
from app.services.tools.pr_context import get_pr_info, get_pr_changed_files, get_pr_diff
from app.services.tools.knowledge import read_repo_rules
from app.services.tools.control import finish_review, escalate

ALL_TOOLS = [
    read_file,
    search_code,
    find_references,
    get_pr_info,
    get_pr_changed_files,
    get_pr_diff,
    read_repo_rules,
    finish_review,
    escalate,
]
