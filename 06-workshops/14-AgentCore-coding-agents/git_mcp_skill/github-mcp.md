# GitHub MCP Skill — Fix Issues via AgentCore Gateway

You have a `gateway` MCP server that provides GitHub tools. These appear as native tools — call them directly without any manual HTTP/curl commands.

## Available tools

All tools are prefixed with `mcp__gateway__GitHubMCP___` when called:

| Tool | Arguments | Description |
|------|-----------|-------------|
| `get_issue` | `owner`, `repo`, `issue_number` | Fetch issue title, body, state, labels |
| `list_issue_comments` | `owner`, `repo`, `issue_number` | List all comments on an issue |
| `comment_on_issue` | `owner`, `repo`, `issue_number`, `body` | Post a comment on an issue |
| `get_file` | `owner`, `repo`, `path`, `ref` (default: "main") | Read a file's content from the repository |
| `list_files` | `owner`, `repo`, `path` (default: ""), `ref` (default: "main") | List files and directories at a path |
| `create_branch` | `owner`, `repo`, `branch`, `from_branch` (default: "main") | Create a new branch |
| `put_file` | `owner`, `repo`, `branch`, `path`, `content`, `message` | Create or update a file (full content) |
| `add_labels` | `owner`, `repo`, `issue_number`, `labels` (list) | Add labels without removing existing ones |
| `set_labels` | `owner`, `repo`, `issue_number`, `labels` (list) | Replace all labels on an issue |
| `remove_label` | `owner`, `repo`, `issue_number`, `label` | Remove a single label |
| `create_pull_request` | `owner`, `repo`, `title`, `head`, `base`, `body`, `draft` | Open a PR |

## Workflow: Fix an issue and open a PR

1. **Read the issue** — call `get_issue` to understand what needs fixing
2. **Read relevant files** — call `get_file` / `list_files` to understand the code
3. **Create a fix branch** — call `create_branch` with name `fix/issue-N`
4. **Push the fix** — call `put_file` with the full corrected file content
5. **Open a PR** — call `create_pull_request` referencing the issue
6. **Add labels** — call `add_labels` with `["agent:claude-code", "fix-submitted"]`
7. **(Optional) Comment** — call `comment_on_issue` linking to the PR

## Rules

- **NEVER approve, merge, or close a PR.** Only submit PRs for human review.
- **NEVER close an issue.** Leave issues open for the human reviewer.
- `put_file` expects the **full file content** (not a diff). Read the current file first if you need to patch it.
- Branch names: `fix/issue-N` where N is the issue number.
- Commit messages: `fix: description (closes #N)`.
