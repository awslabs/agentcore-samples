# Async Step Functions with AgentCore Harness

| Information         | Details                                                         |
|:--------------------|:----------------------------------------------------------------|
| Tutorial type       | Advanced Example                                                |
| Agent type          | Weather search assistant with async orchestration               |
| Agentic Framework   | AWS Step Functions + AgentCore Harness                          |
| LLM model           | Anthropic Claude Haiku 4.5                                      |
| Tutorial components | Step Functions, DynamoDB, Harness with MCP tools, CloudFormation|
| Example complexity  | Advanced                                                        |

## Overview

Build serverless, event-driven AI workflows using AWS Step Functions to orchestrate AgentCore harness invocations.

This example demonstrates:
- **Step Functions** orchestration (no Lambda needed for JSON parsing)
- **AgentCore Harness** with MCP tools for real-time web search
- **JSON extraction** from agent markdown responses
- **DynamoDB** storage with city-based queries

## Architecture

```
Trigger → Step Functions → Invoke Harness → Exa MCP Search
                ↓              ↓
         Extract JSON    Parse JSON
                ↓              ↓
            DynamoDB      Store Results
```

**Workflow:**
1. Receive input: `{city, date}`
2. Invoke harness: "Get weather for {city} on {date}"
3. Harness uses Exa MCP (https://mcp.exa.ai/mcp) to search web
4. Extract the JSON object from the reply with a JSONata regex match
5. Parse it with JSONata `$parse()`
6. Store in DynamoDB with GSI for city queries

## Prerequisites

- **AWS credentials** with permission to create CloudFormation stacks, IAM roles,
  DynamoDB tables, Step Functions state machines and AgentCore harnesses. The
  deploy uses `--capabilities CAPABILITY_NAMED_IAM` because the roles are named.
- **[jq](https://jqlang.github.io/jq/)** — all three scripts use it to read
  `deployment_info.json` and the sample inputs.
- **AWS CLI v2**, configured for a region where AgentCore Harness is available.
  `deploy.sh` honours `AWS_DEFAULT_REGION`, then `AWS_REGION`, then your
  configured default, falling back to `us-west-2`.
- **Model access** to `global.anthropic.claude-haiku-4-5-20251001-v1:0` in that
  region.

## Quick Start

### 1. Deploy
```bash
./deploy.sh
```

Creates:
- DynamoDB table `weather-data` (PK: id, SK: timestamp, GSI: city-index)
- AgentCore harness with Exa MCP tool
- Step Functions state machine `WeatherWorkflow`
- IAM roles

### 2. Test

**Interactive mode** (prompts for city/date):
```bash
./test_workflow.sh
```

**Use sample data**:
```bash
./test_workflow.sh --use-samples
```

**Manual execution**:
```bash
aws stepfunctions start-execution \
  --state-machine-arn $(jq -r '.stateMachineArn' deployment_info.json) \
  --input '{"city":"Tokyo","date":"2024-12-25"}'
```

### 3. Query Results

```bash
# All results
aws dynamodb scan --table-name weather-data

# Specific city
aws dynamodb query --table-name weather-data \
  --index-name city-index \
  --key-condition-expression "city = :city" \
  --expression-attribute-values '{":city":{"S":"Tokyo"}}'
```

### 4. Clean Up
```bash
./cleanup.sh
```

## Key Features

✅ **No Lambda required** - JSON parsing with JSONata inside Step Functions  
✅ **MCP integration** - Exa search for real-time data  
✅ **Error handling** - 3x retry with exponential backoff on transient errors;
caller-fault errors (access denied, validation) fail fast, and a failed
invocation ends the execution as `FAILED` rather than reporting success  
✅ **Structured storage** - DynamoDB with city GSI  
✅ **Cost effective** - ~$0.27 per 1000 executions  

## How JSON Extraction Works

Step Functions extracts the JSON object from the agent's reply without Lambda,
using a JSONata regex match in a `Pass` state:

```json
{
  "Type": "Pass",
  "QueryLanguage": "JSONata",
  "Output": {
    "jsonText": "{% $match($states.input....Text, /\\{[\\s\\S]*\\}/).match %}"
  }
}
```

The pattern is greedy from the first `{` to the last `}`, so all three shapes a
model realistically returns work:

| Agent reply                                  | Extracted            |
|:---------------------------------------------|:---------------------|
| `{"city":"Tokyo",...}`                       | the whole object     |
| `Here's the weather: {"city":"Tokyo",...}`    | the whole object     |
| ` ```json\n{"city":"Tokyo",...}\n``` `       | the whole object     |

Nested objects survive too — `{"city":"Tokyo","detail":{"wind_kph":5}}` is
matched intact.

A `Choice` state then checks whether anything was matched at all. If the reply
contained no JSON object, the execution ends in the `AgentResponseNotJson`
`Fail` state rather than continuing with empty data.

`ParseJson` turns the matched text into real JSON with `$parse()`, and
`StoreToDynamoDB` writes the fields.

## DynamoDB Schema

```
Table: weather-data
├─ id (String, PK) - UUID
├─ timestamp (String, SK) - Unix timestamp
├─ city (String) - City name
├─ date (String) - Query date
├─ temperature_c (Number)
├─ temperature_f (Number)
├─ conditions (String)
├─ input_city (String) - Original input
└─ input_date (String) - Original input

GSI: city-index
├─ city (String, PK)
└─ timestamp (String, SK)
```

## Use Cases

- **Scheduled updates** - EventBridge cron triggers
- **Multi-city monitoring** - Parallel execution with Map state
- **Historical analysis** - Query trends by city/date
- **Alert system** - SNS notifications on conditions
- **Data pipeline** - Feed to QuickSight/S3
