# Use a budget specialist as a tool on AgentCore Runtime

A Strands coordinator delegates a synthetic budget question to a second Strands agent. The specialist can call one deterministic tool, `split_budget`, which splits integer US cents into 50% needs, 30% wants and the remainder in savings. The split is an illustration, not individualized advice. No stock prices, private finances, external market feeds or arbitrary calculators are needed. Each request gets two fresh agents and both use the same approved model and published Bedrock guardrail.

## Run the offline example

Use Python 3.12 and a fresh environment here:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true python -m unittest -v test_budget
```

The models in the test speak the Strands event protocol: the coordinator calls the specialist, the specialist calls the real arithmetic tool, and both return text. This does not invoke Bedrock or test guardrail enforcement. To try actual inference locally, supply `BEDROCK_MODEL_ID`, `GUARDRAIL_ID` and a numeric `GUARDRAIL_VERSION`, set a temporary AWS profile/Region and run `python budget_agent.py`. POST `{"prompt":"Illustrate a 50/30/20 split for 10001 cents."}` to `/invocations` on localhost:8080. Never expose the local server publicly. Real invocations can incur multiple model calls.

## Deploy a JWT-protected Runtime

Bring a dedicated sandbox Cognito user pool/app client in the same Region, two reviewed Bedrock resources (model or profile, and **published** guardrail version), a private ECR repo and an execution role. The role needs trusted Runtime access to its image/logs, inference on the selected model or profile and destination models as needed, and permission to apply the selected guardrail. Provisioning/management of guardrails or Cognito is not required on this role. No role, pool, guardrail or image repository is adopted or modified by the template.

In Bash, set `AWS_REGION`, `REPO`, `STACK`, `RUNTIME_NAME`, `ROLE_PATH_AND_NAME`, `USER_POOL_ID`, `APP_CLIENT_ID`, `BEDROCK_MODEL_ID`, `GUARDRAIL_ID` and `GUARDRAIL_VERSION` to approved values. Verify the account and Region. Use a new stack/runtime name and immutable image tag:

```bash
set -euo pipefail
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
TAG="budget-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false --push -t "$REGISTRY/$REPO:$TAG" .
DIGEST=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$REPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://runtime.yaml --parameters \
  ParameterKey=RuntimeName,ParameterValue="$RUNTIME_NAME" ParameterKey=RepositoryName,ParameterValue="$REPO" \
  ParameterKey=ImageDigest,ParameterValue="$DIGEST" ParameterKey=RolePathAndName,ParameterValue="$ROLE_PATH_AND_NAME" \
  ParameterKey=UserPoolId,ParameterValue="$USER_POOL_ID" ParameterKey=AppClientId,ParameterValue="$APP_CLIENT_ID" \
  ParameterKey=BedrockModelId,ParameterValue="$BEDROCK_MODEL_ID" ParameterKey=GuardrailId,ParameterValue="$GUARDRAIL_ID" \
  ParameterKey=GuardrailVersion,ParameterValue="$GUARDRAIL_VERSION" --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
```

Use the `RuntimeArn` stack output and your approved Cognito-authenticated client to send the same JSON request to the Runtime; the result is `{"response":"..."}`. Never share user JWTs on command lines or log them. Review policy and model behavior in your sandbox; a successful response is not proof that every guardrail rule worked.

## Cleanup and historical workshop

Verify the account and full stack ID, then delete **only** that stack and wait for `stack-delete-complete`. Separately reconcile dedicated image versions and logs. Shared roles, guardrails, Cognito and ECR stay with their owners. The [historical three-notebook workshop](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/01-conversational-agents/finance-personal-assistant) also generated chart and market-analysis code, fetched market data, created a guardrail and provisioned authentication. Those are not part of this narrower example. Never delete a pre-existing guardrail just because the old workshop found it by name.
