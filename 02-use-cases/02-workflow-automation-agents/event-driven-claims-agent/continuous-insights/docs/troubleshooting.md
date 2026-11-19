# Troubleshooting

Run every command from the repository root. Start with `python scripts/status.py --name <name>`. It prints the Agent Insight Store state, the online evaluation config state, how many sessions Failure Analysis has evaluated in the last 24 hours (with failures and without), and the active Insights.

Except where a section says otherwise, the messages below are the ones the scripts print. For how the pieces fit together, see [architecture.md](architecture.md). For setup steps, see the [README](../README.md).

## Install and deploy

These rows do not come from script messages. They cover step 1 of the [README walkthrough](../README.md#walkthrough).

| Symptom | Cause | Fix |
|---|---|---|
| `pip install -r requirements.txt` fails, or a script fails with an error that points at Python syntax | The sample targets Python 3.12 or later. | Check `python3 --version`. Create the virtual environment with Python 3.12 or later, activate it, and install again. |
| `ModuleNotFoundError: No module named 'boto3'` | The virtual environment is not active, or you did not install the requirements. | Run `source .venv/bin/activate` and `pip install -r requirements.txt` from the repository root. |
| `./deploy.sh` stops at the CDK step because the AgentCore CLI does not accept the project's `aws-cdk` version | The claims agent pins `aws-cdk` `^2.1128.0` in `agentcore/cdk/package.json`, and AgentCore CLI 0.30.x and 0.31.x accept only older CDK CLI versions. | In `<claims-agent-dir>/agentcore/cdk/package.json`, change `"aws-cdk": "^2.1128.0"` to `"aws-cdk": "~2.1126.0"`, then run `./deploy.sh us-east-1` again. |
| The first `./deploy.sh` fails with `DiscoveryUrl is not a valid URL` | The claims agent's `agentcore.json` ships a placeholder Cognito discovery URL for the `cognito-gateway-m2m` credential. | In the claims agent folder, run `agentcore remove credential --name cognito-gateway-m2m --yes`. This changes only the local configuration. Then run `./deploy.sh us-east-1` again. |
| `deploy.sh` or `test_invoke.py` fails for another reason | The claims agent is a separate sample with its own prerequisites (Node.js, the AgentCore CLI, the AWS CDK CLI, Docker or Finch, `uv`, model access). | Follow the claims agent's own README. Return to this sample when `./deploy.sh us-east-1` finishes and `test_invoke.py` succeeds. |

## Register and set up

| Symptom | Cause | Fix |
|---|---|---|
| `No runtime named '<name>' in this account and Region.` (`scripts/register_agent.py`) | The claims agent is not deployed in this account and Region, or its runtime has a different name from the default `ClaimsAgent_claimsagent`. | Deploy the claims agent from its own README. If it is deployed, run `aws bedrock-agentcore-control list-agent-runtimes --region <region>` and pass the name with `--runtime-name`. Check that `--region` matches where you deployed. |
| `<n> runtimes are named '<name>'; expected exactly one.` | More than one runtime has that exact name. | Pass the exact name of the one you want with `--runtime-name`. |
| `Runtime '<name>' is <status>, not READY. Wait for the deployment to finish.` | The runtime is still being created or updated, or its last deployment did not finish. | Wait for the deployment to finish, then run `scripts/register_agent.py` again. |
| `Could not read the runtime: ...` | The call to STS or `bedrock-agentcore-control` failed. The message carries the underlying error, such as expired credentials or a missing permission. | Fix the credentials or permission in the message. `scripts/register_agent.py` needs permission to call STS `GetCallerIdentity` and to list AgentCore runtimes. |
| `--name '<name>' is invalid: use 1-48 chars, start with a letter, letters/digits/underscore only` | `--name` becomes part of the evaluation config name and the role name, which have these rules. | Use a name that starts with a letter and has only letters, digits and underscores. |
| `agent_state_<name>.json not found. Run: python scripts/register_agent.py --name <name>` | You ran another script before `scripts/register_agent.py`, or with a different `--name`. | Run `scripts/register_agent.py` with the same `--name` you pass to the other scripts. |
| `This boto3 build has no Agent Insight operations. Upgrade to boto3 1.44.5 or later [...]` | The installed boto3 is older than 1.44.5, the first release with the Agent Insight operations. | Run `pip install -r requirements.txt --upgrade` in the same virtual environment. |
| `Store is <status>/<enablement>; wait for it to settle, then re-run.` (`scripts/setup_insights.py`) | The Agent Insight Store is in a state the script does not change, for example while a change is in progress. | Run `scripts/status.py` until the Store shows `ACTIVE / ENABLED`, then run `scripts/setup_insights.py` again. A Store that is disabled is enabled by the script. |
| `Expected service.name '<runtime name>.DEFAULT' but the log group has [...]` or `... has no spans yet.` | `scripts/setup_insights.py` queries the runtime log group for the last hour. The name the agent emits is not there yet, or it is different from the name `scripts/register_agent.py` computed. Insights are grouped by this name, so it has to match. | If the message says `no spans yet`, invoke the agent once, wait about 2 minutes for ingestion, and run the script again. If it lists other names, check that `--runtime-name` points at the runtime that produced them. |
| `CloudWatch Logs query failed.` (also `cancelled`, `timeout`) or `Timed out after <n> seconds waiting for ...` | A CloudWatch Logs Insights query or a status check did not finish in time. | Run the script again. Each setup step is safe to repeat. |
| `CreateOnlineEvaluationConfig failed: ...: The provided execution role cannot be assumed` | The service cannot assume the evaluation role. A role created seconds ago can take a moment to become assumable. The script retries this case four times before it exits. | Run `scripts/setup_insights.py` again. If it persists and your account requires an additional trust statement, save that statement as a JSON file (one object with `Effect`, `Principal` and `Action`) and pass it with `--extra-trust-statement <file>`. The script adds it to the evaluation role only. |
| `<file> must hold one trust statement with Effect, Principal and Action (missing: [...])` | The file passed to `--extra-trust-statement` is not a single trust statement. | Put one statement object in the file, not a full policy document. |
| `Config CREATE_FAILED: <reason>` or `Config ERROR: <reason>` | The service could not create or run the config. The reason comes from `failureReason`. | Read the reason. If it names a missing permission, check the evaluation role's policy: `scripts/setup_insights.py` rewrites it on every run, so run it again after you fix the cause. |
| `Timed out after 400 seconds waiting for the online evaluation config to become ACTIVE.` | The config has not reached `ACTIVE` yet. | Run `scripts/status.py`. When it shows `ACTIVE`, run `scripts/setup_insights.py` again so it saves the config ID in the state file. |

## Send claims

| Symptom | Cause | Fix |
|---|---|---|
| `FAIL <scenario> ThrottlingException: ...` and a final `<n> sent, <m> failed to invoke` (`scripts/drive.py`) | Too many concurrent invocations for your account's limits. The client already retries with adaptive backoff. | Run `scripts/drive.py` with a lower `--concurrency`, for example `--concurrency 3`. Send the claims that failed with another run; `--count` and `--offset` choose how many and where in the scenario list to start. |
| `scripts/drive.py` exits with code 1 before it sends anything | You did not type `yes` at the confirmation prompt, or the prompt had no terminal to read from. | Type `yes`, or pass `--yes`. |
| Invocation fails with a validation error about the session ID | The runtime requires a session ID of at least 33 characters. `scripts/drive.py` builds `ci-<scenario>-<uuid>` and cuts it to 64 characters. A longer prefix or a changed format can fall outside that range. | If you add scenarios or change the prefix, keep the full ID between 33 and 64 characters. |
| Each claim takes a minute or more | Each claim is a real run of both agents, with model calls and tool calls. | Expected. In our run, 100 claims took about 2 to 3 minutes at `--concurrency 20`. |

## No Insights yet

An empty list does not mean something is broken. Failure Analysis runs on each finished session first. Insights appear only after enough failed sessions have accumulated (about 20). The `scripts/status.py` line `none yet. Insights appear after enough failed sessions...` is this case.

| Check | What to look for |
|---|---|
| `scripts/status.py`: `Insight Store` | `ACTIVE / ENABLED`. If not, run `scripts/setup_insights.py`. |
| `scripts/status.py`: `Online evaluation` | `ACTIVE`. If it shows `not created`, run `scripts/setup_insights.py`. If it shows a failure reason in parentheses, see the `Config ERROR` row above. |
| `scripts/status.py`: `Sessions evaluated` | The number with failures should climb toward the threshold of about 20 failed sessions. If both numbers are 0, nothing has been evaluated yet. A session is evaluated only after it has been idle for the configured timeout, and only if it started after the config existed. Wait a few minutes after `scripts/drive.py` finishes, then check again. |
| How much failing traffic you sent | Send comfortably more than the threshold (about 20 failed sessions) in one run, not exactly the threshold. |
| `scripts/export_insights.py` writes `0 active Insight(s)` | The list was empty at that moment. Use `--archive-status ARCHIVED` to see Insights you archived. |

## Export

| Symptom | Cause | Fix |
|---|---|---|
| `FAILURES.md` shows span IDs instead of a tool name and exception, and the script prints `Resolved <n> of <m> failing span(s)` with `n` lower than `m` | The span lookup in CloudWatch Logs found no record for some IDs. | Check that CloudWatch Transaction Search is on, because it writes the records in `aws/spans`. Check that the failing sessions are inside the lookback window and raise `--lookback-days` (default 7) if not. Check that your credentials allow `logs:StartQuery` and `logs:GetQueryResults` on `aws/spans` and on the runtime log group. |
| `warning: span lookup <status>; findings will show span ids only` | A CloudWatch Logs Insights query ended as `failed`, `cancelled` or `timeout`. | Run `scripts/export_insights.py` again. |

## Apply the patches

These rows do not come from script messages. They cover the `patch -p1` command in [README step 6](../README.md#walkthrough), which applies the reference patch `patches/claims-agent-insight-fixes.patch` to a copy of the claims agent.

| Symptom | Cause | Fix |
|---|---|---|
| `patch: **** Can't open patch file` | The path after `<` is wrong. | Use the path to the file in this repository's `patches/` folder. |
| `Hunk #n FAILED` or `can't find file to patch` | You ran `patch` outside the claims agent folder, the folder is not the upstream claims agent, or your coding assistant already changed the same lines. | Run `patch -p1 --dry-run < <path-to>/patches/claims-agent-insight-fixes.patch` from the folder that holds `app/` and `lambdas/`. If your coding assistant already changed the same files, compare the patch with its diff and apply only the changes you want by hand. |
| `Reversed (or previously applied) patch detected!` | The patch is already in your working tree. | Skip it. |

## Verify the fix

| Symptom | Cause | Fix |
|---|---|---|
| Exit code 2 with `No post-baseline sessions found in the session log. Redeploy, then run drive.py first.` | `scripts/verify_fix.py` counts the sessions that `scripts/drive.py` logged in `sessions_<name>.jsonl` after the baseline was exported. It found none. You may have replayed before you exported the baseline, or run `scripts/drive.py` on another machine. | Export the baseline, apply your changes, redeploy, then run `scripts/drive.py`. If the replay ran earlier, pass `--since <ISO time>` with the time it started. |
| Exit code 2 with `INCONCLUSIVE: the evaluator has not covered the new sessions yet.` and lines like `evaluated 12/50 post-fix sessions; waiting ...` | The online evaluation has not evaluated every replayed session yet. A quiet Insight list is not evidence of a fix until it has. | Run `scripts/verify_fix.py` with `--wait-minutes 25` (or more). It polls once a minute until the count catches up. |
| `KeyError: 'online_config_id'` | The state file has no config ID because `scripts/setup_insights.py` has not finished for this `--name`. | Run `setup_insights.py --name <name>` until it prints `Continuous Insights is on`. |
| Exit code 1 with `STILL FAILING` or `NEW` lines | An Insight's `affectedSessionCount` or `lastSeenAt` moved after the baseline, or an Insight appeared that was not in the baseline. | Read the lines. Confirm the redeployed code is the code you changed, then export again and compare the new `FAILURES.md` with the old one. |

## Clean up

| Symptom | Cause | Fix |
|---|---|---|
| `Nothing recorded to delete: no evaluation config or role in the state file.` (`scripts/cleanup.py`) | `scripts/setup_insights.py` did not save a config ID or role name for this `--name`. | Nothing to do, or run `scripts/setup_insights.py` first if you expected resources. |
| `skip <resource> (already gone)` | The resource was deleted earlier. | None. |
| `FAILED <resource>: <code>: <message>` on stderr | One step failed; the others still run. | Fix the permission or state named in the message and run `scripts/cleanup.py` again. |
| The Agent Insight Store, Transaction Search and the claims agent are still there | `scripts/cleanup.py` removes only what `scripts/setup_insights.py` created: the online evaluation config and the evaluation role. Add `--delete-logs` to also delete the config's results log group. The Store and Transaction Search are account-wide, and disabling the Store deletes stored Insights for every application in the account. | Remove the claims agent with its own destroy script. Change the Store and Transaction Search only if nothing else in the account uses them. |

To start over for a name: `python scripts/cleanup.py --name <name> --delete-logs --yes`, delete the local `agent_state_<name>.json`, then run `scripts/register_agent.py` and `scripts/setup_insights.py` again.
