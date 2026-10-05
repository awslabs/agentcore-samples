# edge: the static front door

One Lambda function (container image, Node.js, arm64) behind two CloudFront distributions. It serves the page
that signs you in and starts VS Code (or, at `/terminal`, Claude Code alone), the browser scripts, and the pinned
VS Code web files. It never proxies to a box and never sees a token: the browser talks to AgentCore itself.

| Site | Serves |
|---|---|
| workbench | `/`, `/callback`, `/terminal` (the page), `/devbox-config.json`, `/devbox/*.js`, `/sw.js`, VS Code's static files. `/api/*` goes to the provisioner |
| webview | VS Code's patched webview shell, from a second origin |

## In the browser

| Script | Does |
|---|---|
| `oidc.js` | Okta sign-in: authorization code + PKCE, tokens in memory only |
| `loader.js` | asks `POST /api/box` for your box, waits while it's made or starting, then boots VS Code |
| `shim.js` | sends VS Code's WebSocket to your runtime's `/ws`, with the token in the subprotocol |
| `sw.js` | the Service Worker: VS Code's remote resources (icons, images) through `/invocations` |
| `terminal.js` | the `/terminal` page: AgentCore's own shell, running `devbox-claude` |

## Build and test

```bash
build/build.sh             # downloads the pinned VS Code tarball, verifies it, writes dist/
npm test                   # unit tests
npm run test:browser       # headless Chrome: the webview shell, the shim, the Service Worker, a real VS Code boot
npm run image              # the Lambda image (arm64)
```

## What to know

- **Configuration** (Lambda environment): `WORKBENCH_ORIGIN`, `WEBVIEW_ORIGIN` (they must differ) and
  `DEVBOX_CONFIG_JSON` (the browser config). Deploy sets them.
- **One person per browser profile.** When someone else signs in, the page clears the last person's VS Code
  state first.
- **An open tab keeps the box running.** It stops about an idle timeout (`DEVBOX_IDLE_SECONDS`) after the last
  tab closes, unless Claude is still working.

