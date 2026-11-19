# Instructions for a coding agent: fix what Continuous Insights found

You are working on the Event-Driven Claims Agent: a Claims Processor agent, a Validation Agent that
reviews its decision, and a deterministic execution phase, on Amazon Bedrock AgentCore.
Amazon Bedrock AgentCore Continuous Insights has grouped recurring problems from live sessions into
**Insights**. Your job is to fix the causes in this code.

## Read first
1. `insights/FAILURES.md`: one section per Insight with a description, root causes, a suggested fix, and
   evidence from sampled sessions. The full API responses are the `insight-*.json` files next to it.
2. `app/claimsagent/main.py` (prompts, agents, routing glue), `app/claimsagent/routing.py`, and
   `lambdas/*/handler.py` (the tools).

## Do
- Fix the **cause** of each Insight at the layer where it lives. Insights here describe agent behavior,
  so a fix may be a prompt change, a deterministic check in code, or a change in a tool. When a rule can be
  enforced in code, prefer code over a longer prompt.
- Tool descriptions are prompt text too: the model reads them to decide how to call a tool and how to read
  its result. When a change alters what a tool accepts or returns, update its description to match.
- Treat each suggested fix as a hypothesis: confirm it against the code and choose the smallest change that
  addresses the cause.
- Add unit tests under `tests/` for any deterministic code you change (a pure function, or a Lambda handler
  with its DynamoDB table stubbed). Prompt changes cannot be unit-tested: say so, and say how a replay of
  the traffic would show whether they worked.
- Finish with a short note listing, per Insight id: what you changed, at which layer, and how it is covered.

## Do not
- Do not weaken a safeguard to make an Insight go away. Do not lower the confidence needed for automatic
  approval, remove or loosen the Cedar policy, or tell an agent to approve more claims.
- Do not deploy, call AWS, or read `.env` or any state file. A human deploys.
- Do not change tool names or parameters that already work, and do not add dependencies.
