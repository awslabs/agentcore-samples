# Observe, Evaluate, and Optimize Your Agent

End-to-end lifecycle management for agents on Amazon Bedrock AgentCore — from
enabling observability infrastructure through advanced monitoring techniques,
automated evaluation, and AI-driven optimization with A/B testing.

## Overview

![Agent development to production loops](AGENT-LOOPS.PNG)

## Before You Start — Enable observability Infrastructure

**CloudWatch Transaction Search must be enabled before any traces appear in CloudWatch
Gen AI observability.** This is a one-time setup per AWS account and region.

### Option 1: Infrastructure as Code (recommended)

Use the CloudFormation template provided in this repository:

```
05-infrastructure-as-code/01-enable-transaction-search/
```

```bash
cd ../../05-infrastructure-as-code/01-enable-transaction-search/
python deploy.py
```

### Option 2: AWS Console

1. Open [CloudWatch → X-Ray settings → Transaction Search](https://console.aws.amazon.com/cloudwatch/home#xray:settings/transaction-search)
2. Click **Enable Transaction Search**
3. Wait approximately 10 minutes for setup to complete

For detailed steps, see [Enable Transaction Search — AWS Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html).

---

## Folder Structure

```
06-observe-evaluate-optimize-your-agent/
  observe/         ← Advanced observability techniques
  evaluate/        ← Automated agent evaluation
  optimize/        ← AI-driven optimization with A/B testing
```

---

## observe/ — Advanced observability Techniques

Advanced instrumentation techniques that go beyond the automatic spans captured by ADOT.
Both samples use a **Strands travel agent** (web_search + get_weather tools) as the demo agent,
so you can focus on the observability concept.

| Script | Concept | What It Demonstrates |
|:-------|:--------|:--------------------|
| `custom_span_creation.py` | Custom Spans | Wrap specific operations (tool calls, processing steps) in OTel spans with rich attributes, events, and error status |
| `data_protection.py` | Data Protection | Protect PII via Bedrock Guardrails (model level) and CloudWatch Logs Data Protection policies (log group level) |

### Custom Span Creation

Creates parent spans for sessions and child spans for each tool call, giving precise
visibility into exactly what the agent did and how long each step took:

```
invoke_agent
  └── travel_agent_session        ← custom session span (w/ session.id attribute)
        ├── model_call             ← automatic (ADOT)
        ├── web_search             ← custom span: search.query, results_count, result_urls
        └── get_weather            ← custom span: weather.location, weather.result
```

**Run:**
```bash
pip install -r observe/requirements.txt
cp observe/.env.example observe/.env
# Edit .env with your CloudWatch log group
opentelemetry-instrument python observe/custom_span_creation.py --session-id "demo-001"
```

### Data Protection

Two-layer PII protection for production agents:

- **Layer 1 — Bedrock Guardrails**: Detects and anonymizes PII in model prompts and responses
- **Layer 2 — CloudWatch Logs Data Protection**: Masks PII patterns in CloudWatch log events with `****`

**Run:**
```bash
python observe/data_protection.py          # Creates AWS resources + demonstrates protection
python observe/data_protection.py --cleanup
```

---

## evaluate/ — Automated Agent evaluation

Four evaluation approaches across the full evaluation maturity curve:

| Sub-folder | Approach | Description |
|:-----------|:---------|:------------|
| `ground-truth-based-evaluation/` | Ground Truth | Dataset simulation → batch evaluation with ground truth labels |
| `llm-as-a-judge-evaluation/` | LLM as Judge | On-demand and online evaluation using built-in LLM evaluators |
| `custom-code-based-evaluation/` | Custom Code | Lambda-based evaluators with domain-specific business logic |
| `skills-evaluation/` | Agent Skills | Built-in skill selection and instruction-following evaluation |

All evaluation samples use an **HR Assistant agent** as the demo agent.

### Built-in LLM Evaluators

| Evaluator | What It Measures |
|:----------|:----------------|
| `GoalSuccessRate` | Did the agent complete the user's goal? |
| `Helpfulness` | Was the response useful and actionable? |
| `Correctness` | Did the agent give accurate information? |
| `TrajectoryToolSelectionAccuracy` | Did the agent choose the right tools in the right order? |
| `Faithfulness` | Is the response grounded in retrieved context? |
| `SkillSelectionAccuracy` | Did the agent load the right skill for the request? |
| `SkillInstructionFollowing` | Did the agent follow the loaded skill's required steps? |

---

## optimize/ — AI-Driven Optimization

End-to-end optimization workflow: baseline measurement → AI-generated recommendations →
configuration bundles → A/B testing — all without redeploying code.

```
HR Assistant v1  ──►  Batch Eval  ──►  SP + TD Recs  ──►  Config Bundle A/B
     │                                                             │
     └──────────────── v2 canary (10%) ◄──── Target-Based A/B ───┘
```

**Key capabilities:**
- **Configuration Bundles**: Versioned container for system prompt + tool descriptions. Injected at invocation time — no code changes needed
- **Batch evaluation**: Scores production sessions using built-in LLM evaluators
- **System Prompt Recommendations**: AI-generated rewrites targeting a specific metric
- **Tool Description Recommendations**: Improved tool descriptions for better tool selection
- **Online evaluation**: Automatically scores every session as it closes
- **A/B Testing**: Config-bundle routing (50/50) or target-based routing (10% canary) with sticky session assignment

**Quick start:**
```bash
cd optimize/
pip install -r requirements.txt
python deploy.py --name HRAssistV1
python invoke.py --name HRAssistV1
python optimize.py --name HRAssistV1
```

---

## AgentCore CLI

The AgentCore CLI supports the full evaluate → optimize loop. Install it:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

### Run a quick evaluation on a session

```bash
agentcore run eval \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate \
  --session-id <session-id>
```

### Run a batch evaluation across many sessions

```bash
agentcore run batch-evaluation \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness Builtin.Correctness
```

### Generate AI-driven optimization recommendations

```bash
# System prompt recommendation
agentcore run recommendation \
  --runtime HRAssistant \
  --type system-prompt \
  --evaluator Builtin.GoalSuccessRate

# Tool description recommendation
agentcore run recommendation \
  --runtime HRAssistant \
  --type tool-description \
  --evaluator Builtin.ToolSelectionAccuracy
```

### Create a custom evaluator

```bash
agentcore add evaluator \
  --name HRResponseQuality \
  --level SESSION \
  --type llm-as-a-judge \
  --instructions "Score whether the HR assistant fully resolved the employee's request. Score 1.0 for complete resolution, 0.5 for partial, 0.0 for unresolved."
```

### Enable online evaluation (continuous monitoring in production)

```bash
agentcore add online-eval \
  --name HROnlineEval \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness \
  --sampling-rate 100 \
  --enable-on-create
```

See [`03-optimize/README.md`](03-optimize/README.md) for the full optimization workflow including
configuration bundles and A/B testing.

---

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [View Agent Data in CloudWatch](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-view.html)
- [CloudWatch GenAI observability — User Guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AgentCore evaluation — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluation.html)
- [Enable Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html)
- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
