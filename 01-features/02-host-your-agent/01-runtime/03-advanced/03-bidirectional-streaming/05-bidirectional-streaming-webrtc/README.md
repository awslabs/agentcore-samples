# Minimal WebRTC Voice Agent with KVS

Minimal example demonstrating WebRTC audio streaming with AWS Nova Sonic.

## Project Structure

```
agent/
  bot.py              - FastAPI server, WebRTC offer/answer, ICE handling
  kvs.py              - KVS signaling channel and TURN server helpers
  audio.py            - Audio resampling (av) and WebRTC output track (av.AudioFifo)
  nova_sonic.py       - Nova Sonic bidirectional streaming session
  requirements.txt
  Dockerfile
  .env.example
server/
  index.html          - Browser client (WebRTC + optional AgentCore runtime)
  server.py           - Static file server
  requirements.txt
deploy.py               - Builds the image and deploys to AgentCore Runtime V2 (VPC mode)
kvs-iam-policy.json     - Minimal IAM policy for KVS
bedrock-iam-policy.json - Minimal IAM policy for Nova Sonic
```

## Requirements

- **Python 3.12+** (required for aws-sdk-bedrock-runtime)
- AWS credentials configured
- **VPC with internet egress** for AgentCore runtime deployment (see setup below)

## VPC Setup for AgentCore runtime

The agent needs internet egress to reach KVS TURN servers for WebRTC connectivity. If you already have a VPC with a private subnet that has NAT gateway access, skip to [Deploying to AgentCore runtime](#deploying-to-agentcore-runtime).

### 1. Create a VPC with public and private subnets

1. Open the [VPC console](https://console.aws.amazon.com/vpc/)
2. Click **Create VPC**
3. Select **VPC and more**
4. Set a name (e.g. `webrtc-bot-example`)
5. Keep the default CIDR (`10.0.0.0/16`)
6. Set **Number of Availability Zones** to **1**
7. Set **Number of public subnets** to **1**
8. Set **Number of private subnets** to **1**
9. Set **NAT gateways** to **In 1 AZ**
10. Click **Create VPC**

### 2. Note the IDs

From the VPC console, copy:
- **Private subnet ID** (e.g. `subnet-0123456789abcdef0`) — this is where the agent runs
- **Security group ID** — the default security group created with the VPC (e.g. `sg-0123456789abcdef0`)

You'll pass these to `deploy.py` as `--subnets` and `--security-groups` below.

## Local Setup

### 1. Agent

```bash
cd agent
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your AWS credentials
python bot.py          # http://localhost:8080
```

### 2. Server

```bash
cd server
pip install -r requirements.txt
python server.py       # http://localhost:7860
```

### 3. Test

Open `http://localhost:7860` and click "Connect".

## Deploying to AgentCore Runtime

The agent runs on **AgentCore Runtime V2**. `deploy.py` builds the ARM64 container with CodeBuild (no local Docker needed), creates the execution role with the KVS and Bedrock permissions already attached, and then creates the runtime with `platformVersion="V2"`.

### 1. Install the deployment dependencies

```bash
pip install "boto3>=1.43.95" bedrock-agentcore-starter-toolkit PyYAML
```

`boto3>=1.43.95` is required to pass `platformVersion` on `CreateAgentRuntime`. An older version fails with `ParamValidationError`.

### 2. Deploy

From this directory, using the private subnet and security group from the VPC setup above:

```bash
export ACCOUNT_ID=123456789012

python deploy.py \
  --region us-west-2 \
  --subnets subnet-0123456789abcdef0 \
  --security-groups sg-0123456789abcdef0
```

The account ID must match the credentials you are deploying with -- it is substituted into the IAM policy resource ARNs, so a mismatch produces a runtime that starts but is denied access to KVS and Bedrock. `deploy.py` prints the account it is using before creating anything.

**VPC network mode is required.** PUBLIC network mode does not support outbound UDP, which WebRTC needs to reach the KVS TURN servers. The subnet must be private with internet egress through a NAT gateway.

**Expect this to take a while.** VPC mode adds network interface provisioning on top of the snapshot preparation, and how long that takes varies between runs. The script polls and prints status, so a long `CREATING` phase is normal rather than a hang.

The Agent ARN is printed at the end and saved to `setup_config.json`.

> **Note on IAM ordering.** `deploy.py` attaches the policies from `kvs-iam-policy.json` and `bedrock-iam-policy.json` to the execution role *before* creating the runtime. This ordering is required: Runtime V2 starts the container during creation, and `bot.py` calls `kvs.init()` at FastAPI startup, so a runtime created before those permissions exist fails with `AccessDeniedException` on `kinesisvideo:DescribeSignalingChannel`.

### 3. Test

Start the browser client:

```bash
cd server
pip install -r requirements.txt
python server.py       # http://localhost:7860
```

Open `http://localhost:7860`, paste the Agent ARN from the deploy output, then click Connect. Allow microphone access and speak -- the agent replies with spoken audio in real time.

### 4. Cleanup

Delete the runtime, then the VPC resources:

```bash
aws bedrock-agentcore-control delete-agent-runtime \
  --agent-runtime-id <runtime-id-from-setup_config.json> \
  --region us-west-2
```

Deleting a runtime is asynchronous and does not complete immediately. Wait for it to disappear before deleting the subnets -- a live runtime holds an elastic network interface in the subnet and the deletion will fail while it exists.

If you created a VPC for this sample, remember to delete the **NAT gateway** as well. It bills hourly whether or not the agent is running.

See [Troubleshooting](#troubleshooting) if the deployment does not reach `READY`.

## How It Works

### Audio Flow

**Browser → Nova Sonic:**
1. WebRTC captures microphone audio
2. `aiortc` receives audio frames on the agent
3. `av.AudioResampler` converts to 16kHz/16-bit/mono PCM
4. Base64-encoded and streamed to Nova Sonic

**Nova Sonic → Browser:**
1. Agent receives audio chunks from Nova Sonic
2. Raw PCM bytes buffered in `av.AudioFifo`
3. `OutputTrack` serves fixed-size 20ms frames to WebRTC
4. Browser plays audio via `<audio>` element

### Audio Configuration

| Parameter | Value |
|-----------|-------|
| Input Sample Rate | 16kHz |
| Output Sample Rate | 24kHz |
| Format | 16-bit PCM mono |
| Model | amazon.nova-2-sonic-v1:0 |
| Voice | matthew |

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `aws-sdk-bedrock-runtime` | Nova Sonic streaming (requires Python 3.12+) |
| `aiortc` | WebRTC peer connections |
| `av` | Audio resampling and frame buffering (FFmpeg) |
| `boto3` | KVS signaling channel and TURN servers |
| `fastapi` / `uvicorn` | HTTP server |

## IAM Permissions

The agent needs KVS permissions for TURN server access. See `kvs-iam-policy.json` for the minimal policy. Additionally, the agent needs `bedrock:InvokeModelWithBidirectionalStream` permission for the Nova Sonic model, in `bedrock-iam-policy.json`.

`deploy.py` attaches both policies to the execution role, substituting the `ACCOUNT_ID` placeholder with the account ID you supply, so these files do not need to be edited.

## Troubleshooting

**Python version error** (`Could not find aws-sdk-bedrock-runtime`):
Use Python 3.12+.

**Audio not working:**
- Check microphone permissions in browser
- Verify AWS credentials have Bedrock access
- Run agent with `-v` for verbose logging

**Connection fails:**
- Ensure both agent and server are running
- Check KVS IAM permissions
- Verify TURN server connectivity

**Deployment never reaches `READY`:**
Check the runtime's log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`:
- `build-logs-*` holds container output from when Runtime V2 created its snapshot
- `runtime-logs-*` holds container output during invocation
- **No container log stream at all** means provisioning failed before the container started -- check the execution role rather than the application code

**Deployment fails with `ParamValidationError` on `platformVersion`:**
Upgrade boto3. Runtime V2 requires `boto3>=1.43.95`.

## Reference

Based on: https://github.com/aws-samples/sample-nova-sonic-speech2speech-webrtc
