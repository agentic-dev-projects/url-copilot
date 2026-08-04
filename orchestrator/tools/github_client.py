"""
github_client.py — GitHub API operations for branch/PR management.

Authentication
--------------
Reads GITHUB_TOKEN and GITHUB_REPO from environment variables (set in .env).
GITHUB_REPO must be in "owner/repo" format (e.g. "agentic-dev-projects/url-copilot").

The GitHub client is lazy-initialised on first use so that imports succeed even
when GITHUB_TOKEN is not set (e.g. in unit tests that mock these functions).
Call _client() to get the authenticated Github instance.

PyGithub usage
--------------
All three public functions use PyGithub (github.Github).  The library handles
rate limiting, retries, and pagination automatically.

create_branch     — GET repo, GET main SHA, create ref "refs/heads/{branch_name}"
commit_and_push   — git add service/ + git commit + git push origin {branch}
create_pr         — POST /repos/{owner}/{repo}/pulls
poll_pr_status    — GET /repos/{owner}/{repo}/pulls/{number}
"""

import os
import subprocess

from github import Auth, Github, GithubException

_GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
_GITHUB_REPO_ENV = "GITHUB_REPO"
_DEFAULT_BASE_BRANCH = "main"

# Module-level lazy cache — one client per process lifetime
_gh_client: Github | None = None


def _client() -> Github:
    """Return an authenticated Github instance, creating it on first call."""
    global _gh_client
    if _gh_client is None:
        token = os.getenv(_GITHUB_TOKEN_ENV)
        if not token:
            raise EnvironmentError(
                f"GITHUB_TOKEN environment variable is not set. "
                f"Add it to .env before calling GitHub tools."
            )
        _gh_client = Github(auth=Auth.Token(token))
    return _gh_client


def _repo():
    """Return the PyGithub Repository object for GITHUB_REPO."""
    repo_name = os.getenv(_GITHUB_REPO_ENV)
    if not repo_name:
        raise EnvironmentError(
            f"GITHUB_REPO environment variable is not set (expected 'owner/repo')."
        )
    return _client().get_repo(repo_name)


def create_branch(branch_name: str, from_branch: str = _DEFAULT_BASE_BRANCH) -> str:
    """Create a new git branch from from_branch and return the branch ref URL.

    Idempotent: if the branch already exists, returns the URL without error.

    Args:
        branch_name: Name for the new branch (e.g. "feature/add-qr-endpoint").
        from_branch: Branch to fork from (default "main").

    Returns:
        The full ref URL of the created branch
        (e.g. "https://github.com/org/repo/tree/feature/add-qr-endpoint").

    Raises:
        GithubException: if the API call fails for any reason other than the
            branch already existing.
        EnvironmentError: if GITHUB_TOKEN or GITHUB_REPO are not set.
    """
    repo = _repo()
    source = repo.get_branch(from_branch)
    try:
        repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=source.commit.sha,
        )
    except GithubException as exc:
        # 422 = branch already exists — treat as success
        if exc.status != 422:
            raise
    return f"https://github.com/{repo.full_name}/tree/{branch_name}"


def commit_and_push(branch_name: str, commit_message: str) -> str:
    """Stage all changes under service/, commit, and push to branch_name.

    This must be called after write_file tool calls and before create_pr.
    Without this step, the feature branch on GitHub has no commits and
    create_pr will fail with "no commits between main and <branch>".

    Args:
        branch_name:    The feature branch to push to (must already exist —
                        call create_branch first).
        commit_message: Git commit message.

    Returns:
        A summary string: "Committed and pushed to <branch_name>: <short_sha>"

    Raises:
        RuntimeError: if git add, commit, or push fails.
    """
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )

    def _run(cmd: list[str]) -> str:
        result = subprocess.run(
            cmd, cwd=repo_root, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed: {' '.join(cmd)}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result.stdout.strip()

    # Checkout the feature branch (it was created on GitHub; create it locally
    # if it doesn't exist, tracking origin)
    try:
        _run(["git", "checkout", branch_name])
    except RuntimeError:
        _run(["git", "checkout", "-b", branch_name, f"origin/{branch_name}"])

    _run(["git", "add", "service/"])
    _run(["git", "commit", "-m", commit_message])
    _run(["git", "push", "origin", branch_name])

    short_sha = _run(["git", "rev-parse", "--short", "HEAD"])
    return f"Committed and pushed to {branch_name}: {short_sha}"


def create_pr(
    title: str,
    body: str,
    branch: str,
    base: str = _DEFAULT_BASE_BRANCH,
) -> tuple[int, str]:
    """Open a pull request and return (pr_number, pr_url).

    Args:
        title:  Pull request title.
        body:   Pull request description (markdown).
        branch: The head branch (feature branch) to merge from.
        base:   The base branch to merge into (default "main").

    Returns:
        (pr_number, pr_url) tuple — e.g. (42, "https://github.com/org/repo/pull/42").

    Raises:
        GithubException: if the PR cannot be created (e.g. no commits ahead).
        EnvironmentError: if GITHUB_TOKEN or GITHUB_REPO are not set.
    """
    repo = _repo()
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch,
        base=base,
    )
    return pr.number, pr.html_url


def poll_pr_status(pr_number: int) -> dict:
    """Return the current merge/close status of a pull request.

    Args:
        pr_number: The integer PR number (from create_pr).

    Returns:
        {
          "merged":    bool,        — True if the PR has been merged
          "merged_by": str | None,  — GitHub login of the merger, or None
          "closed":    bool,        — True if the PR is closed (merged or declined)
          "state":     str,         — "open" | "closed"
        }

    Raises:
        GithubException: if pr_number does not exist.
        EnvironmentError: if GITHUB_TOKEN or GITHUB_REPO are not set.
    """
    repo = _repo()
    pr = repo.get_pull(pr_number)
    return {
        "merged": pr.merged,
        "merged_by": pr.merged_by.login if pr.merged_by else None,
        "closed": pr.state == "closed",
        "state": pr.state,
    }
