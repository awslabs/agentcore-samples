# deploy: devbox.py

> ⚠️ **Creates real, billed AWS resources.** Each box is an AgentCore Runtime microVM, billed while its session
> runs; everyone's files are on EFS; the egress firewall
> bills by the hour until `network pause` (see [AWS Network Firewall pricing](https://aws.amazon.com/network-firewall/pricing/)).

One Python script that builds the dev box in your account. Nobody is listed anywhere: every member of the Okta
group `devbox-users` who is in exactly one tier group gets their own box on their first visit, made by the
provisioner Lambda. Deploy builds everything shared, and keeps the existing boxes up to date.

```
uv run deploy/devbox.py check                        read-only: prerequisites, and what deploy would change
uv run deploy/devbox.py deploy                       create or update everything (safe to re-run)
uv run deploy/devbox.py network learn|enforce        egress: log every name / allow only the allowlist
uv run deploy/devbox.py network pause|resume         delete / recreate the firewall, NAT and its IP (the hourly cost)
uv run deploy/devbox.py status                       what is deployed (read-only)
uv run deploy/devbox.py reset-box <user>             a fresh microVM at that person's next visit; their files stay
uv run deploy/devbox.py retire-instances             delete the old Instances boxes, if any are left
uv run deploy/devbox.py undeploy [--delete-volumes]  remove it; everyone's files stay unless --delete-volumes
```

Output marks: ✓ already right, + changed, → would change (check), ! worth knowing, ✗ a problem.

## Before the first deploy

- **Your settings file.** `cp deploy/devbox.env.example deploy/devbox.env`, then set `OKTA_DOMAIN`. `devbox.env`
  is git-ignored.
- **The identity side** ([../IDENTITY-SETUP.md](../IDENTITY-SETUP.md)): the Okta groups, Okta connected to IAM
  Identity Center, and the `ClaudeCode-Power` and `ClaudeCode-Standard` permission sets assigned to the tier groups.
- **Your admin profiles** in `~/.aws/config`, named in `devbox.env` (`ORG_ADMIN_PROFILE`, `AI_ADMIN_PROFILE`).
- **Docker Desktop with buildx, Node.js 22+ and `uv`** on your laptop.
- **A supported Availability Zone** in `DEVBOX_AZ`: in us-east-1, one whose id is `use1-az1`, `use1-az2` or
  `use1-az4`. `check` verifies it.

## A first deploy

1. `check`: lists what deploy would create. Changes nothing.
2. `deploy`, with `DEVBOX_OKTA_CLIENT_ID` still blank. It prints the Okta steps with the real CloudFront URL.
3. Do the Okta steps: the Dev Box app, its URLs, the Trusted Origin, the `devbox` scope and claims, the access
   policy ([../IDENTITY-SETUP.md](../IDENTITY-SETUP.md) › 4). Copy the app's client id into `devbox.env`.
4. `deploy` again: sign-in now works. Each person's box is made on their first visit.
5. Use a box for a whole session in learn mode, then run `network enforce` (see the Spike checklist, item 4).

## What it makes

| | Names |
|---|---|
| Boxes | a runtime `devbox_vm_<name>` per person: a microVM (`DEVBOX_COMPUTE=microvm`) in the box subnet, their folder at `/mnt/workspace`, an authorizer that accepts only their Okta uid |
| Files | EFS file system `devbox`; each person's folder `/devbox/<name>`, through their own access point |
| Provisioner | Lambda `devbox-provisioner`, HTTP API `devbox-api`, table `devbox-boxes` |
| IAM | an execution role `devbox-exec-<name>` per person (mounts only their own folder; no Bedrock), inside the `devbox-exec-boundary` permissions boundary; the edge, gateway and provisioner roles |
| Network | VPC `devbox` in one AZ, a NAT, Network Firewall `devbox-fw`, DNS Firewall `devbox-dns` |
| Tools | gateway `devbox-tools` (web search), with a Cedar policy |
| Edge | Lambda `devbox-edge`, and two CloudFront distributions (workbench and webview) |
| Images | ECR `devbox-box` and `devbox-edge` (the tag is a hash of the source, so an unchanged image isn't rebuilt) |

What was made is recorded in `deploy/.state.json` (git-ignored). AWS is the source of truth: everything is found
by name or tag first, so a lost state file costs nothing.

## When nobody's using it

`network pause` deletes the firewall, the NAT and its IP (the hourly cost). While paused, a box can't start, and
a running one loses Bedrock and its AWS sign-in. `network resume` takes about 10 to 15 minutes: run it before
people start work.

## reset-box and undeploy

- `reset-box <user>`: that person's next visit starts a fresh microVM, with the same files. Nobody can stop the old
  session (the resource policy denies it to everyone); it ends at its idle timeout. They must reload the page.
- `undeploy`: lists everything, asks for the account id, then removes it all except the EFS file system
  (everyone's files) and the network it sits in. `--delete-volumes` deletes those too.
- After an undeploy, the next deploy gets a **new CloudFront URL**: update the Okta app's two URLs and the Trusted
  Origin. Deploy prints the old and new values.

## Troubleshooting

- **Sign-in fails with a `redirect_uri` error, or CORS on the token request:** the CloudFront URL changed. Update
  the Okta app's sign-in and sign-out URLs and the Trusted Origin.
- **The page says "Setting up your box failed":** the provisioner's log, `/aws/lambda/devbox-provisioner`, has the
  AWS error, including any missing permission.
- **A box won't start:** is the network paused? `status`, then `network resume`.
- **The first request of a session ends in a 424:** usually the EFS mount. Check the mount target (`status`), and
  TCP 2049 between `devbox-box` and `devbox-efs`.
- **`undeploy --delete-volumes` leaves the VPC:** AgentCore's network interface can stay up to 8 hours. Run it again
  later, or just deploy: deploy reuses them.

## Tests

```bash
cd deploy
uv run --no-project --python 3.12 --with 'boto3==1.43.108' --with 'botocore==1.43.108' --with 'pytest==9.1.1' pytest -q tests
```

No credentials and no AWS calls: every request is checked against botocore's service models, and whole commands
run against an in-memory fake AWS (`tests/fake_aws.py`).

## Files

| Path | What |
|---|---|
| `devbox.py` | the script |
| `provisioner.py` | the provisioner Lambda (it shares `devbox.py`'s code for making a box) |
| `devbox.env.example` | the settings, to copy to `devbox.env` |
| `shell-probe.py`, `ws-probe.sh` | spike probes: the terminal and the `/ws` handshake, the way a browser does them |
| `templates/` | IAM and resource policies, the Cedar rule, the egress allowlist, the firewall rules |
| `tests/` | pytest |

## Spike checklist

What only the live account can tell. Deploy prints these at the end of every run, with the real values filled in.

1. **op: diag.** The headers AgentCore forwards, and whether the box runs multi-user or single-user.
2. **The resource policy.** With the owner's own token, `/commands` and `stopruntimesession` are refused; another person's token is refused on everything.
3. **EFS mount works.** No 424 on a new session; `/mnt/workspace` is the person's folder only.
4. **Learn-mode domains.** After a full session, `network enforce` lists what the allowlist lacks.
5. **Image size.** The box image stays under AgentCore's 2 GB limit.
6. **Terminal opens.** `uv run deploy/shell-probe.py <user>` shows the shell, then `id`.
7. **MMDSv2.** Whether `requireMMDSV2` is accepted for a microVM runtime.
8. **/ws works on microVM.** The workbench connects, and `deploy/ws-probe.sh <user>` shows HTTP 101.
9. **Idle stop.** Close every tab: the session ends about `DEVBOX_IDLE_SECONDS` later.
10. **Resume after an idle stop, or after 8 hours.** The files are back, and Claude Code resumes its last session.
11. **The real 60-minute WebSocket cutoff.** VS Code reconnects with no dialog, and the terminal survives.
12. **Cold start.** How long the page waits on a new session, and any 409, 424 or timeout.
13. **undeploy, then deploy.** A marker file in `~/` is still there.
14. **Web search.** After the AWS sign-in, the web-search MCP server connects on its own.
15. **DNS Firewall.** In enforce mode an unlisted name doesn't resolve; an allowlisted one does.
16. **Port 80.** In enforce mode, plain HTTP can't leave the box.
17. **Sign-out.** Sign out, reload twice: the Okta sign-in page appears.
