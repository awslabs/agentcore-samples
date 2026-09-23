# Hosting Embabel GOAP Agents with AgentCore Browser in Amazon Bedrock AgentCore Runtime

## Overview

In this tutorial we will learn how to host an Embabel GOAP (Goal-Oriented Action Planning) agent on Amazon Bedrock AgentCore Runtime, using AgentCore Browser for web-based claim verification.

This tutorial demonstrates patterns unique to Java: typed goal-oriented planning with `@Agent`, `@Action`, and `@AchievesGoal` annotations, where the GOAP planner automatically chains actions based on type availability on the Blackboard.

For the minimal Spring AI agent, see [01-springai-with-bedrock-model](../01-springai-with-bedrock-model).

### Tutorial Details

| Information         | Details                                                                                          |
|:--------------------|:-------------------------------------------------------------------------------------------------|
| Tutorial type       | Multi-step pipeline                                                                              |
| Agent type          | Single (GOAP-planned)                                                                            |
| Agentic Framework   | Embabel Agent Framework + Spring AI                                                              |
| LLM model           | Anthropic Claude Haiku 4.5                                                                       |
| Tutorial components | AgentCore Runtime, AgentCore Browser, Embabel GOAP planning, Spring AI ChatClient                |
| Tutorial vertical   | Cross-vertical (Fact-Checking)                                                                   |
| Example complexity  | Intermediate                                                                                     |
| SDK used            | spring-ai-agentcore-runtime-starter, spring-ai-agentcore-browser, embabel-agent-starter, AWS CDK |

### Library

This tutorial uses the [spring-ai-agentcore](https://github.com/spring-ai-community/spring-ai-agentcore) community library, a Spring Boot starter that auto-configures AgentCore Runtime endpoints, provides the `@AgentCoreInvocation` annotation, and includes the `spring-ai-agentcore-browser` module for browser automation.

### Tutorial Key Features

* Embabel GOAP planning with `@Agent`, `@Action`, `@AchievesGoal` annotations
* Typed Blackboard: 4 POJOs auto-chained by the planner (`FactCheckRequest` → `ParsedClaims` → `VerifiedClaims` → `FactCheckReport`)
* AgentCore Browser for real web browsing during claim verification
* Inner `ChatClient` with `browserToolCallbackProvider` for browser tool calls
* Default demo claims target Wikipedia/official docs (bot-friendly, always verifiable)

## Prerequisites

* Java 21 (Amazon Corretto recommended)
* Maven 3.9+
* Docker
* Node.js 18+ and npm (for CDK)
* AWS CLI configured with appropriate credentials
* AWS CDK CLI (`npm install -g aws-cdk`)

## Project Structure

```
02-embabel-with-bedrock-model/
├── README.md
├── agent/
│   ├── pom.xml
│   ├── Dockerfile
│   ├── build-and-push.sh
│   └── src/main/
│       ├── java/com/example/agent/
│       │   ├── AgentApplication.java
│       │   ├── model/
│       │   │   ├── FactCheckRequest.java
│       │   │   ├── ParsedClaims.java
│       │   │   ├── VerifiedClaims.java
│       │   │   └── FactCheckReport.java
│       │   └── service/
│       │       └── FactCheckAgent.java
│       └── resources/application.yml
└── infra/
    ├── bin/app.ts
    ├── lib/agentcore-stack.ts
    ├── package.json
    ├── tsconfig.json
    └── cdk.json
```

## How the GOAP Pipeline Works

```
FactCheckRequest ──→ parseClaims() ──→ ParsedClaims
                                           │
                     verifyClaims() ◄──────┘
                          │
                     VerifiedClaims
                          │
                     summarize() ──→ FactCheckReport  ← @AchievesGoal
```

1. **parseClaims** — LLM extracts individual verifiable claims from user input
2. **verifyClaims** — Inner ChatClient with browser tools navigates real web pages to verify each claim
3. **summarize** — LLM produces a human-readable report (terminal goal)

The GOAP planner sees that `parseClaims` produces `ParsedClaims`, `verifyClaims` consumes `ParsedClaims` and produces `VerifiedClaims`, and `summarize` consumes `VerifiedClaims` and produces `FactCheckReport` (the goal). It chains them automatically.

## Step-by-Step Deployment

### 1. Install CDK dependencies

```bash
cd infra
npm install
```

### 2. Deploy ECR repository (first run)

```bash
cdk deploy -c firstRun=true
```

### 3. Build and push the Docker image

```bash
cd ../agent
chmod +x build-and-push.sh
./build-and-push.sh -r us-east-1 -u <EcrRepositoryUri>
```

### 4. Deploy the full stack

```bash
cd ../infra
cdk deploy
```

### 5. Invoke the agent

```bash
RUNTIME_ARN=$(aws cloudformation describe-stacks \
  --stack-name AgentCoreStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentRuntimeArn`].OutputValue' \
  --output text)

aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "$RUNTIME_ARN" \
  --cli-binary-format raw-in-base64-out \
  --content-type "application/json" \
  --payload '{"claims": ["Amazon S3 was launched in 2006", "Spring Boot requires Java 17 or later"]}' \
  /dev/stdout
```

## Resources

* [spring-ai-agentcore](https://github.com/spring-ai-community/spring-ai-agentcore) — Spring Boot starter for AgentCore Runtime integration, including the `spring-ai-agentcore-browser` module used in this tutorial
* [Embabel Agent Framework (GitHub)](https://github.com/embabel/embabel-agent) — Source code and wiki with quick-start guide, configuration reference, and examples
* [Embabel Agent Framework (Docs)](https://docs.embabel.com/) — Official user guide covering GOAP planning, annotations, Blackboard model, and more

## Cleanup

```bash
cd infra
cdk destroy
```
