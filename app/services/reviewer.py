"""Post review results to GitHub as PR review comments."""

from app.core.logging import get_logger
from app.services.github import get_github_client, get_pr_patches

logger = get_logger(__name__)

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🔵"}

# Hidden HTML marker stamped on every body we post. GitHub renders HTML comments
# invisibly, so we can recognise (and delete) our own prior artifacts on re-post.
_MARKER = "<!-- bot4bread:ai-review -->"


def _parse_valid_lines(patch: str) -> set[int]:
    """Return the set of new-file line numbers that GitHub will accept for inline
    comments on this file.

    GitHub only allows annotating lines present on the RIGHT side of the unified
    diff (added ``+`` lines and unchanged context lines). We walk each ``@@`` hunk
    header to seed the new-file line counter, then advance it for context/added
    lines and skip removed lines. Tolerant of malformed patches.
    """
    valid: set[int] = set()
    new_line = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            # Header looks like: @@ -a,b +c,d @@ optional section ; c is the
            # new-file start line of this hunk.
            try:
                plus_part = raw.split("+", 1)[1].lstrip()
                start = plus_part.split(" ", 1)[0].split(",", 1)[0]
                new_line = int(start)
            except (ValueError, IndexError):
                # Unparseable header — stop trusting positions for this hunk.
                new_line = 0
            continue
        if not raw:
            # Blank line in a unified diff is a context line for an empty source line.
            valid.add(new_line)
            new_line += 1
            continue
        marker = raw[0]
        if marker == "-":
            continue  # removed line: not present on the right side
        if marker == "\\":
            continue  # e.g. "\ No newline at end of file"
        # '+' (added) or ' ' (context) — both exist on the right side
        valid.add(new_line)
        new_line += 1
    return valid


def post_review(repo_full_name: str, pr_number: int, result: dict, head_sha: str = "") -> list[int]:
    """Post the agent's review to GitHub as a PR review with inline comments.

    Args:
        result: Graph output dict with keys: risk_level, summary, comments.
               Comments with severity="resolved" are filtered out of inline posting.
        head_sha: Optional HEAD commit SHA of the PR (passed by the caller).

    Returns:
        List of GitHub comment IDs for the posted inline review comments.
    """
    gh = get_github_client()
    pr = gh.get_repo(repo_full_name).get_pull(pr_number)

    summary = result.get("summary", "")
    all_comments = result.get("comments", [])
    risk_level = result.get("risk_level", "low")

    # Separate resolved vs new/open comments
    resolved = [c for c in all_comments if c.get("severity") == "resolved"]
    active_comments = [c for c in all_comments if c.get("severity") != "resolved"]

    if not active_comments and not summary and not resolved:
        return []

    # Learn which (filename, line) pairs are actually annotatable. If the diff
    # cannot be fetched we leave this as None and skip validation rather than
    # suppressing every comment.
    valid_lines: dict[str, set[int]] | None = None
    try:
        patches = get_pr_patches(repo_full_name, pr_number)
        valid_lines = {p.filename: _parse_valid_lines(p.patch) for p in patches}
    except Exception as exc:
        logger.warning("diff_fetch_failed_skip_line_validation", error=str(exc))
        valid_lines = None

    # Build body with resolution summary if applicable
    body = f"{_MARKER}\n## AI Review (risk: {risk_level})\n\n{summary}"
    if resolved:
        body += f"\n\n**Re-review:** {len(resolved)} prior issue(s) resolved."

    gh_comments = []
    unanchored = []  # valid findings we could not place inline (hallucinated lines)
    for c in active_comments:
        severity = c.get("severity", "suggestion")
        emoji = _SEVERITY_EMOJI.get(severity, "🔵")
        filename = c.get("filename", "unknown")
        line = c.get("line", 1)

        # Per-comment degrade: one bad line must not nuke the whole inline review.
        if valid_lines is not None and line not in valid_lines.get(filename, set()):
            unanchored.append(c)
            continue

        gh_comments.append({
            "path": filename,
            "line": line,
            "side": "RIGHT",
            "body": f"{_MARKER}\n{emoji} **{severity}**: {c.get('comment', '')}",
        })

    # Surface unanchored findings as text so they are never silently lost.
    if unanchored:
        body += "\n\n### Findings that could not be anchored to the diff\n\n"
        for c in unanchored:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "🔵")
            body += (
                f"- {emoji} **{c.get('filename', 'unknown')}:{c.get('line', '?')}** "
                f"— {c.get('comment', '')}\n"
            )

    # Idempotent re-post: delete our own prior artifacts before posting new ones so
    # retries / re-reviews replace rather than stack duplicates. Defensive: never
    # let cleanup break the main flow.
    try:
        for rc in pr.get_review_comments():
            if _MARKER in (getattr(rc, "body", "") or ""):
                try:
                    rc.delete()
                except Exception:
                    pass
        for ic in pr.get_issue_comments():
            if _MARKER in (getattr(ic, "body", "") or ""):
                try:
                    ic.delete()
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("prior_artifact_cleanup_skipped", error=str(exc))

    try:
        if gh_comments:
            review = pr.create_review(body=body, event="COMMENT", comments=gh_comments)
            try:
                return [rc.id for rc in review.get_review_comments()]
            except Exception:
                return []
        else:
            pr.create_issue_comment(body)
            return []
    except Exception as exc:
        logger.warning("inline_review_failed_fallback", error=str(exc))
        # body already carries the marker; do not double-stamp.
        fallback = body + "\n\n### Findings\n\n"
        for c in active_comments:
            emoji = _SEVERITY_EMOJI.get(c.get("severity"), "🔵")
            fallback += f"- {emoji} **{c.get('filename', 'unknown')}:{c.get('line', '?')}** — {c.get('comment', '')}\n"
        pr.create_issue_comment(fallback)
        return []
