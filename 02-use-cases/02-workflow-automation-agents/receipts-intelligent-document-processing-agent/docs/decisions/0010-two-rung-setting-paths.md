# ADR-0010: Two Rung-Setting Paths + a Custom Step-Down Metric

**Status:** Accepted
**Date:** 2026-06-24

## Context

A `503` can be set against the ladder in two different time scales: *this one request* just got a `503`, versus *the model tier has been failing for everyone for a while*. Reacting to only one of those leaves a gap.

## Decision

Set the rung two ways, and feed the account-level path with a **custom metric** the agent emits on a real step-down:

1. **Reactive (in-agent).** On a persistent `503` during a run, the agent drops to the next rung's model **for that run** and records the step-down. `429`/`500` back off and retry the same model.
2. **Proactive (account-level control loop).** The agent emits a `ModelStepDowns` CloudWatch metric whenever it actually steps down. An alarm on sustained `ModelStepDowns` → EventBridge → a **controller Lambda** steps `activeRung` down one rung via AppConfig (with a cooldown), so every *new* invocation starts on the safer rung. A recovery (alarm → OK) steps it back up, one rung at a time.

## Reasoning

The reactive path protects the request in flight — no point failing a receipt while a lower rung has capacity. The proactive path protects the *fleet*: if the top model is broadly down, every new run should start lower rather than each one independently eating a `503`-and-step-down tax first.

The non-obvious part is the **signal**. A `503` the agent *recovers from* via step-down produces a **successful** Runtime invocation — so it never appears as a Runtime `System Error` metric. Watching Runtime errors would miss exactly the event we care about. The honest signal is the custom `ModelStepDowns` metric the agent emits on a real step-down; that metric is what bridges the reactive path to the proactive one.

The cooldown is **stateless** — the controller reads the most recent AppConfig deployment's `StartedAt` and skips if it's within the window. The deployment history *is* the cooldown clock; no extra store. One rung per event prevents flapping.

## Alternatives Considered

- **In-agent step-down only:** every run pays the `503`-then-step tax independently during a sustained outage, and the system never "settles" onto a safe rung.
- **Control loop only:** the first requests of an incident fail before the alarm trips; the in-agent path covers that window.
- **Alarm on Runtime `System Errors`:** would miss recovered `503`s (they're successful invocations). Hence the custom metric.
- **A DynamoDB cooldown record:** more moving parts than reading the deployment history that AppConfig already keeps.

## Consequences

More infrastructure: the controller Lambda, a `ModelStepDowns` alarm, an EventBridge rule, and `PutMetricData` (namespace-scoped) on the Runtime role. The controller's IAM must cover **both** the AppConfig `application/*` resources **and** the `deploymentstrategy/<id>` resource — `appconfig:StartDeployment` authorizes against the strategy too, which lives outside the `application/*` path (a live deploy caught this as an `AccessDenied`). The loop is faithfully testable on demand: `cloudwatch:SetAlarmState` fires the real EventBridge event, so the whole alarm→controller→AppConfig path runs for real without needing an actual Bedrock outage.
