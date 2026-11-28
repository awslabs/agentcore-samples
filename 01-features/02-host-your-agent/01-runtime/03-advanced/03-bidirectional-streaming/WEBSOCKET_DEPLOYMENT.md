# Deploy one WebSocket voice agent

Samples 01–04 use a Linux ARM64 container and an IAM-authenticated AgentCore **Runtime V2** in PUBLIC network mode. The shared helper creates only that Runtime. Bring your own approved private ECR repository and execution role; it does not create a CodeBuild project, IAM role, Gateway, Cognito resources, or Memory. Sample 05 uses [a different VPC configuration for WebRTC](05-bidirectional-streaming-webrtc/).

From the bidirectional streaming tutorial root, use a sandbox in commercial AWS with Runtime V2 and the selected inference model available. Buildx with ARM64 support (or your own build pipeline), Python 3.12, the AWS CLI and `jq` are needed for this walkthrough. For Pipecat's browser client, also install Node and the dependencies in `04-pipecat-sonic-ws/client/` before starting it.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r utils/requirements.txt
export AWS_PROFILE=my-sandbox AWS_REGION=us-east-1
SAMPLE=01-bedrock-sonic-ws       # or the exact 02, 03, or 04 directory name
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
```

Inspect the account and Region. Set `ROLE_ARN` to the prepared [Runtime execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) and `ECR_REPO` to its authorized repository. Restrict the role trust to AgentCore in the selected account. Give it ECR pull and logging permissions and only the selected agent's service permissions: Nova Sonic bidirectional model invocation for 01, 02 (Nova mode) or 04; for 03, streaming Transcribe, Polly synthesis and invocation of the configured Nova text model/inference profile instead. No demo Gateway or Memory permissions are needed. The deployment profile needs permission to pass that role, create/tag/read/delete the sample Runtime, and (for local build and push) publish the image to the repository. Use an invocation-only profile for the browser signer if available. Never pass AWS or third-party model credentials to deployment or include them in the image.

```bash
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
TAG="voice-$(date -u +%Y%m%dT%H%M%SZ)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false --push -t "$REGISTRY/$ECR_REPO:$TAG" "$SAMPLE/websocket"
DIGEST=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$ECR_REPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
python utils/runtime_v2.py "$SAMPLE" --account "$ACCOUNT" --region "$AWS_REGION" create \
  --role "$ROLE_ARN" --image "$REGISTRY/$ECR_REPO@$DIGEST"
bash utils/start_client.sh "$SAMPLE"
```

Use a fresh tag in an immutable repository. The helper checks account, Region and private image digest, records the creation attempt, requests HTTP protocol (including the WebSocket route), and waits for READY and V2. It never updates a Runtime by name. Keep the browser and signer on loopback; a presigned URL grants temporary access. Do not share URLs, log audio or use real personal information in these demos. Actual streaming incurs service charges.

```bash
python utils/runtime_v2.py "$SAMPLE" --account "$ACCOUNT" --region "$AWS_REGION" cleanup
```

Keep `$SAMPLE/stream-runtime.json` until cleanup verifies the resource is gone. It checks caller account, Region, exact ARN, name and the deployment tag before deleting by ID. An incomplete receipt or missing tag requires manual reconciliation, not deletion by name. Remove only your own images, roles and log groups separately. Historical `setup_config.json`, Toolkit builds, roles, Memory, and Gateway resources are **not** adopted or deleted. If you previously deployed them, reconcile their original IDs and owners before cleanup. The old script could reuse names and remove other resources' policies.
