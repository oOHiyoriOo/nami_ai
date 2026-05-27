#!/usr/bin/env python3
"""Gitea MCP Server (stdio) — create, read, comment on, and close issues."""

import os

import httpx
from mcp.server.fastmcp import FastMCP

# ── Configuration ────────────────────────────────────────────────────────────
GITEA_URL = os.environ["GITEA_URL"]  # required — set via env (e.g. https://your.gitea.host)
GITEA_TOKEN = os.environ["GITEA_TOKEN"]  # required — set via env
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP("gitea")

_client = httpx.Client(
    base_url=f"{GITEA_URL}/api/v1",
    headers={
        "Authorization": f"token {GITEA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    timeout=30,
)


def _api(method: str, path: str, **kwargs) -> dict | list | str:
    """Call the Gitea API and return the parsed response."""
    resp = _client.request(method, path, **kwargs)
    resp.raise_for_status()
    if resp.status_code == 204:
        return "ok"
    return resp.json()


def _slim_user(user: dict | None) -> str | None:
    """Reduce a full user object to just the login name."""
    if not user:
        return None
    return user.get("login")


def _slim_label(label: dict) -> str:
    return label.get("name", "")


def _slim_issue(issue: dict) -> dict:
    """Strip noisy fields from an issue object."""
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "body": issue.get("body"),
        "user": _slim_user(issue.get("user")),
        "assignees": [_slim_user(a) for a in (issue.get("assignees") or [])],
        "labels": [_slim_label(l) for l in (issue.get("labels") or [])],
        "comments": issue.get("comments"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "html_url": issue.get("html_url"),
    }


def _slim_comment(comment: dict) -> dict:
    """Strip noisy fields from a comment object."""
    return {
        "id": comment.get("id"),
        "user": _slim_user(comment.get("user")),
        "body": comment.get("body"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
    }


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 20,
) -> list[dict]:
    """List issues for a repository.

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        state: Filter by state — "open", "closed", or "all".
        limit: Max number of issues to return (default 20).
    """
    issues = _api(
        "GET",
        f"/repos/{owner}/{repo}/issues",
        params={"state": state, "limit": limit, "type": "issues"},
    )
    return [_slim_issue(i) for i in issues]


@mcp.tool()
def get_issue(owner: str, repo: str, issue_number: int) -> dict:
    """Get full details of a single issue including body/description.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: The issue number.
    """
    return _slim_issue(_api("GET", f"/repos/{owner}/{repo}/issues/{issue_number}"))


@mcp.tool()
def get_issue_comments(owner: str, repo: str, issue_number: int) -> list[dict]:
    """Get all comments on an issue.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: The issue number.
    """
    comments = _api("GET", f"/repos/{owner}/{repo}/issues/{issue_number}/comments")
    return [_slim_comment(c) for c in comments]


@mcp.tool()
def comment_on_issue(
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
) -> dict:
    """Post a comment on an issue.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: The issue number.
        body: The comment text (Markdown supported).
    """
    return _slim_comment(_api(
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        json={"body": body},
    ))


@mcp.tool()
def create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
) -> dict:
    """Create a new issue in a repository.

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        title: Issue title.
        body: Issue description (Markdown supported).
    """
    return _slim_issue(_api(
        "POST",
        f"/repos/{owner}/{repo}/issues",
        json={"title": title, "body": body},
    ))


@mcp.tool()
def close_issue(
    owner: str,
    repo: str,
    issue_number: int,
    comment: str = "",
) -> dict:
    """Close an issue, optionally leaving a final comment.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: The issue number.
        comment: Optional closing comment (e.g. summary of the fix).
    """
    if comment:
        _api(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": comment},
        )
    return _slim_issue(_api(
        "PATCH",
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        json={"state": "closed"},
    ))


if __name__ == "__main__":
    mcp.run(transport="stdio")
