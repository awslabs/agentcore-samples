# Evaluate coding-agent tool policies with Cedar

Before attaching tools to a coding agent, check which requests your policies actually allow. This local lab uses the Cedar authorization engine to evaluate the four [policy files](policies/) against synthetic AgentCore Gateway requests. No AWS account, model, CLI, API key, shell tool or cloud resources are needed. **The request is data; no command or tool is executed.**

## Run the lab

From this directory with Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python evaluate.py
python -m unittest -v test_evaluate
```

The two reads in `evaluate.py` have the same principal, Gateway and tool name. Only the path changes: `/workspace/src/example.py` is allowed, while `/other/example.py` is denied because no permit matches. `npm test` matches an exact command permit. The HTTP tool is explicitly forbidden. Try changing the command or resource in a test. A matching forbid wins even if another policy permits the request.

| Policy | What to inspect |
|---|---|
| `file_access.cedar` | Three file actions; a workspace path prefix on one Gateway |
| `command_execution.cedar` | Exact command permits and explicit command forbids |
| `tool_access.cedar` | Prohibition on the API action |
| `tool_restrictions.cedar` | Prohibitions on code execution, retrieval and HTTP actions |

`evaluate.py` binds `<gateway_arn>` to a fixed synthetic resource, parses the policies, and submits the principal, action, resource and `context.input` to Cedar. It distinguishes a default deny from a denial caused by a matching forbid; evaluation errors are reported, not counted as successful denials. This exercises policy semantics, **not** a deployed Gateway schema, authentication or enforcement. Path matching is a string check: it does not resolve filesystem paths or symlinks. Real tools still need identity, input validation and isolation.

## If you need a deployed example

See [AgentCore Policy samples](../../../01-features/07-centralize-and-govern-your-ai-infrastructure/02-policy/) for service-side policy integration and [Lambda targets for Gateway](../../../01-features/07-centralize-and-govern-your-ai-infrastructure/01-gateway/01-attach-targets/mcp/aws-lambda/) for target setup. Neither is a drop-in replacement for the old interactive coding-agent demo.

The [previous deployment walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/03-coding-assistants/securing-coding-agents) provisioned a Gateway, Policy Engine, Cognito and Lambda targets and included an optional model-driven demo. This lab replaces that deployment, not existing resources. If you deployed it, use your original account, Region and resource inventory to verify ownership before cleanup. Do not delete a shared policy engine, pool, role or target just because its name matches; the old cleanup helper removes policies and other child resources.
