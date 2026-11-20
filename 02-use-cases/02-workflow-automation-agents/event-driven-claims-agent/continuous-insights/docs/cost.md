# Cost

A full walkthrough (two rounds of 100 claims and about a day of the claims agent stack) costs on the order of **$55**, with a plausible range of **$25 to $130**. Three lines make up most of it: the Failure Analysis evaluations this sample adds, the Bedrock model calls, and the claims agent's own evaluations. The token counts behind those lines are assumptions, not measurements, and the Failure Analysis billing is the least certain number in this document.

Prices are for `us-east-1`, checked on 2026-11-19 with the AWS Price List API (service codes `AmazonBedrockFoundationModels`, `AmazonBedrockAgentCore` and `AmazonCloudWatch`), except where the table says otherwise. Prices change; confirm them on the [AgentCore pricing page](https://aws.amazon.com/bedrock/agentcore/pricing/) and the [Amazon Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) before you rely on them.

## Cost drivers

| Driver | What bills | Unit price | Status |
|---|---|---|---|
| Claims Processor: Claude Sonnet 4.6, `global.` profile | Input and output tokens | $3.00 per 1M input, $15.00 per 1M output | Published |
| Validation Agent: Claude Haiku 4.5, `us.` profile | Input and output tokens | $1.10 per 1M input, $5.50 per 1M output | Published |
| AgentCore Evaluations, built-in evaluators | Input and output tokens the evaluator processes | $2.40 per 1M input, $12.00 per 1M output (first pricing tier) | Published |
| `Builtin.FailureAnalysis` specifically | Not listed separately | Assumed to bill like other built-in evaluators | **Unverified** |
| Agent Insights (Continuous Insights) | The Insight feature itself | No additional charge for Insights | See the AgentCore pricing page |
| Claims agent stack | Runtime, Gateway, Lambda, DynamoDB, and the other resources `./deploy.sh` creates | About $3 to $5 per day, from the claims agent's README | Its README's estimate; not re-measured here |
| CloudWatch Logs | Ingestion and storage | $0.50 per GB ingested (Standard log class), $0.03 per GB-month stored | Published |
| CloudWatch Logs Insights | Data scanned by queries | $0.005 per GB scanned | Published |
| CloudWatch Transaction Search | Span ingestion into CloudWatch Logs | $0.35 to $0.50 per GB ingested | **Unverified**; not re-checked for `us-east-1` |

Phase 3 of the claims agent (create the claim, notify, escalate) makes no model call, so the model cost is the Claims Processor and the Validation Agent only.

## What was measured and what was assumed

**Measured:** not yet. We did not measure token counts or the billed cost for the 100-claim run, so every token count below is an assumption. Replace the counts with your own from the Bedrock invocation metrics or the session spans.

| Assumption | Value | Why |
|---|---|---|
| Claims Processor tokens per claim | 12,000 input, 1,200 output | About three model calls (policy lookup, decision, structured output); the prompt and tool schemas are re-sent on each call |
| Validation Agent tokens per claim | 5,000 input, 600 output | About two model calls with a shorter context |
| Failure Analysis tokens per session | 30,000 input, 2,000 output | It reads the whole session's spans. This is an estimate, not a published figure |
| The claims agent's own evaluation, per session | 30,000 input, 1,500 output | The claims agent ships an online evaluation (`ClaimsEvaluation`) with three built-in evaluators at 100% sampling. Assumed about 10,000 input and 500 output tokens per evaluator |
| Span volume | 100 to 200 KB per session | Two agents plus tool calls; about 30 MB for the 200 sessions of the walkthrough, which is a few cents at the ingestion prices above |

## Estimate

| Per claim | Cost |
|---|---|
| Claims Processor, Sonnet 4.6 | $0.054 |
| Validation Agent, Haiku 4.5 | $0.009 |
| Failure Analysis evaluation (this sample) | $0.10 (assumed; plausible range $0.03 to $0.30) |
| Claims agent's own evaluation (`ClaimsEvaluation`) | $0.09 (assumed) |
| CloudWatch logs and spans | under $0.001 |
| **Total** | **about $0.25** |

| Walkthrough | Cost |
|---|---|
| Round 1: 100 claims, model calls | about $6 |
| Round 2: 100 claims, model calls | about $6 |
| Failure Analysis on 200 sessions (added by this sample) | about $19 (range $6 to $60) |
| Claims agent's own evaluation on 200 sessions | about $18 (range $5 to $55) |
| Smoke test after the redeploy | under $1 |
| Claims agent stack, about one day | about $3 to $5 |
| **Total** | **about $55** (range $25 to $130) |

How the total is built: 200 claims at $0.063 in model calls ($12.60), plus 200 sessions at $0.096 for Failure Analysis ($19.20), plus 200 sessions at $0.090 for the claims agent's own evaluation ($18.00), plus $3 to $5 for the stack. The range keeps the model calls fixed and lets both evaluation lines run from about a third to about three times the assumed tokens per session.

The Failure Analysis line is the part this sample adds. Sending fewer claims costs less, but send enough for the first Insights to form: about 20 failed sessions. In our run, 59 of the 100 claims in round 1 had failures. The rest is the claims agent running as its README describes. The claims agent's $3 to $5 per day does not mention model or evaluation usage, so this estimate counts both separately and does not check whether its estimate includes them.

## Teardown

**Run both teardowns when you finish.** The stack keeps costing about $3 to $5 per day while it stays deployed.

```bash
python scripts/cleanup.py --name ClaimsAgent --delete-logs   # this sample: evaluation config and role
cd <path-to>/event-driven-claims-agent
./scripts/destroy.sh us-east-1                       # the claims agent and its resources
```

Two settings are account-wide and `scripts/cleanup.py` leaves them alone. The Agent Insight Store has no separate charge. CloudWatch Transaction Search keeps billing per ingested span for any traced workload in the account.

## Cost at scale

Model calls and evaluations both scale with traffic. The evaluation cost also scales with the **fraction of sessions you sample**, so that setting is your main lever. Using the per-claim assumptions above (stack cost excluded):

| Monthly claims | Sampling | Failure Analysis | Model calls |
|---|---|---|---|
| 10,000 | 100% | about $960 | about $630 |
| 10,000 | 10% | about $96 | about $630 |
| 100,000 | 10% | about $960 | about $6,300 |

Lower sampling means fewer sessions to learn from, so Insights take longer to appear and rare failures may not show up. Start high on a new agent and lower the rate once your Insights stabilize.

## Reduce the cost

- Run both teardowns when you finish.
- Send the minimum your Insight threshold needs: about 20 failed sessions. The walkthrough sends 100 claims per round, so that round 1 passes the threshold and each Insight has sessions to compare between rounds.
- Use the idle timeout that matches your real sessions. A very short timeout can evaluate a session before it is over.
- Sample less on high-volume agents (see above).
- Keep prompts and tool schemas short; they are re-sent on every model call.
- Choose the `global.` inference profile for lower token prices, or the `us.` profile if you must keep inference inside the US. The global profile is cheaper. The claims agent uses `global.` for Sonnet and `us.` for Haiku by default.

## Check your actual spend

Filter Cost Explorer to the Amazon Bedrock, AgentCore, CloudWatch, Lambda, and DynamoDB services for the days you ran the walkthrough. Set an AWS Budgets alert on a sandbox account before you start.
