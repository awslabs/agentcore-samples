# Getting Started — TypeScript Agent on Amazon Bedrock AgentCore

Deploy a TypeScript agent to AgentCore Runtime using Direct Code Deploy with Node.js 22.

## Prerequisites

| Requirement | Version | Install |
|---|---|---|
| Node.js | 22.x | [nodejs.org](https://nodejs.org/) |
| AWS CLI | 2.x | [AWS CLI install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| jq | latest | `brew install jq` / `apt install jq` |

### Configure AWS credentials

```bash
aws configure
# Or set environment variables:
# export AWS_ACCESS_KEY_ID=<your-key>
# export AWS_SECRET_ACCESS_KEY=<your-secret>
# export AWS_DEFAULT_REGION=us-west-2
```

Verify your credentials:

```bash
aws sts get-caller-identity
```

---

## Project Structure

```
typescript/
├── app.ts               # Agent entry point (Express server)
├── package.json          # Node.js dependencies
├── tsconfig.json         # TypeScript config
├── iam.sh                # IAM role creation/deletion (bash + AWS CLI)
├── runtime.sh            # Create, get, list, wait, invoke, and delete runtimes (bash + AWS CLI)
└── README.md
```

---

## Step 1: Create the IAM Execution Role

AgentCore Runtime needs an IAM role to run your agent. The `iam.sh` script creates a role named `TypescriptExecutionRole` with the necessary permissions (Bedrock model invocation, ECR pull, CloudWatch Logs, X-Ray, and AgentCore services).

```bash
./iam.sh create
```

This is idempotent — if the role already exists, it returns the existing ARN. Capture the output and export it:

```bash
export ROLE_ARN=$(./iam.sh create)
echo $ROLE_ARN
```

---

## Step 2: Build the Agent Package

Install dependencies, bundle TypeScript + all dependencies into a single file, and create the deployment zip:

```bash
npm install

npm run build

cd dist
zip deployment_package.zip app.js
cd ..

```

This uses `esbuild` to bundle `app.ts` + all dependencies (Express, Strands Agents SDK, Zod, AWS SDK) into a single `dist/app.js`. No `node_modules` needed in the zip.

---

## Step 3: Upload to S3

Set your account ID and region, then upload:

```bash
export BUCKET=$(your-bucket)

aws s3 cp dist/deployment_package.zip \
  s3://$BUCKET/typescript_deploy/deployment_package.zip
```

---

## Step 4: Deploy with Direct Code Deploy

Make sure `ROLE_ARN` is exported from Step 1, then create the runtime:

```bash
export AWS_REGION="us-east-1"
# ROLE_ARN already exported from Step 1

./runtime.sh create
```

Example output:

```json
{
  "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_typescript_agent-XXXXXXX",
  "agentRuntimeId": "my_typescript_agent-XXXXXXX",
  "status": "CREATING"
}
```

Export your agent ARN to be referenced in next examples:

```bash
export AGENT_ARN="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_typescript_agent-XXXXXXX"
export AGENT_ID="my_typescript_agent-XXXXXXX"
```


Wait for the runtime to become `READY` (polls every 10s):

```bash
./runtime.sh wait $AGENT_ID
```

---

## Step 5: Verify and Invoke

### List runtimes

```bash
./runtime.sh list
```

### Get runtime details

```bash
./runtime.sh get $AGENT_ID
```

### Invoke the agent

Once the runtime status is `READY`:

```bash
./runtime.sh invoke $AGENT_ARN

# With a custom prompt:
./runtime.sh invoke $AGENT_ARN "what is your status?"
```

---

## Step 6: Clean Up

Delete the runtime, IAM role, and S3 artifact when you're done:

```bash
# Delete the runtime
./runtime.sh delete <agentRuntimeId>

# Delete the IAM role
./iam.sh delete

# Remove the S3 artifact
aws s3 rm s3://bedrock-agentcore-code-${ACCOUNT_ID}-${REGION}/typescript_deploy/deployment_package.zip
```

---

## How It Works

### Agent Code (`app.ts`)

The agent is an Express server powered by [Strands Agents SDK](https://strandsagents.com/) with a **calculator tool**. The Strands agent uses Amazon Bedrock (Claude Haiku 4.5) as the LLM and can call tools via native tool-calling.

| Endpoint | Method | Purpose |
|---|---|---|
| `/ping` | GET | Health check — AgentCore uses this to verify the agent is running |
| `/invocations` | POST | Receives a prompt, runs the Strands agent (with tool use), and returns the response |

AgentCore Runtime expects the server to listen on port **8080**.

#### Calculator Tool

The agent has a `calculator` tool that supports `add`, `subtract`, `multiply`, and `divide`. When you send a math question, the LLM decides to call the tool and returns the result.

```bash
./runtime.sh invoke $AGENT_ARN "What is 25 * 4 + 10?"
```

### Direct Code Deploy

Instead of building a container image, Direct Code Deploy lets you upload your source code as a zip to S3. AgentCore handles the build and runtime environment. The deploy payload specifies:

```json
{
  "agentRuntimeArtifact": {
    "codeConfiguration": {
      "code": {
        "s3": {
          "bucket": "bedrock-agentcore-code-<ACCOUNT_ID>-<REGION>",
          "prefix": "typescript_deploy/deployment_package.zip"
        }
      },
      "runtime": "NODE_22",
      "entryPoint": ["app.js"]
    }
  }
}
```

### IAM Role

The execution role grants the agent permissions to:
- Invoke Bedrock models (`bedrock:InvokeModel`)
- Pull container images from ECR
- Write logs to CloudWatch
- Send traces to X-Ray
- Access AgentCore services (Memory, Browser, Gateway, CodeInterpreter)
