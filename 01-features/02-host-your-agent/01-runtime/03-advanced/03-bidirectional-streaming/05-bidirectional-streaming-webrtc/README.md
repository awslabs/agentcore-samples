# WebRTC voice agent with Kinesis Video Streams and Nova Sonic

Speak into a browser microphone and receive audio back over WebRTC. FastAPI handles signaling; `aiortc` and `av` convert incoming audio to 16 kHz PCM and return 24 kHz audio from Nova Sonic. Kinesis Video Streams (KVS) supplies ICE/TURN servers. The browser can signal a local Python process or an IAM-authenticated AgentCore Runtime V2. The media path is the same.

## Prerequisites

Use a sandbox account and a Region supporting Nova Sonic and Runtime V2. Bedrock inference, KVS, ECR, Runtime, NAT gateways and network traffic can incur charges. Create a dedicated `SINGLE_MASTER` KVS signaling channel in that Region, or get permission from the owner of an existing channel. The agent describes the named channel; it never creates or deletes one.

Use Python 3.12, AWS CLI and a short-lived AWS profile. Don't put credentials in source, a Docker build context or the browser. Start from this directory:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
export AWS_PROFILE=my-sandbox AWS_REGION=us-west-2
export KVS_CHANNEL_NAME=my-webrtc-channel
aws sts get-caller-identity
```

## Run locally

Install `agent/requirements.txt` in the active environment. In one terminal run `python agent/bot.py`; in another run `python server/server.py`. Open **http://localhost:7860**, allow microphone access, and click **Connect**. The agent uses your AWS credential chain for the selected KVS channel and model. Local tests with real speech still make billable AWS calls.

## Run on AgentCore Runtime V2

The deploy script creates **one** Runtime. Bring an execution role, private ECR repository and image, signaling channel, private subnets and security group; none of them is modified by the deployer. This replaces automatic CodeBuild, ECR and role provisioning. An existing build pipeline can supply the image instead of local Docker.

1. Prepare the role to [run an AgentCore container](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html), scoped to the image repository and runtime. The trust principal is `bedrock-agentcore.amazonaws.com`; constrain its source account and Runtime ARN. Add the channel actions in [kvs-iam-policy.json](kvs-iam-policy.json) and the model action in [bedrock-iam-policy.json](bedrock-iam-policy.json), replacing the uppercase placeholders with the actual values. The foundation-model ARN has an empty account field. Apply permissions **before** creation: the container looks up KVS at startup. Use a profile authorized to pass only this role and to create, tag, read and delete the sample Runtime. The browser server's profile only needs invocation of the chosen Runtime for the hosted demo.
2. Select **private subnets with NAT egress** and a security group allowing outbound traffic to KVS TURN servers. PUBLIC mode does not provide the outbound UDP needed here. Reuse operator-approved networking rather than changing routes or security groups in the helper.
3. Build the `agent/` Dockerfile for Linux ARM64. Example from this directory, with `ECR_REPO` set to an existing repository name. Choose a fresh tag in an immutable repository. The documented example assumes commercial AWS.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
TAG="webrtc-$(date -u +%Y%m%dT%H%M%SZ)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false --push -t "$REGISTRY/$ECR_REPO:$TAG" agent/
DIGEST=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$ECR_REPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
```

4. Set `ROLE_ARN`, `SUBNET_ID` and `SG_ID` to your prepared resources. Use boto3 1.43.95 or newer for the `platformVersion` field. The script checks the caller's account, requests VPC/HTTP/V2 and polls until the service reports V2 and READY. It does not update a same-name Runtime.

```bash
python deploy.py --region "$AWS_REGION" --account "$ACCOUNT" create \
  --image "$REGISTRY/$ECR_REPO@$DIGEST" --role "$ROLE_ARN" --channel "$KVS_CHANNEL_NAME" \
  --subnets "$SUBNET_ID" --security-groups "$SG_ID"
```

5. Start `python server/server.py --runtime-arn <ARN-from-create>` and open **http://localhost:7860**. Keep this single-user server on loopback; don't expose it through a tunnel or reverse proxy. It signs signaling requests for the one ARN selected at launch. The browser never needs AWS keys. Click **Force TURN only** to exercise the relay rather than a direct media connection.

## Cleanup and recovery

```bash
python deploy.py --region "$AWS_REGION" --account "$ACCOUNT" cleanup
```

Keep `webrtc-runtime.json` until deletion completes. Cleanup verifies caller account, Region, exact Runtime ARN, name and deployment tag, requests deletion by ID and waits for absence. A timeout or tag mismatch retains the receipt. If create ended without a response, the incomplete receipt contains a unique tag value: inspect the service in the recorded account and Region, reconcile by tag and creation history, and remove the receipt only after confirming no owned resource remains. Do not delete or update a resource just because the name matches. For an older deployment, use its original ID and ownership records instead; this helper will not adopt it.

Stop the local UI and agent. Separately remove **only** images, roles, channels, log groups and networking you created exclusively for the demo. Wait for Runtime deletion before removing subnets, and remember that a dedicated NAT gateway bills even while idle. Keep shared resources intact.

## Troubleshooting

- **Runtime creation fails:** inspect `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`, the role trust and ECR pull permissions. Check KVS name, Region and channel permissions; missing startup dependencies prevent readiness.
- **No audio:** check microphone permission, outbound UDP, model access and TURN reachability. Signaling success by itself does not prove media flowed.
- **Local source:** [upstream WebRTC speech-to-speech example](https://github.com/aws-samples/sample-nova-sonic-speech2speech-webrtc).
