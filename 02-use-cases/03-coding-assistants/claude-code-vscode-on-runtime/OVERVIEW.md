# Server-side dev box · overview and setup

The short introduction is [README.md](README.md); the click-through explainer is [explainer.html](explainer.html).

Controls on a developer's laptop can be undone by anyone with admin rights on it. This sample moves Claude Code,
the code and the network into a dev box the developer can't reconfigure. Each person gets VS Code in the browser,
or Claude Code on its own in a full-page browser terminal, backed by their own AgentCore Runtime **microVM**. Their
files live in their own folder on an EFS file system in your account. The Claude Code policy, the egress allowlist
and IAM are all enforced out of their reach (one exception: AgentCore's shell; see the explainer's *Locking the
files* page, gap G1).

**Understand it first:** open [explainer.html](explainer.html) in a browser (one self-contained file, no server): before you
start (groups and permission sets), the whole app, the box image, the config files, the hooks, how the files
are locked, deploying it, and a step-by-step test of the running box.

`local-test/` runs the real box image and the real edge on a laptop, against local stand-ins for Okta and
AgentCore, so you can try changes without AWS.

## What is built

```
browser (one tab per person)
  │  static files only: the loader, our scripts, the pinned VS Code web assets (and their xterm.js)
  ├──► CloudFront "workbench" ─┐
  ├──► CloudFront "webview"  ──┴──► Lambda devbox-edge (static, logs only; never sees a token)
  │
  │  the Okta access token, held in the page's memory only
  ├──► Okta (sign-in: authorization code + PKCE)
  └──► AgentCore Runtime devbox_vm_<name>  POST /invocations, WSS /ws (VS Code), WSS /ws/shells (the terminal)
         JWT authorizer: only this person's uid, the Dev Box app, scope devbox, group devbox-users
         resource policy: the workbench and the terminal; no command API, no stop, no act-as-user, for anyone
           └──► the box (one session = one microVM, 2 vCPU / 8 GB, in the box subnet)
                  proxy :8080 ──► openvscode-server 127.0.0.1:3000 ("dev", no sudo)
                  /terminal: AgentCore's shell ──► devbox-claude ──► Claude Code in tmux
                  Claude Code 2.1.277 (CLI + extension) under root-owned managed settings
                  /mnt/workspace = their EFS access point /devbox/<name> (every file operation as 1000:1000)
                  egress: subnet ──► Network Firewall (domain allowlist) ──► NAT ──► internet; DNS Firewall on the resolver
```

- **VS Code in the browser** (`https://<workbench>/`) is openvscode-server 1.109.5. Its static files
  come from two CloudFront distributions in front of one static Lambda. The second one
  serves VS Code's webview shell from a different origin than the workbench.
- **The terminal** (`https://<workbench>/terminal`) is Claude Code alone, full page. It uses
  AgentCore's own terminal (`InvokeAgentRuntimeCommandShell`), drawn by the xterm.js in the pinned VS
  Code bundle. The page starts `devbox-claude`, which attaches to the same tmux session VS Code's
  Claude Code terminal uses.
- **The browser talks to AgentCore directly** with the person's Okta token: the loader for
  status, a WebSocket shim for VS Code's connection, a Service Worker for images and other remote
  resources, and the terminal page's socket. The token travels only in the WebSocket subprotocol or
  the `Authorization` header, never in a URL. CloudFront and the Lambda never carry a token or any
  user data.
- **One box per person**: a microVM runtime each, whose authorizer accepts only
  that person's Okta `uid`. Each person has their own EFS access point. Their execution role may mount
  only that one, and the file system's policy says the same, so no box can mount another person's
  folder or the file system's root.
- **The resource policy** allows the workbench, its WebSocket and the terminal,
  and denies the command API, `StopRuntimeSession` and the act-as-user actions to everyone, admins
  included. The trade-off: the terminal is a shell AgentCore starts in the box, and on a microVM it
  may be root. Only the owner can open it, in their own box. Gap G1 under [Known gaps](#known-gaps) says what that costs.
- **Inside the box** each person signs in to IAM Identity Center with a device code and gets their
  existing `ClaudeCode-Power` or `ClaudeCode-Standard` permission set ([IDENTITY-SETUP.md](IDENTITY-SETUP.md)). The
  box's own execution role has no Bedrock access.

Components:

| Folder | What | README |
|---|---|---|
| `box/` | the container image: proxy, entrypoint, `devbox-claude`, managed settings, hooks | [box/README.md](box/README.md) |
| `edge/` | the static Lambda, the browser scripts (loader, shim, Service Worker, terminal page), the patched webview shell | [edge/README.md](edge/README.md) |
| `local-test/` | the whole stack on the laptop, no AWS; headless Chrome end-to-end test | [local-test/README.md](local-test/README.md) |
| `deploy/` | `devbox.py`: builds and runs it in your account (Python + boto3, via `uv run`) | [deploy/README.md](deploy/README.md) |
| `explainer.html`, `tools/` | the click-through explainer: one self-contained file, open it in a browser. `tools/embed_sources.py` refreshes the four box files it embeds (`--check` says if they're out of date) | [README.md](README.md) |

## Prerequisites

- **The identity setup** ([IDENTITY-SETUP.md](IDENTITY-SETUP.md)): the `ClaudeCode-*` permission sets, assigned to the
  tier groups `ai-claude-power` and `ai-claude-standard` (pushed from Okta by SCIM). `devbox.py check` verifies both.
- **An Okta org with API Access Management** (a custom authorization server with its own scopes,
  claims and access policies). The sample uses its `default` server.
- **On the laptop:** Docker Desktop with buildx (arm64 images), Node.js 22 or newer, and `uv`
  (it brings Python 3.12 and boto3). Google Chrome for `local-test`.
- **A demo or sandbox account**, never production: `deploy` creates billed resources (see [Costs](#costs)).
- **An Availability Zone that microVM VPC mode supports.** In us-east-1 that's the zone ids
  `use1-az1`, `use1-az2` and `use1-az4`. Zone names map to different ids in each account; `check`
  looks up `DEVBOX_AZ`'s id and refuses an unsupported one.

## Setup, in order

Run everything from `server/`.

1. **Prove it locally.** `local-test/run.sh e2e` builds the box image and the edge, starts the
   stack, and drives a real VS Code session in headless Chrome. No AWS calls, no credentials.
2. **Fill in `deploy/devbox.env`** (table below) and run `uv run deploy/devbox.py check`. It is
   read-only: it checks the prerequisites (including `DEVBOX_AZ`'s zone id) and lists what a deploy
   would create.
3. **`uv run deploy/devbox.py deploy`, pass 1**, with `DEVBOX_OKTA_CLIENT_ID` still blank. It builds
   and pushes both images, then creates IAM, the web-search gateway, the EFS file system (with a policy
   that names nobody), the network (with the EFS mount target and the free S3 gateway endpoint), the
   firewalls, the box table and permissions boundary, and the edge. It ends by printing the Okta steps
   with the real CloudFront domain filled in. Until pass 2 the site answers with an error.
4. **Do the Okta steps it printed**: the `devbox-users` group (who gets a box) and, for each
   member, exactly one tier group; the "Dev Box" single-page app with the exact redirect URIs, the
   Trusted Origin, the `devbox` scope, the `client_id` claim and a `groups` claim that carries
   `devbox-users` and the tier groups, and the access policy at priority 1. Check it with the
   authorization server's Token Preview.
5. **Set `DEVBOX_OKTA_CLIENT_ID`** in `devbox.env` to the app's client id.
6. **`deploy`, pass 2.** It creates the provisioner (a Lambda behind an HTTP API with Okta's JWT
   authorizer, at `/api/box` on the workbench URL). Nobody is named anywhere: no box exists yet.
7. **Open the box** as any member of `devbox-users`. `https://<workbench>/` is VS Code;
   `https://<workbench>/terminal` is Claude Code on its own. On someone's first visit the page shows
   "Setting up your dev box" while the provisioner makes their folder, role and microVM runtime
   (a few minutes, once), then the cold start. Someone outside the group, or in none or two tier groups,
   is told why and gets nothing.
8. **One full session in `network learn`** (the default): the cold start, `aws sso login` in the box,
   a Claude turn, a web search, a reconnect. Learn mode logs every name the box reaches and blocks nothing.
9. **`uv run deploy/devbox.py network enforce`.** It lists every name learn mode saw that the
   allowlist (`deploy/templates/egress-allowlist.txt`) doesn't have. Add the ones the box needs,
   then run it again.
10. **Work through the [spike checklist](#spike-checklist).**
11. **`uv run deploy/devbox.py network pause`** whenever nobody is demoing (see [Costs](#costs)).

### Moving from the Instances boxes

For an account that still has the first (Instances) deploy. Okta needs no change: the workbench
domain stays the same.

1. **`uv run deploy/devbox.py check`** (read-only). It confirms `DEVBOX_AZ`'s zone id is one microVM
   VPC mode supports, and lists the EFS storage, the `devbox_vm_<name>` runtimes it would make, and
   the old Instances boxes it leaves alone.
2. **`uv run deploy/devbox.py deploy`.** One pass, since the client id is already set. It builds both
   images, creates the EFS file system with each person's access point and execution role, the mount
   target and the S3 gateway endpoint, and the new microVM runtimes, and points the browser config at
   them. It never deletes the old Instances runtimes, capacity providers or volumes; the page just
   stops using them.
3. **Open `https://<workbench>/terminal`** and sign in. After the cold start, the full-page terminal
   starts Claude Code in the box (`https://<workbench>/` is VS Code). Then check spike items 3 (the
   EFS mount), 6 (`deploy/shell-probe.py`: the terminal and its uid) and 8 (`deploy/ws-probe.sh`:
   VS Code's WebSocket).
4. **Optional, once a microVM box works: `uv run deploy/devbox.py retire-instances`.** It lists what it
   will delete and asks for the account id. Then it deletes the Instances runtimes `devbox_<name>`,
   every recorded session **and its EBS volume**, the capacity providers, and the old roles. That can't
   be undone, and nothing on those volumes is copied to EFS (the workbench never connected on
   Instances, so there shouldn't be anything worth keeping).

Later: `status` shows what is deployed (read-only). `reset-box <user>` moves one person to a new
session, so their next visit gets a fresh microVM; their files stay on EFS. `undeploy` removes it all
but keeps the EFS file system (everyone's files) and the network it sits in; `undeploy --delete-volumes`
also deletes the file system with every file on it. deploy is safe to re-run. All the details are in
[deploy/README.md](deploy/README.md).

## `devbox.env`

No secrets. Account ids, the Identity Center instance and the identity store are looked up from
the admin profiles.

| Setting | Required | Default | What |
|---|---|---|---|
| `ORG_ADMIN_PROFILE`, `AI_ADMIN_PROFILE` | yes | | admin profiles in your `~/.aws/config`: Identity Center, and the account everything is created in |
| `DEVBOX_TIER_GROUPS` | no | `Power=ai-claude-power Standard=ai-claude-standard` | `<tier>=<Okta group>`: a member of `DEVBOX_OKTA_GROUP` in exactly one of these gets that tier (its `ClaudeCode-<tier>` permission set and its models). The same groups grant the permission sets, so the box and IAM agree. Nobody is listed by name |
| `OKTA_DOMAIN` | yes | | your Okta org, for example `example.okta.com` |
| `OKTA_AUTH_SERVER`, `OKTA_AUDIENCE` | no | `default`, `api://default` | the authorization server and the token audience |
| `DEVBOX_OKTA_GROUP` | no | `devbox-users` | who gets a box: the provisioner and every runtime require it |
| `DEVBOX_OKTA_CLIENT_ID` | for pass 2 | blank | the Dev Box app's client id (public) |
| `IDC_REGION`, `IDC_START_URL` | no | `us-east-1`, the identity store's start URL | the Identity Center sign-in inside the box |
| `GEO`, `OPUS_MODEL`, `SONNET_MODEL`, `HAIKU_MODEL` | the models each tier uses | Opus 5, Sonnet 4.5, Haiku 4.5 | inference profile = `GEO.MODEL`; Power gets all three, Standard gets Sonnet and Haiku |
| `REGION`, `DEVBOX_AZ` | no | `us-east-1`, `us-east-1a` | the region is fixed; the zone's id must be `use1-az1`, `use1-az2` or `use1-az4` (the box subnet and the EFS mount target live there) |
| `DEVBOX_COMPUTE` | no | `microvm` | the only value: deploy no longer makes Instances boxes |
| `DEVBOX_IDLE_SECONDS` | no | `3600` | a session ends after this long idle; at most 28800 (a microVM lives 8 hours) |
| `DEVBOX_EGRESS_MODE` | no | `learn` | `learn` or `enforce`; `network learn` and `network enforce` switch it later |
| `FIREWALL_ENFORCE_DEFAULTS` | no | `aws:drop_established aws:alert_established` | spike switch: enforce mode's default actions |

## Known gaps

What the design doesn't close today. The explainer's *Locking the files* page shows G1; G2 is the egress mode.

| # | Gap | Impact | Fix | Status |
|---|---|---|---|---|
| G1 | **The owner's root AgentCore shell.** The resource policy allows `InvokeAgentRuntimeCommandShell` so that `/terminal` works. The page immediately runs `exec /usr/local/bin/devbox-claude`, but the owner, with their own token, can open the shell API with their own client (`deploy/shell-probe.py` does) and get a root bash with `CAP_SYS_ADMIN`. | They can rewrite `/etc/claude-code` and `/etc/devbox` (all the in-box controls) for that microVM's life, read the execution role's credentials and the token the proxy sees. The AWS-side controls still hold: IAM on their own session, the SCP, the gateway, the firewalls, their own EFS folder only. The owner's Okta access token is now worth a root shell on their own box. | Deny `InvokeAgentRuntimeCommandShell` in `deploy/templates/iam/runtime-resource-policy.json`. `/terminal` goes away; Claude then runs only in VS Code's terminal, as `dev`. | Open, a deliberate trade-off. |
| G2 | **Egress is in `learn` mode.** | Alerts only; the box can reach any name. | One full session in learn mode, then `uv run deploy/devbox.py network enforce` (add what it lists that the box needs, run it again). | Open. |
| G3 | **The in-box controls govern the agent, not the human.** In VS Code's terminal `dev` can run `curl`, `aws` or `psql`, or fetch another tool. | The managed settings and the hook don't apply to a human's shell. | By design: the AWS-side controls are what limit the human (IAM/SCP on models, the gateway, the firewall once enforced). | Accepted. |
| G4 | **Claude's bash sandbox is off** on the microVM (the boot probe logs `sandbox: off (no bwrap or no user namespaces)`; bubblewrap isn't in the image). | Claude's commands run with `dev`'s full rights inside the box. | Add bubblewrap + socat to the image; the supervisor already writes `30-sandbox.json` when its probe passes. Whether the microVM allows the user namespaces bwrap needs is **VERIFY**. | Open. |
| G5 | **Hook and deny rules are pattern-based.** | A determined agent can phrase around them. | They're guard rails that explain; the hard limits are Unix permissions and the AWS-side controls. | Accepted. |
| G6 | **The Identity Center sign-in isn't matched to the box owner**. | A person who has someone else's Okta credentials can sign in to AWS as them from their own box, and their Claude runs on that person's tier. | A box-side check: compare the Identity Center session's user with `DEVBOX_OWNER` after sign-in (for example in an `awsAuthRefresh` wrapper), and refuse otherwise. Today Okta MFA and sign-on policy are the control. | Not built. |
| G7 | **openvscode-server 1.109.5 is the newest release, but old** (Feb 2026), and bundles older versions of some packages. | The box image swaps in newer releases of six of them (shell-quote, undici, lodash-es, picomatch, and socks with ip-address 10.x; `OVS_NODE_FIXES` in `box/Dockerfile`). | Bump the box and the edge together from one tarball (the server refuses a renderer from another commit), then re-run the CSP and browser tests. | Open. |

Also worth knowing: the egress allowlist can be forged by SNI and doesn't cover DNS-over-HTTPS; all webviews share one origin; the SCP applies only outside the management account (see the explainer's *Before you start* page).

## Spike checklist

What only the live account can tell. `deploy` prints this list at the end of every run, with the
real runtime ARN, workbench URL and image filled in. [deploy/README.md › Spike checklist](deploy/README.md#spike-checklist)
says why each item matters.

1. **op: diag.** The header names AgentCore forwards on `/invocations` and the `/ws` upgrade, and, new on microVM, the uid, the capabilities and user namespaces: multi-user or single-user?
2. **The resource policy.** With the owner's own token, `/commands` and `stopruntimesession` are refused while the workbench and the terminal work; another person's token is refused on all of them.
3. **EFS mount works.** The first invoke of a new session doesn't end in a 424; `/mnt/workspace` is 1000:1000, mode 0750; `dev` writes `home/` and `projects/`, and nothing of anyone else's is visible.
4. **Learn-mode domains.** A full session in learn mode, then `network enforce` lists what the allowlist lacks.
5. **Image size.** The box image stays under AgentCore's 2 GB limit.
6. **Terminal opens.** `uv run deploy/shell-probe.py <user>`: a STATUS frame with the shell id, then `id`. Record the shell's uid.
7. **MMDSv2.** Whether `requireMMDSV2` is accepted for a microVM runtime.
8. **/ws works on microVM.** The workbench connects, and `deploy/ws-probe.sh <user>` shows HTTP 101 for the browser's way.
9. **Idle stop.** Close every tab; the session ends about `DEVBOX_IDLE_SECONDS` later.
10. **Resume after an idle stop, or after the 8-hour maximum.** The files are back and `devbox-claude` resumes the last Claude Code session.
11. **The real 60-minute WebSocket cutoff.** VS Code reconnects with no dialog; the terminal survives.
12. **Cold start.** How long the page waits (the EFS mount is part of it), and any 409 / 424 / timeout.
13. **undeploy, then deploy.** A marker file in `~/` is still there.
14. **Web search.** After `aws sso login`, the web-search MCP server connects without `/mcp`.
15. **DNS Firewall.** In enforce mode an unlisted name doesn't resolve; an allowlisted one does, and a new session still mounts EFS.
16. **Port 80.** In enforce mode plain HTTP can't leave the box.
17. **Sign-out.** Sign out, reload twice: the Okta sign-in page appears (the org accepted the POST to `/v1/logout`).

## Costs

us-east-1, from the AWS pricing pages.

| What | Price | Billed |
|---|---|---|
| a box: one microVM session (2 vCPU / 8 GB) | AgentCore Runtime's consumption pricing (per vCPU and GB of memory, per second) | only while its session runs: it ends `DEVBOX_IDLE_SECONDS` after the box goes idle, and after 8 hours at most |
| everyone's files: EFS (Standard, elastic throughput) | about $0.30 per GB-month, plus throughput per GB read and written | always |
| Network Firewall endpoint | by the hour, per endpoint: see [AWS Network Firewall pricing](https://aws.amazon.com/network-firewall/pricing/) | until `network pause`; the NAT's hourly charge is waived while chained to it |
| the NAT's Elastic IP | $0.005/h | until `network pause` |
| DNS Firewall | about $0.60 per million queries | per query |
| the old Instances volumes (100 GiB gp3 each) | about $8/month each | until `retire-instances` |

Idle stop keeps the boxes cheap. `network pause` deletes the firewall, the NAT and its IP (the
fixed hourly cost). `network resume` takes about 10 to 15 minutes. While paused a box can't start,
and a running one loses Bedrock, STS and SSO.

## Demo runbook

1. At least 15 minutes before: `uv run deploy/devbox.py network resume`, then `status`.
2. Open the workbench URL in a browser profile of its own and sign in as Ada. The first visit after
   an idle stop is a cold start (the page says so and shows the wait).
3. VS Code opens the projects folder with a Claude Code terminal at its prompt. Claude Code shows an
   AWS sign-in URL and a code in that terminal: open the URL on the laptop and enter the code. Ada is
   now signed in with her own `ClaudeCode-Power` session. Grace does the same in another browser
   profile and gets her own box.
4. Or open `<workbench>/terminal` for Claude Code alone (the **Terminal · VS Code** switch goes back
   and forth). It's the same Claude Code session as VS Code's terminal, in the same tmux.
5. Show the controls: the managed settings under `/etc/claude-code` can be read but not changed by
   `dev` (there's no sudo; this holds in two-user mode only, which spike item 1 tells you), Claude Code
   uses only the managed web-search server, and in enforce mode `curl -m 5 http://example.com` in a
   terminal can't connect.
6. Afterwards: close the tabs and run `uv run deploy/devbox.py network pause`.

Lifecycle facts to know:

- A session ends about `DEVBOX_IDLE_SECONDS` (default 1 hour) plus 2 minutes after its last tab
  closes and its last Claude turn ends. An open tab keeps it running, even in the background. A
  microVM session lives at most 8 hours (`maxLifetime`); then the next visit is a cold start.
- AgentCore ends a WebSocket after 60 minutes. VS Code reconnects on its own and the terminal
  survives (tested locally with a 20 s cutoff; the real one is spike item 11). The terminal page
  reattaches to the same shell the same way.
- The Okta refresh token lasts at most 12 hours, so a forgotten tab stops being able to reconnect.
- The files survive everything short of `undeploy --delete-volumes` (`$HOME`, the projects, Claude
  Code's sessions are on EFS). Processes and tmux sessions don't survive a new session:
  `devbox-claude` resumes the last Claude Code session.
- `reset-box <user>` starts a person on a fresh microVM, with the same files.

## Alternatives considered

- **The Instances compute type.** A persistent EC2 instance and EBS
  volume per person, but AgentCore lets only HTTP `/invocations` into an Instances box. VS Code's
  WebSocket is refused, and so is the terminal.
- **microVM with AgentCore's managed session storage** instead of EFS. It needs no VPC, but it is
  wiped whenever the runtime's version changes (every image update) and after 14 days without an
  invocation. EFS keeps the files through both.
- **Remote-SSH, SSM or tunnels over `InvokeAgentRuntimeCommandShell`.** Not built. The resource policy
  now allows the owner AgentCore's terminal (for the `/terminal` page), but VS Code still goes through
  the box's proxy; a tunnel would add a second way in beside it.
- **VS Code Remote Tunnels.** It relays through Microsoft with a GitHub or Microsoft sign-in, which
  breaks the AWS-only rule, the Okta identity chain and the egress allowlist.
- **One origin per webview.** Upstream VS Code gives each webview its own origin, which needs a
  wildcard certificate on a custom domain. Here all webviews share one CloudFront origin, separate
  from the workbench ([edge/README.md](edge/README.md) › Webview isolation).

## Sources

- AgentCore Runtime: [file system configurations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-filesystem-configurations.html),
  [VPC configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html),
  [lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html),
  [quotas](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html),
  [pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- AgentCore Runtime Instances (the first deploy): [how it works](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html),
  [security model](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-security.html)
- Amazon EFS: [pricing](https://aws.amazon.com/efs/pricing/)
- AWS Network Firewall: [pricing](https://aws.amazon.com/network-firewall/pricing/)
- Claude Code: [managed settings](https://code.claude.com/docs/en/managed-settings)
