# Host a tool-free Claude Agent SDK request on Runtime

The Python Claude Agent SDK starts its bundled native CLI as a child process. This example shows how to package it in a Linux ARM64 image, use **Bedrock role credentials** rather than a model API key, and return `{"response":"..."}`. Each invocation gets an empty temporary working directory, isolated CLI configuration, one model turn and a two-minute deadline. No built-in tools, shell, filesystem tools, subagents or MCP servers are enabled. This is a text-generation example, not a coding sandbox. A system prompt alone is not an authorization boundary.

## Local check

From this directory with Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v test_agent
```

To invoke locally, set `BEDROCK_MODEL_ID` to a Claude model or inference profile you are authorized to use and select a temporary AWS profile and Region. Run `python agent.py` and POST `{"prompt":"Explain rainbows in one sentence"}` to `http://localhost:8080/invocations`. Real answers cost money. Do not set Anthropic API keys, OAuth tokens, or Bedrock bearer tokens for this sample.

## Deploy the container

Use a sandbox with Runtime and the selected Bedrock model available. Bring a private ECR repository and a prepared [Runtime execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html). Trust `bedrock-agentcore.amazonaws.com` with source account/Runtime restrictions. Scope pull permissions to your repository, logging to the Runtime, and Bedrock inference permissions to the chosen model or inference profile and its required destination models. No credentials are template parameters. Confirm account and Region with `aws sts get-caller-identity` before continuing.

Set `AWS_REGION`, `REPO`, `ROLE_PATH_AND_NAME`, `BEDROCK_MODEL_ID`, `STACK` and `RUNTIME_NAME`. Choose new stack and Runtime names; the template creates only the Runtime, never a repository or role. From this directory:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
TAG="claude-$(date -u +%Y%m%dT%H%M%SZ)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false -t "$REGISTRY/$REPO:$TAG" --push .
DIGEST=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$REPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://runtime.yaml \
  --parameters ParameterKey=RuntimeName,ParameterValue="$RUNTIME_NAME" ParameterKey=RepositoryName,ParameterValue="$REPO" \
  ParameterKey=ImageDigest,ParameterValue="$DIGEST" ParameterKey=RolePathAndName,ParameterValue="$ROLE_PATH_AND_NAME" \
  ParameterKey=BedrockModelId,ParameterValue="$BEDROCK_MODEL_ID" --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
ARN=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_ID" --query 'Stacks[0].Outputs[?OutputKey==`RuntimeArn`].OutputValue' --output text)
aws bedrock-agentcore invoke-agent-runtime --region "$AWS_REGION" --agent-runtime-arn "$ARN" --content-type application/json \
  --payload fileb://prompt.json answer.json
```

Create `prompt.json` containing a synthetic JSON prompt as above before invocation. The examples use commercial AWS, one new immutable image tag and an operator with permission to pass the specific role. Do not replace or adopt another deployment by name. Keep the full stack ID. On completion, verify your account and delete **only** that stack:

```bash
aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$STACK_ID"
aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
```

Review any logs and images you own separately; shared resources remain. The [old three-mode sample](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/claude-agent/claude-sdk) exposed read/write tools and returned raw SDK messages. Those modes, payload contract and Toolkit deployment are intentionally not retained.
