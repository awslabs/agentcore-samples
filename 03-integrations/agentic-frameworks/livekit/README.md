# AI Video Interview Agent

An AI-powered video interview system using LiveKit and Amazon Bedrock Nova Sonic for real-time voice interviews based on behavioral competencies.

## Architecture

- **AWS Bedrock AgentCore**: Serverless runtime for interview agent
- **AgentCore Gateway**: OAuth-secured MCP gateway for Lambda invocation
- **Amazon Cognito**: M2M authentication for gateway access
- **Amazon Bedrock Nova Sonic**: Real-time voice AI (TTS + conversation)
- **LiveKit**: Real-time audio/video communication
- **AWS Lambda**: Interview evaluation with Claude Sonnet
- **Amazon S3**: Transcript and feedback storage
- **React UI**: Web-based interview interface with video panels
- **Docker**: Containerized UI services

## Features

- Real-time voice interviews with AI agent
- Live video feed of candidate with animated AWS logo
- 4 behavioral competencies coverage (random order)
- 2 follow-up questions per principle
- Automatic transcript storage in S3 and AgentCore Memory
- AI-powered interview evaluation using Claude Sonnet
- PDF feedback report generation
- Modern React UI with secure HTTPS access

## Deployment

This application can be deployed directly to an AWS EC2 instance with Docker installed.

### EC2 Requirements

- Instance type: t3.medium or larger recommended
- OS: Amazon Linux 2023 or Ubuntu 22.04
- Docker and Docker Compose installed
- Security group ports: 8443 (HTTPS)
- IAM role or credentials with Bedrock access (Nova Sonic and Claude Sonnet)

## Quick Start

### Prerequisites

- AWS Account with Bedrock access (Nova Sonic and Claude Sonnet models)
- LiveKit account and credentials
- Docker and Docker Compose
- EC2 instance (if deploying to AWS)

### Setup

1. **Configure environment:**

```bash
cp .env.example .env

# Edit .env with your credentials
```

```bash
mkdir -p ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/nginx.key -out ssl/nginx.crt \
  -subj "/CN=localhost"
```

1. **Deploy Lambda function and AgentCore Gateway:**

```bash
# See Setup Instructions section below
```

1. **Start UI services:**

```bash
docker-compose -f docker-compose.ui.yml up -d --build
```

1. **Access interview:**

```
https://YOUR_PUBLIC_IP:8443
```

For EC2 deployment, use your instance's public IP address.

## Configuration

### Environment Variables (.env)

Required:

- `LIVEKIT_URL`: LiveKit WebSocket URL
- `LIVEKIT_API_KEY`: LiveKit API key
- `LIVEKIT_API_SECRET`: LiveKit API secret
- `AWS_REGION`: AWS region
- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `NOVA_SONIC_MODEL_ID`: Nova Sonic model ID (default: amazon.nova-sonic-v1:0)
- `PUBLIC_IP`: Your EC2 public IP

### Interview Topics

Configured in `data/leadership_principles.json`:

1. Teamwork
2. Problem Solving
3. Communication
4. Adaptability

## Architecture Details

### Services

**AgentCore Runtime** (AWS Bedrock)

- Serverless interview agent with Nova Sonic
- Handles interview logic and conversation flow
- Stores transcripts in S3 and AgentCore Memory
- Invokes Lambda via AgentCore Gateway using OAuth authentication

**AgentCore Gateway** (AWS Bedrock)

- MCP protocol gateway for secure Lambda invocation
- OAuth 2.0 client credentials flow via Cognito
- Tool naming: `{target-name}___{tool-schema-name}`
- Validates tokens using `allowedClients` configuration

**Lambda Function** (AWS Lambda)

- Evaluates interview using Claude Sonnet 4.5
- Generates PDF feedback report with fpdf2
- Saves JSON and PDF to S3

**Token API** (Port 8001)

- Generates LiveKit access tokens
- Invokes AgentCore runtime
- Provides feedback retrieval endpoint

**Interview UI** (Port 3000)

- React-based candidate interface
- Video panels: candidate camera + AWS logo
- LiveKit audio/video components

**Nginx** (Port 8443)

- HTTPS proxy for UI and API
- Required for browser microphone/camera access

### Interview Flow

1. Candidate enters name and joins room
2. AgentCore runtime starts and connects to LiveKit
3. Agent greets and explains interview format
4. Agent asks questions from 4 behavioral competencies (random order)
5. Agent asks 2 follow-up questions per principle
6. Transcript saved to S3 and AgentCore Memory in real-time
7. Interview ends when candidate disconnects
8. AgentCore fetches OAuth token from Cognito
9. AgentCore invokes Lambda via Gateway using MCP protocol
10. Lambda evaluates transcript using Claude Sonnet
11. PDF feedback report generated and saved to S3
12. Candidate views feedback PDF in browser

## Development

### Project Structure

```
ai-interview-agent/
├── src/
│   ├── agentcore/
│   │   ├── nova_realtime_agentcore.py # AgentCore runtime
│   │   ├── chat_history.py            # Transcript management
│   │   ├── config/
│   │   │   ├── gateway_config.json    # Gateway credentials (gitignored)
│   │   │   └── gateway_config.json.example # Config template
│   │   ├── Dockerfile                 # AgentCore container
│   │   └── requirements.txt           # Python dependencies
│   ├── lambda/
│   │   ├── evaluate_interview.py      # Lambda evaluation function
│   │   └── fpdf-layer.zip             # Lambda layer for PDF generation
│   ├── gateway/
│   │   ├── setup_gateway.py           # Gateway setup script
│   │   └── target_config.json         # Gateway configuration
│   ├── ui/                            # React interview UI
│   │   ├── App.js
│   │   ├── App.css
│   │   └── package.json
│   └── web/
│       └── token_api.py               # LiveKit token + AgentCore invocation
├── docker/
│   ├── Dockerfile.ui                  # UI container
│   └── Dockerfile.token-api           # Token API container
├── data/
│   └── leadership_principles.json     # Interview questions
├── docs/
│   └── PRESENTATION.md                # Technical presentation
├── ssl/                               # SSL certificates
├── docker-compose.ui.yml              # UI services compose file
├── nginx.conf                         # HTTPS proxy configuration
├── .env.example                       # Environment template
└── README.md
```

### Modifying Interview Behavior

Edit `src/agentcore/nova_realtime_agentcore.py`:

- Agent instructions (INTERVIEW_INSTRUCTIONS constant)
- Session timeout (SESSION_TIMEOUT constant)
- S3 bucket configuration (S3_BUCKET constant)

### Updating Gateway Credentials

Edit `src/agentcore/config/gateway_config.json`:

- Gateway credentials are loaded from JSON file at runtime
- File is automatically packaged with AgentCore deployment
- Keep credentials out of version control (file is gitignored)

### Adding Interview Topics

Edit `data/leadership_principles.json`:

```json
{
  "name": "Principle Name",
  "description": "Description",
  "questions": ["Question 1", "Question 2"]
}
```

## Monitoring

### Health Checks

```bash
# Token API

curl http://localhost:8001/health

# AgentCore Runtime

agentcore logs
```

### Logs

```bash
# UI services

docker-compose -f docker-compose.ui.yml logs -f

# AgentCore Runtime

agentcore logs --follow
```

## Setup Instructions

### 1. Deploy Lambda Function

```bash
cd src/lambda

# Create Lambda layer for fpdf2

aws lambda publish-layer-version --layer-name fpdf2-layer \
  --zip-file fileb://fpdf-layer.zip --compatible-runtimes python3.11

# Deploy Lambda function

zip function.zip evaluate_interview.py
aws lambda create-function --function-name EvaluateInterview \
  --runtime python3.11 --handler evaluate_interview.lambda_handler \
  --zip-file fileb://function.zip --role <LAMBDA_ROLE_ARN> \
  --layers <LAYER_ARN> --timeout 60 --memory-size 512

# Grant S3 and Bedrock permissions to Lambda role
```

### 2. Setup Cognito for OAuth Authentication

```bash
cd src/gateway
python setup_gateway.py

# Creates:

# - Cognito User Pool

# - Resource Server (interview-gateway) with scope 'invoke'

# - M2M Client with client credentials flow

# - AgentCore Gateway with Lambda target

# Note: Update Lambda ARN in setup_gateway.py before running
```

### 3. Configure Gateway Credentials

```bash
cd src/agentcore

# Copy example config and edit with your credentials

cp config/gateway_config.json.example config/gateway_config.json

# Edit config/gateway_config.json with values from setup_gateway.py:

# - gateway_url: Gateway MCP endpoint URL

# - client_id: Cognito M2M client ID

# - client_secret: Cognito M2M client secret

# - token_url: Cognito OAuth token endpoint

# - scope: interview-gateway/invoke

# Note: This file is gitignored to protect credentials
```

### 4. Deploy AgentCore Runtime

```bash
cd src/agentcore

# Configure AgentCore CLI

agentcore configure

# Update .bedrock_agentcore.yaml with Gateway ARN

agentcore launch
```

### 5. Deploy UI Services

```bash
docker-compose -f docker-compose.ui.yml up -d --build
```

## Troubleshooting

### Common Issues

**Microphone/Camera not working**

- Ensure accessing via HTTPS (not HTTP)
- Check browser permissions

**Agent not joining**

- Check AgentCore logs: `agentcore logs`
- Verify AWS credentials and Bedrock access
- Confirm Nova Sonic model availability in region

**Feedback not generating**

- Check Lambda logs in CloudWatch
- Verify S3 bucket permissions
- Ensure Lambda has Bedrock access for Claude Sonnet
- Check AgentCore logs for gateway invocation errors
- Verify OAuth token generation from Cognito
- Confirm gateway has `lambda:InvokeFunction` permission

**Connection failed**

- Verify LiveKit credentials in .env
- Check network connectivity to LiveKit server

## Cost Estimation

**Per Interview (30 minutes):**

- AgentCore Runtime: ~$0.10
- Nova Sonic: ~$0.45 (30 min audio)
- Claude Sonnet Evaluation: ~$0.02
- Lambda Execution: ~$0.001
- S3 Storage: <$0.001
- **Total: ~$0.57 per interview**

**Monthly (100 interviews):**

- Total: ~$57/month

## Known Limitations

- Nova Sonic Realtime API does not expose transcription events
- Transcripts captured via chat context monitoring
- Maximum session length: 1 hour (configurable)
- AgentCore doesn't support environment variables via YAML - gateway credentials loaded from JSON config file instead

## Gateway Integration Details

### OAuth Flow

1. AgentCore runtime requests token from Cognito using client credentials
2. Cognito validates client ID/secret and returns access token
3. AgentCore invokes gateway with token in Authorization header
4. Gateway validates token using `allowedClients` configuration
5. Gateway invokes Lambda function and returns result

### Tool Naming Convention

Gateway tools use format: `{target-name}___{tool-schema-name}`

- Target name: `evaluate-interview`
- Tool schema name: `evaluate_interview`
- Full tool name: `evaluate-interview___evaluate_interview`

### Required IAM Permissions

- **AgentCore Runtime Role**: `bedrock-agentcore:InvokeGateway`
- **Gateway Role**: `lambda:InvokeFunction`
- **Lambda Role**: `bedrock:InvokeModel`, `s3:PutObject`, `s3:GetObject`

## Contributing

Contributions welcome! Please open an issue or PR.

## License

MIT
