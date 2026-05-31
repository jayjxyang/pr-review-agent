"""Git history tools — git_log and git_blame via GitHub API."""

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.github import get_github_client, graphql_query

logger = get_logger(__name__)

_MAX_COMMITS = 10

_BLAME_QUERY = """
query($owner: String!, $name: String!, $ref: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $ref) {
      ... on Commit {
        blame(path: $path) {
          ranges {
            startingLine
            endingLine
            commit {
              oid
              message
              author {
                name
                date
              }
            }
          }
        }
      }
    }
  }
}
"""


@tool
def git_log(repo: str, ref: str = "HEAD", path: str = None, limit: int = 10) -> str:
    """Get recent commit history. Optionally filter by file path.

    Args:
        repo: Repository full name (owner/repo).
        ref: Git ref (branch or SHA). Defaults to HEAD.
        path: Optional file path to filter commits.
        limit: Max number of commits to return (default 10, max 10).
    """
    limit = min(limit, _MAX_COMMITS)
    try:
        repo_obj = get_github_client().get_repo(repo)
        kwargs = {"sha": ref}
        if path:
            kwargs["path"] = path
        commits = repo_obj.get_commits(**kwargs)
    except Exception as e:
        return f"Error fetching git log: {e}"

    output = []
    for i, commit in enumerate(commits):
        if i >= limit:
            break
        sha = commit.sha[:7]
        msg = commit.commit.message.split("\n")[0]
        author = commit.commit.author.name
        date = commit.commit.author.date.strftime("%Y-%m-%d")
        files = [f.filename for f in (commit.files or [])]
        files_str = ", ".join(files[:5])
        if len(files) > 5:
            files_str += f" (+{len(files) - 5} more)"
        output.append(f"{sha} {date} [{author}] {msg}\n  files: {files_str}")

    if not output:
        return "No commits found."
    return "\n".join(output)


@tool
def git_blame(repo: str, path: str, ref: str, start_line: int, end_line: int) -> str:
    """Get blame information for a line range in a file. Shows who last modified each line.

    Args:
        repo: Repository full name (owner/repo).
        path: File path in the repository.
        ref: Git ref (branch or SHA).
        start_line: Start line number (1-based).
        end_line: End line number (1-based, inclusive).
    """
    owner, name = repo.split("/", 1)
    try:
        data = graphql_query(_BLAME_QUERY, {
            "owner": owner,
            "name": name,
            "ref": ref,
            "path": path,
        })
    except Exception as e:
        return f"Error fetching blame: {e}"

    blame_obj = data.get("repository", {}).get("object", {})
    if not blame_obj or "blame" not in blame_obj:
        return f"Error: could not retrieve blame for {path} at {ref}"

    ranges = blame_obj["blame"]["ranges"]
    output = []
    for r in ranges:
        r_start = r["startingLine"]
        r_end = r["endingLine"]
        if r_end < start_line or r_start > end_line:
            continue
        commit = r["commit"]
        sha = commit["oid"][:7]
        author = commit["author"]["name"]
        date = commit["author"]["date"][:10]
        msg = commit["message"].split("\n")[0]
        output.append(f"L{r_start}-{r_end}: {sha} [{author} {date}] {msg}")

    if not output:
        return f"No blame data for lines {start_line}-{end_line} in {path}"
    return "\n".join(output)
