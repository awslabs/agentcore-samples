# Authorize a simulated refund with AgentCore Gateway Policy

This CDK blueprint connects a JWT-authenticated Runtime to a JWT-authenticated Gateway in the **same trust boundary**. Cognito supplies the access token and group claim. The Runtime restricts the agent to Gateway tools and scopes Memory to the authenticated subject. The Gateway starts in **ENFORCE** mode: before a permit exists, the default is deny. No general-purpose shell, editor or filesystem tools are enabled.

The Lambda targets contain fictional customers, orders and in-memory refund records. This is **not** a payment processor or per-customer authorization system. Authenticated demo users can read all fixture orders. The refund permit applies only when the serialized `cognito:groups` tag is exactly `["standard"]` and `amount_cents` is an integer between 1 and 10000 inclusive. Users with no group, a different group, or multiple groups do not match. No real financial data or payment credentials belong here. The order handler also rejects values outside that range. A model response alone is not evidence of a Gateway policy decision.

## Prepare and inspect

Use a dedicated, previously CDK-bootstrapped commercial AWS sandbox with Runtime, Memory, Policy, Gateway, Cognito and access to the configured Claude Sonnet 4.5 inference profile. These services and model calls can incur charges. Install Docker with ARM64 build support, Node.js/npm, AWS CLI, Python 3.13 and uv. Use a short-lived sandbox profile. Do not deploy from a shared or production account. From this blueprint directory:

```bash
uv sync --frozen --group test
(cd cdk && npm ci --ignore-scripts && npm run build && npm run cdk -- synth --all --quiet)
uv run --frozen --group test python -m unittest discover -s test -v
```

Review the synthesized templates in `cdk/cdk.out`. Confirm one Gateway with ENFORCE, an engine and its two policies, two Lambda targets, a user pool, scoped execution roles, Memory, the Runtime and its ARM64 image. Tests evaluate the synthesized Cedar against fixture principals, not a live service. CDK uses its existing bootstrap ECR repository; do not empty that shared repository.

## Deploy once to a fresh sandbox

The CDK app uses fixed stack and service names. **Stop on an existing stack or resource; do not adopt or update an old deployment.** Ensure no other deployment is in progress. In Bash set `ACCOUNT` and `AWS_REGION` explicitly, then verify both stack names before deploying. Save the full IDs returned after creation.

```bash
set -euo pipefail
[[ $(aws sts get-caller-identity --query Account --output text) == "$ACCOUNT" ]]
existing=$(aws cloudformation list-stacks --region "$AWS_REGION" --output json)
python3 -c 'import json,sys; names={"supportAgentDemo-AgentCoreStack","supportAgentDemo-DockerImageStack"}; assert not any(s["StackName"] in names and s["StackStatus"]!="DELETE_COMPLETE" for s in json.load(sys.stdin)["StackSummaries"]), "stack name already in use"' <<< "$existing"
(cd cdk && AWS_DEFAULT_REGION="$AWS_REGION" npm run cdk -- deploy --all --region "$AWS_REGION")
AGENT_STACK=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name supportAgentDemo-AgentCoreStack --query 'Stacks[0].StackId' --output text)
IMAGE_STACK=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name supportAgentDemo-DockerImageStack --query 'Stacks[0].StackId' --output text)
```

The preflight is not a lock against concurrent deployers. A deployment failure may leave partially created resources; inspect CloudFormation in the selected account/Region. Do not retry against an unknown stack. CDK does not need to invoke the model to deploy.

## Invoke as a demo user

In the **stack-created** Cognito pool, have the sandbox administrator create a synthetic user and assign *only* the `standard` group. Use the pool from the stack's `UserPoolId` output. Do not reuse personal customer identities. Choose a second synthetic user without that group to compare denials. In a local desktop session:

```bash
uv run --frozen python scripts/chat.py --account "$ACCOUNT" --region "$AWS_REGION" --stack-id "$AGENT_STACK"
```

Sign in at the hosted Cognito page. The client uses authorization code + PKCE and a fresh state on a loopback callback, requests separate Runtime/Gateway scopes and sends only the access token to the selected Runtime. The Runtime forwards that bearer only to its configured regional Gateway. The token is not printed, copied to clipboard, exported or stored on disk by this script. Each chat process uses a new Runtime session ID. Ask for a synthetic order and try a $10 refund (`1000` cents), then a $101 refund (`10100` cents). Verify the actual tool/policy outcome; do not disable enforcement to troubleshoot. A larger refund is not meant to work in any group.

## Cleanup and migration

Exit chat and sign out of the Cognito hosted login in your browser. Verify account, Region and both saved **full stack IDs**. Delete the application stack first, wait, then delete the image stack:

```bash
aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$AGENT_STACK"
aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$AGENT_STACK"
aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$IMAGE_STACK"
aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$IMAGE_STACK"
```

Review retained logs, Cognito resources and bootstrap assets under their original ownership and retention policy. The [previous walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/05-blueprints/customer-support-agent-with-agentcore) generated Toolkit YAML and enabled policy manually after running unrestricted tools. Its state, roles and assets are not imported by this version. This is a fresh-deployment example, not an in-place migration.
