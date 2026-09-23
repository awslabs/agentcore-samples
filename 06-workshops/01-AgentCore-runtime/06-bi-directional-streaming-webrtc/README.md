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
  index.html          - Browser client (WebRTC + optional AgentCore Runtime)
  server.py           - Static file server
  requirements.txt
kvs-iam-policy.json     - Minimal IAM policy for KVS
bedrock-iam-policy.json - Minimal IAM policy for Nova Sonic
```

## Requirements

- **Python 3.12+** (required for aws-sdk-bedrock-runtime)
- AWS credentials configured
- **VPC with internet egress** for AgentCore Runtime deployment (see setup below)

## VPC Setup for AgentCore Runtime

The agent needs internet egress to reach KVS TURN servers for WebRTC connectivity. If you already have a VPC with a private subnet that has NAT gateway access, skip to [Deploying to AgentCore Runtime](#deploying-to-agentcore-runtime).

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

You'll use these in the `agentcore configure` step below.

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

### 1. Install the starter toolkit

```bash
pip install bedrock-agentcore-starter-toolkit
```

### 2. Configure

From the `agent/` directory:

```bash
cd agent

export SUBNET_IDS=subnet-0123456789abcdef0  # private subnet (with NAT gateway for internet egress)
export SECURITY_GROUP_ID=sg-0123456789abcdef0

agentcore configure \
  -e bot.py \
  --deployment-type container \
  --disable-memory \
  --vpc \
  --subnets $SUBNET_IDS \
  --security-groups $SECURITY_GROUP_ID \
  --non-interactive
```

VPC network mode is required because PUBLIC network mode does not support outbound UDP connectivity. 

### 3. Deploy

```bash
agentcore deploy --env KVS_CHANNEL_NAME=voice-agent-minimal --env AWS_REGION=us-west-2
```

This builds an ARM64 container via CodeBuild (no Docker required locally) and deploys it to AgentCore Runtime. Note the ARN in the output.

### 4. Attach IAM permissions

The execution role created by the toolkit needs KVS and Bedrock permissions. First, update `ACCOUNT_ID` in `kvs-iam-policy.json` and `bedrock-iam-policy.json` with your AWS account ID. Then replace `ROLE_NAME` with the role name from the deploy output (e.g. `AmazonBedrockAgentCoreSDKRuntime-us-west-2-9d74932bdb`):

```bash
ROLE_NAME=ROLE_HERE

aws iam put-role-policy \
  --role-name $ROLE_NAME \
  --policy-name kvs-access \
  --policy-document file://kvs-iam-policy.json

aws iam put-role-policy \
  --role-name $ROLE_NAME \
  --policy-name bedrock-nova-sonic \
  --policy-document file://bedrock-iam-policy.json
```

### 5. Test

Enter the agent ARN output from `agentcore deploy` in the browser client at `http://localhost:7860` along with AWS credentials, then click Connect. Once connected, speak into your microphone — the agent will respond with spoken audio in real time.

### Cleanup

```bash
agentcore destroy
```

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

The agent needs KVS permissions for TURN server access. See `kvs-iam-policy.json` for the minimal policy — replace `ACCOUNT_ID` with your AWS account ID.

Additionally, the agent needs `bedrock:InvokeModelWithBidirectionalStream` permission for the Nova Sonic model.

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

## Reference

Based on: https://github.com/aws-samples/sample-nova-sonic-speech2speech-webrtc
