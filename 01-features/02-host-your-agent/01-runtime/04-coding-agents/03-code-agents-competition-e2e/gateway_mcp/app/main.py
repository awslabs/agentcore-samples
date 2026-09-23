"""GitHub MCP Server.

Exposes the actions the DevOps agent needs against a target GitHub repo.

Auth: retrieves a GitHub installation access token by signing a JWT with
the GitHub App's private key (stored in AWS Secrets Manager) and exchanging
it via the GitHub API.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import List, Optional

import boto3
import httpx
import jwt
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import BaseModel

GITHUB_API = "https://api.github.com"

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
GITHUB_APP_SECRET_ARN = os.environ.get("GITHUB_APP_SECRET_ARN", "")

_secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

GITHUB_TOKEN: Optional[str] = None
GITHUB_TOKEN_EXP: int = 0


def _load_app_creds() -> dict:
    """Read the GitHub App credentials from Secrets Manager.

    The secret JSON contains: app_id, private_key, installation_id.
    """
    resp = _secrets_client.get_secret_value(SecretId=GITHUB_APP_SECRET_ARN)
    return json.loads(resp["SecretString"])


def _mint_installation_token() -> tuple[str, int]:
    """Sign a JWT and exchange it for a GitHub installation access token."""
    creds = _load_app_creds()
    app_id = creds["app_id"]
    private_key = creds["private_key"]
    installation_id = creds["installation_id"]

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {encoded_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "DevOpsAgent-GitHubMCP",
            },
        )
        r.raise_for_status()
        data = r.json()

    token = data["token"]
    # GitHub installation tokens expire in 1 hour
    expires_at = now + 3600
    return token, expires_at


def _headers() -> dict:
    if not GITHUB_TOKEN:
        raise RuntimeError("GitHub token not initialized — middleware did not run.")
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "DevOpsAgent-GitHubMCP",
    }


class TokenMiddleware(Middleware):
    """Mint a GitHub installation token on first request, refresh near expiry."""

    async def on_request(self, context: MiddlewareContext, call_next):
        global GITHUB_TOKEN, GITHUB_TOKEN_EXP
        now = int(time.time())
        if GITHUB_TOKEN is None or now >= GITHUB_TOKEN_EXP - 300:
            GITHUB_TOKEN, GITHUB_TOKEN_EXP = _mint_installation_token()
            print(
                f"[TokenMiddleware] minted token prefix={GITHUB_TOKEN[:8]}... expires_in={GITHUB_TOKEN_EXP - now}s",
                flush=True,
            )
        return await call_next(context)


mcp = FastMCP("GitHubMCP")
mcp.add_middleware(TokenMiddleware())


class Issue(BaseModel):
    number: int
    title: str
    body: Optional[str] = None
    state: str
    labels: List[str] = []
    url: str


class Comment(BaseModel):
    id: int
    author: str
    body: str
    created_at: str


# ---------------- Issues ----------------


@mcp.tool()
def get_issue(owner: str, repo: str, issue_number: int) -> Issue:
    """Fetch an issue's title, body, state, labels, and URL."""
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}",
            headers=_headers(),
        )
        r.raise_for_status()
        d = r.json()
    return Issue(
        number=d["number"],
        title=d["title"],
        body=d.get("body") or "",
        state=d["state"],
        labels=[lbl["name"] for lbl in d.get("labels", [])],
        url=d["html_url"],
    )


@mcp.tool()
def list_issue_comments(owner: str, repo: str, issue_number: int) -> List[Comment]:
    """List all comments on an issue in chronological order."""
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers=_headers(),
            params={"per_page": 100},
        )
        r.raise_for_status()
        return [
            Comment(
                id=x["id"],
                author=x["user"]["login"],
                body=x.get("body") or "",
                created_at=x["created_at"],
            )
            for x in r.json()
        ]


@mcp.tool()
def comment_on_issue(owner: str, repo: str, issue_number: int, body: str) -> dict:
    """Post a comment on an issue. Returns {id, url}."""
    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers=_headers(),
            json={"body": body},
        )
        r.raise_for_status()
        d = r.json()
        return {"id": d["id"], "url": d["html_url"]}


@mcp.tool()
def update_comment(owner: str, repo: str, comment_id: int, body: str) -> str:
    """Edit an existing issue comment by id. Returns the comment URL."""
    with httpx.Client(timeout=30) as c:
        r = c.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/comments/{comment_id}",
            headers=_headers(),
            json={"body": body},
        )
        r.raise_for_status()
        return r.json()["html_url"]


# ---------------- Assignees ----------------


@mcp.tool()
def assign_issue(owner: str, repo: str, issue_number: int, assignees: List[str]) -> List[str]:
    """Add assignees to an issue. Returns the final list of assignee logins."""
    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/assignees",
            headers=_headers(),
            json={"assignees": assignees},
        )
        r.raise_for_status()
        return [u["login"] for u in r.json().get("assignees", [])]


# ---------------- Labels ----------------


@mcp.tool()
def set_labels(owner: str, repo: str, issue_number: int, labels: List[str]) -> List[str]:
    """Replace an issue's labels with the given set. Returns the final labels."""
    with httpx.Client(timeout=30) as c:
        r = c.put(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/labels",
            headers=_headers(),
            json={"labels": labels},
        )
        r.raise_for_status()
        return [lbl["name"] for lbl in r.json()]


@mcp.tool()
def add_labels(owner: str, repo: str, issue_number: int, labels: List[str]) -> List[str]:
    """Add labels to an issue without removing existing ones."""
    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/labels",
            headers=_headers(),
            json={"labels": labels},
        )
        r.raise_for_status()
        return [lbl["name"] for lbl in r.json()]


@mcp.tool()
def remove_label(owner: str, repo: str, issue_number: int, label: str) -> bool:
    """Remove a single label from an issue. Returns True on success."""
    with httpx.Client(timeout=30) as c:
        r = c.delete(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
            headers=_headers(),
        )
        return r.status_code in (200, 204)


# ---------------- File reads ----------------


@mcp.tool()
def get_file(owner: str, repo: str, path: str, ref: str = "main") -> str:
    """Read a file's content from the repository. Returns the decoded text content."""
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(),
            params={"ref": ref},
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        return content


@mcp.tool()
def list_files(owner: str, repo: str, path: str = "", ref: str = "main") -> List[str]:
    """List files and directories at a given path in the repository."""
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(),
            params={"ref": ref},
        )
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list):
            return [f"{item['name']}{'/' if item['type'] == 'dir' else ''}" for item in items]
        return [items["name"]]


# ---------------- Git refs + commits ----------------


@mcp.tool()
def create_branch(owner: str, repo: str, branch: str, from_branch: str = "main") -> str:
    """Create a new branch from `from_branch`. Returns the new ref name."""
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{from_branch}",
            headers=_headers(),
        )
        r.raise_for_status()
        sha = r.json()["object"]["sha"]
        r = c.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            headers=_headers(),
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        r.raise_for_status()
        return r.json()["ref"]


@mcp.tool()
def put_file(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    content: str,
    message: str,
) -> str:
    """Create or update a file on a branch via the Contents API. Returns the commit SHA."""
    with httpx.Client(timeout=30) as c:
        existing = c.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(),
            params={"ref": branch},
        )
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if existing.status_code == 200:
            payload["sha"] = existing.json()["sha"]
        r = c.put(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        return r.json()["commit"]["sha"]


# ---------------- Pull Requests ----------------


@mcp.tool()
def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str = "",
    draft: bool = False,
) -> dict:
    """Open a PR from `head` → `base`. Returns {number, url}."""
    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_headers(),
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
            },
        )
        r.raise_for_status()
        d = r.json()
        return {"number": d["number"], "url": d["html_url"]}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", stateless_http=True)
