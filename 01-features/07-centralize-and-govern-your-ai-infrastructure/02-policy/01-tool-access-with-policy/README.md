# Preview Cedar for an existing Gateway

Use the AgentCore control-plane API to generate proposed tool-access policies from a natural-language requirement. The Gateway supplies the tool context. Review the generated action names, resource scopes, conditions and findings before considering enforcement. This example **does not create a policy, attach an engine, invoke a Gateway tool, or delete policies**.

## Prerequisites

Use a sandbox in commercial AWS with a READY Gateway and an ACTIVE Policy Engine already attached. Get the account, Region and IDs from their owner. Use short-lived credentials for that account. The caller needs STS identity lookup and access to `GetGateway`, `GetPolicyEngine`, `StartPolicyGeneration`, `GetPolicyGeneration` and `ListPolicyGenerationAssets` for the selected resources. Policy authoring and Gateway update/delete permissions are not needed. AWS calls and generation can incur charges. Do not supply customer data, tokens or secrets as input.

From this directory with Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v test_preview  # offline modeled API requests
```

## Generate and review

Set `ACCOUNT`, `REGION`, `GATEWAY_ID` and `ENGINE_ID` to your approved resources. Describe a constraint on a tool that actually exists in that Gateway. For example, for an underwriting tool with a `coverage_amount` input:

```bash
printf '%s\n' 'Permit the application tool only for coverage amounts below 1000000.' > intent.txt
python preview.py --account "$ACCOUNT" --region "$REGION" \
  --gateway-id "$GATEWAY_ID" --engine-id "$ENGINE_ID" --intent-file intent.txt
```

The program checks the caller and the existing attachment, starts one generation job, prints its ID, waits up to five minutes, and lists **every page** of assets and findings. The JSON output is a draft, not an authorization decision. An empty draft list is not a successful policy validation. Review both job-level and asset-level findings; do not silently ignore warnings or install generated text without testing it against your intended permit and deny cases.

If a job is still running, use `--generation-id <id>` **instead of** `--intent-file` to read its result without submitting another job. If the start response was lost, inspect generation history in the selected engine before retrying. Starting the same intent again is a new job. Generation history stays with the existing engine; there is no local stack to tear down. Remove `intent.txt` and the virtual environment if no longer needed.

## Existing deployments

The [previous walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/07-centralize-and-govern-your-ai-infrastructure/02-policy) provisioned Lambda targets, Cognito, a Gateway and a Policy Engine, changed claims, installed policies and ran optional agent invocations. This preview is not a replacement for those enforcement tests. See [guardrails in policy](../02-guardrails-in-policy/) and [temporal policies](../03-temporal-policies/) for other service-side examples. Removing the old scripts does not remove deployed resources. Reconcile your original resource IDs, account, Region and ownership; do not delete a shared engine's policies, detach its Gateway, or remove shared identity resources as a reset step.
