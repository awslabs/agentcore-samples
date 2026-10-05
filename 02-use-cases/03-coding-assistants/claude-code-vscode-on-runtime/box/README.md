# box: the dev box image

The container image every person's AgentCore Runtime session runs: VS Code server, Claude Code (CLI and
VS Code extension), the AWS CLI and the MCP proxy for AWS, behind a small proxy. One image for everyone;
what's personal is written at boot from the runtime's environment.

```
AgentCore ──:8080──► proxy (devboxproxy)  /ping · /invocations · /ws (VS Code's connection)
                       └──► VS Code server on 127.0.0.1:3000 (dev, uid 1000)
AgentCore's shell (/terminal) ──► devbox-claude ──► Claude Code in tmux (as dev)
PID 1: the supervisor (root) starts both, writes the boot-time config, waits for /mnt/workspace (EFS)
```

## Build and test

```bash
cd proxy && npm ci && npm test && cd ..               # proxy unit tests
python3 -m unittest discover -s test -p 'test_*.py'   # hooks, config renderers, supervisor, devbox-claude
docker buildx build --platform linux/arm64 --load -t devbox-box:dev .   # the image (arm64)
node test/smoke/smoke.mjs                             # runs the image like AgentCore would and checks it
```

## What to know

- **Every download is pinned and checked** (sha256, signatures, hashes) in the Dockerfile's `fetch` stage.
  The explainer's *The box image* page lists them.
- **Written at boot, from the runtime environment** (`DEVBOX_*`, set by deploy and the provisioner):
  `/etc/devbox/aws-config` (the person's Identity Center role), `managed-settings.d/20-tier.json` (their
  models) and `managed-mcp.json` (web search). All root-owned.
- **Root-owned and read-only to `dev`:** `/etc/claude-code`, `/etc/devbox`, `/opt`, `/usr/local/bin`. No sudo,
  no setuid files, and everything started for `dev` runs with `no_new_privs`.
- **Two modes:** with root and capabilities (a microVM), the proxy and VS Code run as separate users; with
  no capabilities, everything runs as one user. The first log line and `op: diag` say which.
- **Resume:** `devbox-claude` attaches to the tmux session `claude`, or starts `claude --resume` on the last
  session. Files, Claude's sessions and the AWS sign-in live on EFS, so they survive a new microVM.

## Files

| Path | What |
|---|---|
| `Dockerfile` | `fetch` (download and verify), then the box |
| `proxy/` | the Node proxy: `/ping`, `/invocations`, the `/ws` relay |
| `rootfs/opt/devbox/lib/devbox/` | the supervisor, the boot-time config renderers (`config.py`), `devbox-claude`'s code |
| `rootfs/etc/claude-code/` | managed settings and the three hooks |
| `test/` | unit tests and the container smoke test |

