# Stream a grounded answer from a Strands Runtime

First check [raw retrieval](../01-raw-mcp/). This optional Runtime discovers tools on that IAM-authenticated Gateway but passes only `knowledge___Retrieve` to its Strands agent. The Gateway URL and model ID are operator configuration, not request fields. The agent responds to `{"prompt":"..."}` with SDK server-sent text events.

Use a dedicated sandbox; verify the account and set `AWS_REGION`, `REPO`, `STACK`, `RUNTIME_NAME`, `MODEL_ID`, `GATEWAY_URL`, and `ROLE_PATH_AND_NAME`. Bring a private ECR repository and prepared Runtime execution role. In addition to image pull and log permissions, grant the role `bedrock-agentcore:InvokeGateway` on **only** the selected Gateway and Bedrock inference for the chosen model/profile and required destination models. It does not need KB administration or direct KB retrieval. Do not supply tokens, model API keys or secret parameters.

From this directory:

```bash
set -euo pipefail
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
TAG="kb-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false --push -t "$REGISTRY/$REPO:$TAG" .
DIGEST=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$REPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://runtime.yaml --parameters \
  ParameterKey=RuntimeName,ParameterValue="$RUNTIME_NAME" ParameterKey=RepositoryName,ParameterValue="$REPO" \
  ParameterKey=ImageDigest,ParameterValue="$DIGEST" ParameterKey=RolePathAndName,ParameterValue="$ROLE_PATH_AND_NAME" \
  ParameterKey=BedrockModelId,ParameterValue="$MODEL_ID" ParameterKey=GatewayUrl,ParameterValue="$GATEWAY_URL" \
  --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
ARN=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_ID" --query 'Stacks[0].Outputs[?OutputKey==`RuntimeArn`].OutputValue' --output text)
printf '%s\n' '{"prompt":"Summarize the demo warranty and cite the source."}' > prompt.json
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$ARN" --region "$AWS_REGION" --payload fileb://prompt.json response.sse
```

Keep the full stack ID. Use a fresh name for each new stack and an immutable image tag; stop on collisions. To clean up, delete the **recorded Runtime stack** and wait for `stack-delete-complete` before deleting the separate Gateway stack. Remove only images, logs and files you own. Keep the imported role, repository and KB intact. For older deployments follow the ownership notes in the [parent guide](../README.md).
