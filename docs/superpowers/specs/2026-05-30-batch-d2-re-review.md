# Batch D2 — Re-review Incremental

## Goal

When a PR author pushes new commits after a review, perform an incremental re-review that evaluates whether prior issues were fixed, flags new problems, and avoids duplicate comments.

---

## 1. Problem

Currently `run_review` runs a full fresh review on every `pull_request.synchronized` webhook. It has no memory of prior findings, produces duplicate comments, and cannot tell the author which issues they successfully addressed.

---

## 2. Re-review Detection

In `run_review`, before invoking the graph, query PostgreSQL for the most recent review of the same PR:

```python
last_review = get_last_review(repo_full_name, pr_number)
```

- If `last_review` exists: extract `reviewed_sha` and unresolved comments → pass into graph state
- If `last_review` is `None`: normal first review (no behavior change)

---

## 3. Incremental Diff

New helper in `app/services/github.py`:

```python
def get_pr_incremental_diff(repo: str, base_sha: str, head_sha: str) -> list[FilePatch]:
```

- Uses GitHub Compare API: `GET /repos/{owner}/{repo}/compare/{base}...{head}`
- Returns `list[FilePatch]` (same format as `get_pr_patches`)
- Filters out skip-pattern files (same `_should_skip` logic)
- This diff shows only what changed since the last review

The agent also retains access to the full PR diff via the `get_pr_diff` tool for broader context.

---

## 4. State Changes

Add to `ReviewState`:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `prior_comments` | `list[dict]` | `[]` | Unresolved comments from last review (max 20) |
| `last_reviewed_sha` | `str` | `""` | SHA of the last reviewed commit |

Each prior comment dict contains: `id`, `filename`, `line`, `severity`, `comment`.

---

## 5. Agent Prompt Changes

### RE_REVIEW_ADDENDUM

When `prior_comments` is non-empty, append to the scan system prompt:

```
## Re-review Context

This PR was previously reviewed at commit {last_reviewed_sha}. The author has pushed new commits.
You have access to the incremental diff (changes since last review) below.

### Prior Unresolved Comments
{formatted_prior_comments}

For EACH prior comment, evaluate whether the new changes address it:
- If FIXED: include in your response as {"status": "resolved", "prior_comment_id": <id>}
- If NOT FIXED: re-flag it with the same severity
- If CHANGED INCORRECTLY: flag with new comment explaining the problem

Also review the new changes for any NEW issues not covered by prior comments.
```

### Incremental Diff Injection

The incremental diff is included in the initial HumanMessage (alongside the review request), formatted the same way as `get_pr_diff` output but prefixed with `[INCREMENTAL DIFF: {last_reviewed_sha[:7]}..{current_sha[:7]}]`.

---

## 6. Persistence Changes

### get_last_review

```python
def get_last_review(repo: str, pr_number: int) -> dict | None:
```

- Queries `reviews` table: `WHERE repo = ? AND pr_number = ? ORDER BY created_at DESC LIMIT 1`
- If found, loads unresolved comments: `WHERE review_id = ? AND resolved = false ORDER BY created_at ASC LIMIT 20`
- Returns `{"reviewed_sha": ..., "comments": [...]}` or `None`

### save_review updates

Modify `save_review` to accept an optional `resolved_comment_ids: list[int]` parameter:

- For each ID in the list, update `review_comments SET resolved = true WHERE id = ?`
- This runs in the same transaction as persisting the new review

---

## 7. Task Changes (run_review)

```python
# Before graph invocation:
last_review = get_last_review(repo_full_name, pr_number)

prior_comments = []
last_reviewed_sha = ""
incremental_diff = ""

if last_review:
    last_reviewed_sha = last_review["reviewed_sha"]
    prior_comments = last_review["comments"]
    patches = get_pr_incremental_diff(repo_full_name, last_reviewed_sha, ref)
    incremental_diff = "\n\n".join(f"## {p.filename}\n{p.patch}" for p in patches)

# Pass to graph:
graph.invoke({
    ...existing fields...,
    "prior_comments": prior_comments,
    "last_reviewed_sha": last_reviewed_sha,
})
```

The incremental diff text is passed into the initial messages, not as a state field (it's consumed once in the prompt, not referenced later).

---

## 8. Review Posting Changes

Modify `post_review` to:

- Only post NEW comments (not re-flag existing ones that are still open — they already exist on the PR)
- Include a summary line: "X prior issues resolved, Y still open, Z new issues found"
- If all prior issues are resolved and no new issues: post a short "All prior issues addressed" comment

---

## 9. Graph Changes

Modify `scan_call` node:

- If `state["prior_comments"]` is non-empty: append `RE_REVIEW_ADDENDUM` to system prompt, include incremental diff in the human message
- If empty: normal first-review behavior (no change)

The `finish_review` tool output is already a JSON dict with `comments` list. Extend each comment to optionally include `prior_comment_id` (if it's resolving an old comment) and `status` field (`"resolved"` or `"new"`).

---

## 10. File Structure

| File | Change |
|---|---|
| `app/agent/state.py` | **MODIFY** — Add `prior_comments`, `last_reviewed_sha` fields |
| `app/agent/prompts.py` | **MODIFY** — Add `RE_REVIEW_ADDENDUM` template |
| `app/agent/graph.py` | **MODIFY** — Inject re-review context in `scan_call` |
| `app/services/github.py` | **MODIFY** — Add `get_pr_incremental_diff` |
| `app/services/persistence.py` | **MODIFY** — Add `get_last_review`, modify `save_review` |
| `app/tasks/review.py` | **MODIFY** — Add re-review detection logic |
| `app/services/reviewer.py` | **MODIFY** — Update `post_review` for re-review summary |
| `tests/test_persistence.py` | **MODIFY** — Tests for `get_last_review`, resolved updates |
| `tests/test_github.py` | **NEW** — Tests for `get_pr_incremental_diff` |
| `tests/test_agent_graph.py` | **MODIFY** — Tests for re-review prompt injection |
| `tests/test_review_task.py` | **NEW** — Integration tests for re-review flow |

---

## 11. Scope Exclusions

- **GitHub webhook sync** (syncing manual resolves from GitHub back to PG) — deferred to a later batch
- **Comment threading** (linking new comments to old ones on GitHub) — not needed for MVP
- **Re-review of re-reviews** (multiple rounds) — works naturally since each review becomes the "last review" for the next push

---

## 12. Testing Strategy

- `get_last_review`: returns latest review with unresolved comments; returns `None` for first review
- `get_pr_incremental_diff`: calls GitHub compare API with correct SHAs; applies skip filters
- `scan_call` with `prior_comments`: injects RE_REVIEW_ADDENDUM into system prompt
- `scan_call` without `prior_comments`: normal first-review behavior (no addendum)
- `save_review` with `resolved_comment_ids`: marks specified comments as resolved
- `post_review` re-review: includes resolution summary, only posts new comments
- Integration: full re-review flow with mocked GitHub API + in-memory SQLite DB
