# Amazon Bedrock AgentCore Policy - Automated Policy Creation

## Overview

This sample demonstrates **automated, agent-driven Cedar policy lifecycle management** for Amazon Bedrock AgentCore.

A policy orchestration agent — built with [Strands Agents](https://github.com/strands-agents/sdk-python) — discovers tools on an AgentCore Gateway, reads the RBAC permission manifests those tools emit on registration, auto-generates valid Cedar policies using the NL2Cedar API, routes each policy through a human reviewer via Amazon SNS, and commits approved policies to a Policy Engine attached to the Gateway.

## What You Will Learn

1. How tools registered on an AgentCore Gateway emit structured RBAC permission manifests
2. How to convert RBAC metadata into natural language policy descriptions
3. How to use the AgentCore NL2Cedar API to auto-generate Cedar policies
4. How to implement a human-in-the-loop review workflow using Amazon SNS + interactive approval
5. How to programmatically create policies in a Policy Engine and enforce them at the Gateway

## Architecture

```
  Lambda Tools
  (FinancialReportTool, TradeExecutionTool)
       |
       | register on Gateway (emits RBAC manifest)
       v
  AgentCore Gateway
       |
       |<---- Policy Orchestration Agent (Strands)
       |         1. list_gateway_tools
       |         2. get_tool_rbac_permissions
       |         3. generate_nl_policy_statement
       |         4. generate_cedar_policy (NL2Cedar API)
       |         5. request_human_approval (SNS email + interactive)
       |         6. create_policy_in_engine
       v
  Policy Engine
  (Cedar policies: enforce parameter constraints)
       |
       | ALLOW / DENY at runtime
       v
  Tool Invocations
```

## Demo Scenario: Financial Data Platform

Two Lambda-backed tools are deployed:

| Tool | Function | RBAC-Derived Cedar Rules |
|------|----------|--------------------------|
| FinancialReportTarget | `get_financial_report` | `classification_level` must be `internal`; `region` in `{US, EU, APAC}` |
| TradeExecutionTarget  | `execute_trade`        | `amount` must not exceed 500,000 |

## File Structure

```
04-Automated-Policy-Management/
├── Automated-Policy-Creation.ipynb     # Main notebook - start here
├── requirements.txt
├── README.md
└── scripts/
    ├── setup_infrastructure.py         # Deploy Lambda + create Gateway + attach targets
    ├── cedar_utils.py                  # RBAC->NL conversion and NL2Cedar helper
    ├── human_review.py                 # SNS/SQS review infrastructure
    ├── policy_orchestrator.py          # Strands agent + tool definitions
    └── lambda-tools/
        ├── financial_report_tool.js    # FinancialReportTool Lambda (Node.js)
        └── trade_execution_tool.js     # TradeExecutionTool Lambda (Node.js)
```

## Prerequisites

- Python 3.10+
- AWS CLI configured (`aws configure`) with credentials for an IAM principal that has permissions for:
  - **Lambda** — `CreateFunction`, `UpdateFunctionCode`, `DeleteFunction`
  - **IAM** — `CreateRole`, `AttachRolePolicy`, `DeleteRole`
  - **Amazon Cognito** — `CreateUserPool`, `CreateUserPoolClient`, `DeleteUserPool`
  - **Bedrock AgentCore** — `CreateGateway`, `CreateGatewayTarget`, `UpdateGateway`, `DeleteGateway`, `CreatePolicyEngine`, `CreatePolicy`, `DeletePolicy`, `DeletePolicyEngine`, `GeneratePolicy`
  - **Amazon SNS** — `CreateTopic`, `Subscribe`, `Publish`, `DeleteTopic`
  - **Amazon SQS** — `CreateQueue`, `SetQueueAttributes`, `DeleteQueue`
  - **Amazon Bedrock** — `InvokeModel` for `amazon.nova-lite-v1:0`
- (Optional) A valid email address to receive Cedar policy review notifications via SNS

## Usage

### Step 0 — Install dependencies and configure AWS

```bash
pip install -r requirements.txt
```

Open `Automated-Policy-Creation.ipynb` in JupyterLab and run cells **sequentially** from top to bottom.

### Step 1 — Deploy infrastructure

The notebook deploys two Lambda functions (`FinancialReportTool`, `TradeExecutionTool`), creates an AgentCore Gateway with Cognito OAuth, and attaches both Lambdas as Gateway targets. A `config.json` file is written with all resource identifiers.

> `config.json` is auto-generated and contains OAuth credentials — do not commit it to source control.

### Step 2 — Set up human review infrastructure

An SNS topic and SQS queue are created. If you provide an email address you will receive a copy of each generated Cedar policy for audit purposes. Confirm the SNS subscription from your inbox before the orchestrator runs.

### Step 3 — Create the Policy Engine

An empty Policy Engine is created. It will be populated with approved Cedar policies by the orchestrator in the next step.

### Step 4 — Run the Policy Orchestration Agent

The Strands agent runs fully automatically, processing each tool in sequence:

1. Discovers tools on the Gateway via MCP
2. Reads the RBAC manifest for each tool
3. Converts RBAC rules to a natural language policy statement
4. Calls the NL2Cedar API to generate Cedar syntax (~20 seconds per tool)
5. Displays the generated Cedar policy and **pauses for your approval**
6. Creates approved policies in the Policy Engine

The agent pauses **twice** — once per tool — waiting for you to type `yes` (approve) or `no` (reject).

### Step 5 — Attach the Policy Engine to the Gateway

The Policy Engine is attached to the Gateway in `ENFORCE` mode. From this point all tool requests are evaluated against the Cedar policies. The default action is `DENY`.

### Step 6 — Test enforcement

Five test requests are sent through the Gateway to verify the Cedar policies are enforced correctly (see [Sample Prompts](#sample-prompts) below).

## Sample Prompts

The test agent in Step 6 sends the following prompts to exercise each enforcement path:

| # | Prompt | Tool called | Expected result |
|---|--------|-------------|-----------------|
| 1 | `"Get a quarterly financial report for the US region with internal classification level."` | `get_financial_report` | **ALLOW** — `classification_level=internal`, `region=US` both satisfy the Cedar policy |
| 2 | `"Get a quarterly financial report for the US region with restricted classification level."` | `get_financial_report` | **DENY** — `classification_level=restricted` is not in the allowed set `{internal}` |
| 3 | `"Get an annual financial report for the LATAM region with internal classification level."` | `get_financial_report` | **DENY** — `region=LATAM` is not in the allowed set `{US, EU, APAC}` |
| 4 | `"Execute a buy trade for AMZN stock worth $250,000."` | `execute_trade` | **ALLOW** — `amount=250000` is within the $500,000 ceiling |
| 5 | `"Execute a buy trade for MSFT stock worth $750,000."` | `execute_trade` | **DENY** — `amount=750000` exceeds the $500,000 ceiling |

## Clean Up

The final notebook cell removes all AWS resources created during the demo in this order:

1. **Detach Policy Engine** from the Gateway (re-issues `update_gateway` without `policyEngineConfiguration`)
2. **Delete Cedar policies** inside the Policy Engine, then delete the Policy Engine itself
3. **Delete SNS topic and SQS queue** used for human review notifications
4. **Delete the AgentCore Gateway** and the associated Cognito User Pool / App Client
5. **Delete Lambda functions** (`FinancialReportTool`, `TradeExecutionTool`)
6. **Delete the IAM role** (`AgentCoreAutoPolicyLambdaRole`)

Run the cleanup cell even if earlier steps failed — each sub-step is wrapped in its own `try/except` so a failure in one step does not block the rest.

After cleanup, `config.json` will still exist locally but all the AWS resources it references will be gone.

## Key Concepts

### RBAC Manifest Emission

When a tool is attached to an AgentCore Gateway, its registration contract includes a **RBAC permission manifest** — a structured JSON object describing:
- Which user roles may invoke the tool
- Data classification requirements
- Input parameter constraints (allowed values, max values)

The orchestrator reads this manifest from `config.json` after infrastructure setup.

### NL2Cedar Generation

The `cedar_utils.py` module converts each RBAC manifest entry through two stages:
1. `rbac_to_natural_language()` — deterministic conversion to a policy sentence
2. `generate_cedar_from_nl()` — calls the AgentCore `generate_policy` API

### Human-in-the-Loop

For each generated policy:
1. An SNS notification is published (email to subscribed reviewer)
2. The orchestrator agent pauses and displays the Cedar policy in the notebook
3. The operator types `yes` (approve) or `no` (reject)
4. Only approved policies are created in the Policy Engine

## Relationship to Other Samples

This sample builds on concepts from:
- `01-Getting-Started`: Gateway setup patterns, agent-with-tools structure
- `02-Natural-Language-Policy-Authoring`: NL2Cedar API usage

The infrastructure setup reuses the same Gateway/Lambda/PolicyClient patterns established in those samples.
