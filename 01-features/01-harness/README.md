# AgentCore harness

Every agent has an orchestration layer which contains the loop that calls the model, decides which tool to invoke, passes results back, manages context windows, and handles failures. Running that loop requires infrastructure underneath it: compute to host the agent, a sandbox to safely execute code, secure connections to tools, persistent storage, and error recovery. This infrastructure forms the agent harness, enabling an agent to actually run.

The new managed agent harness feature in AgentCore replaces all that upfront build with a straightforward configuration. You declare your agent and run it in just three API calls, without writing orchestration code. You define what your agent does: which model it uses, which tools it can call, and what instructions it follows. AgentCore’s harness stitches together compute, tooling, memory, identity, and security to create a running agent that you can test in minutes. Trying a different model or adding a tool is a config change, not a code rewrite. You can test several variations of an agent in minutes by changing the API parameter on the fly.

![AgentCore harness](00-getting-started/images/harness.png)

## Top-level layout

| Folder | What's inside |
|:-------|:--------------|
| `00-getting-started/` | Core workflow: create harness, invoke, ExecuteCommand |
| `01-advanced-examples/` | Custom containers, gateway, execution limits, MCP, skills, VPC, OAuth, AWS Skills, S3 filesystem |
| `02-use-cases/` | End-to-end applications (travel agent, webapp visual testing, AWS builder agent) |
| `utils/` | Shared IAM and boto3 client helpers used by all scripts |

## How this tree is organized

Scripts are grouped first by complexity (getting started → advanced → use-cases),
then by the specific feature or capability being demonstrated. Each folder is
self-contained — copy any folder and it runs independently.

## Finding things

- **By feature** → `01-advanced-examples/<feature>/`
- **By end-to-end scenario** → `02-use-cases/<use-case>/`
- **By tool type** → MCP: `04-mcp-integration/`, Browser: `01-travel-agent/` (Part 5), Skills: `05-agent-skills/` (custom) and `13-aws-skills/` (native AWS Skills)
- **Auth patterns** → `07-oauth/` (JWT inbound + OAuth outbound)
- **Persistent storage** → `14-s3-filesystem/` (mount S3 as the agent filesystem; includes an LLM wiki)
- **Build an agent with AWS Skills** → `02-use-cases/03-aws-builder-agent/` (harness + AWS Skills = an AWS engineering agent)

## AgentCore CLI

The harness is accessible via the AgentCore CLI. The fastest path to a running harness:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

```bash
# Create and deploy (interactive wizard selects Harness type)
agentcore create --name myresearchagent --model-provider bedrock
agentcore deploy

# Invoke
agentcore invoke --session-id "$(uuidgen)" \
  "Research three tropical vacation options under $3k."

# Local development
agentcore dev

# Check status
agentcore status

# Add memory
agentcore add memory
agentcore deploy
```

The tutorials in this folder use **boto3 directly** for full visibility into every API call and parameter — the CLI abstracts these details away. Both approaches produce the same result.

## Resources

- [AgentCore harness Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html)
- [AgentCore CLI — Get started with harness](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-get-started.html)

## Prerequisites

- Python 3.10+
- AWS CLI v2 configured with credentials
- `AWS_DEFAULT_REGION` environment variable set (or boto3 default region configured)
- Model access enabled for Claude Haiku 4.5 and Claude Sonnet 4.6 in Amazon Bedrock

## Running the Python Scripts

Install dependencies once:

```bash
pip install -r requirements.txt
```

Then run any script directly:

```bash
# Getting started
python 00-getting-started/getting_started.py

# Execution limits
python 01-advanced-examples/03-execution-limits/execution_limits.py

# Custom containers (Node.js preset)
python 01-advanced-examples/01-custom-containers/custom_container.py --language node

# gateway integration
python 01-advanced-examples/02-gateway-integration/gateway_integration.py

# MCP integration
python 01-advanced-examples/04-mcp-integration/mcp_integration.py

# Agent skills (xlsx spreadsheets)
python 01-advanced-examples/05-agent-skills/agent_skills.py

# AWS Skills (native skill bundles from the AWS Agent Toolkit)
python 01-advanced-examples/13-aws-skills/aws_skills.py

# S3 filesystem mount (persistent storage across sessions; requires VPC subnets + SGs)
python 01-advanced-examples/14-s3-filesystem/s3_filesystem.py \
    --access-point-arn arn:aws:s3files:REGION:ACCOUNT:file-system/fs-xxxx/access-point/fsap-xxxx \
    --subnet-ids subnet-xxxx --security-group-ids sg-xxxx

# S3-backed LLM wiki (ingest → query → lint)
python 01-advanced-examples/14-s3-filesystem/s3_llm_wiki.py \
    --access-point-arn arn:aws:s3files:REGION:ACCOUNT:file-system/fs-xxxx/access-point/fsap-xxxx \
    --subnet-ids subnet-xxxx --security-group-ids sg-xxxx

# OAuth + JWT auth
export HARNESS_USER_NAME="testuser"
export HARNESS_USER_PASS="TestPassword123!"
python 01-advanced-examples/07-oauth/oauth_gateway.py

# AWS builder agent (harness + AWS Skills builds a serverless app)
python 02-use-cases/03-aws-builder-agent/aws_builder_agent.py

# Travel guide agent
python 02-use-cases/01-travel-agent/travel_agent.py

# Webapp visual testing
python 02-use-cases/02-webapp-visual-testing/webapp_visual_testing.py

# Weather agent (gateway + guardrails + evals + observability)
python 02-use-cases/04-weather-agent/weather_agent.py
```

Run all tests:

```bash
python run_tests.py
```
