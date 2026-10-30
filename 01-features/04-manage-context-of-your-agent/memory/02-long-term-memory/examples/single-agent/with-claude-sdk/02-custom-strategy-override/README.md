# Long-term memory — Claude SDK, custom strategy override

A conversation loop built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), wired to AgentCore **long-term memory** using a **built-in strategy with overrides** (also called a *custom strategy override*).

The companion tutorial [`01-built-in-strategies`](../01-built-in-strategies/) used AgentCore's default, fully-managed extraction. This tutorial keeps that same managed pipeline and its fixed record schema, but **overrides two things per step**: the **Bedrock model** that runs extraction/consolidation, and the **prompt instructions** appended to that step. That lets you steer *what* gets extracted and *which* model does the work — without owning the whole pipeline.

Because the Anthropic SDK is a stateless API client — no framework, no hooks, no session handling — the full memory lifecycle is explicit and easy to follow: create an IAM role → create with a custom override strategy → store turns → wait for extraction → retrieve records → inject them into a future session's system prompt.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Healthcare intake assistant |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategy | Custom semantic override (`customMemoryStrategy` → `semanticOverride`) — **requires an IAM execution role** |
| Memory components | `create_memory_and_wait` (with a custom override strategy + execution role), `create_event`, `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Complexity | Advanced |

## What is a strategy override?

A built-in strategy (Semantic, Summary, User Preference, Episodic) runs an AgentCore-managed extraction pipeline with a managed model and a managed prompt, producing records in a fixed schema. A **strategy override** wraps that built-in strategy in a `customMemoryStrategy` and lets you replace, per pipeline step:

- **`appendToPrompt`** — instructions *added* to that step's built-in system prompt. The text narrows or clarifies the default behavior; it does **not** replace the whole prompt or change the output schema.
- **`modelId`** — the Bedrock model AgentCore invokes for that step, **in your account**, via the execution role you provide.

Each built-in strategy has a matching override key — `semanticOverride`, `summaryOverride`, `userPreferenceOverride`, `episodicOverride` — and each exposes the steps that strategy supports (`extraction`, `consolidation`, and for episodic, `reflection`). This tutorial overrides the **extraction** and **consolidation** steps of a `semanticOverride`.

> **The output schema is fixed.** Overrides change *instructions* and *model*, never the record shape. If you need a different schema, use a [self-managed strategy](../../../../03-self-managed-strategy/) instead.

## When to use overrides vs. built-in vs. self-managed

| | **Built-in** (tutorial 01) | **Override** (this tutorial) | **Self-managed** |
|---|---|---|---|
| Who runs extraction | AgentCore (managed model) | AgentCore (your model + prompt) | **You** (your Lambda) |
| IAM execution role | ❌ Not required | ✅ **Required** | ✅ Required |
| Record schema | Fixed (built-in) | Fixed (built-in) | **Anything you want** |
| Choose the model | ❌ | ✅ (any Bedrock model) | ✅ (incl. non-Bedrock) |
| Custom prompts | ❌ | ✅ extraction / consolidation / reflection | ✅ (your own code) |
| Bedrock billing | AgentCore's account | **Your** account + quotas | Your account |
| Setup | Minimal | Moderate | Advanced (S3 + SNS + Lambda) |

Reach for an **override** when the default extraction doesn't quite fit but you still want AgentCore to run the loop:

- **Domain-specific extraction** — capture only a narrow slice (clinical facts, financial events, legal entities) and ignore everything else. This tutorial's intake agent extracts medications, allergies, and conditions, and drops the small talk.
- **Compliance / model pinning** — regulation or policy requires a specific audited model version, or all inference must stay in-region.
- **Different language or jargon** — your users speak a language or domain vocabulary the default prompt handles poorly; write the extraction instructions to match.
- **Custom consolidation rules** — define how new records merge with old ones (e.g. "a severe allergy supersedes a mild one for the same substance").
- **Cost / latency tuning** — Haiku for high-volume, low-margin extraction; Sonnet for nuanced consolidation — independently per step.

Stay with **plain built-in strategies** when the defaults are fine (simpler, no IAM role). Graduate to **self-managed** only when you need a record schema the built-ins can't produce, a non-Bedrock model, or external lookups before deciding what to store.

## What it does

[`claude-sdk-ltm-custom-override.py`](./claude-sdk-ltm-custom-override.py):

1. Creates (or reuses) the **IAM execution role** that override strategies require — with a trust policy for `bedrock-agentcore.amazonaws.com` and `bedrock:InvokeModel` permission. Set `MEMORY_EXECUTION_ROLE_ARN` to reuse an existing role and skip the IAM writes.
2. Creates (or reuses) a memory resource with a **custom semantic override** — supplying our own `modelId` and `appendToPrompt` for both the **extraction** and **consolidation** steps, and passing `memory_execution_role_arn`.
3. Runs a first conversation with Claude through Amazon Bedrock, maintaining the `messages[]` array by hand and storing each turn with `create_event` — which queues the turn for extraction by *our* model under *our* prompt.
4. Polls until the asynchronous extraction surfaces records (override adds an extra model hop, so we wait up to ~2.5 min).
5. Retrieves the custom-extracted clinical facts with `retrieve_memories` and prints them — the non-clinical small talk should be absent.
6. Starts a **second session with an empty `messages[]` array**, injecting the retrieved records into the system prompt — and shows the agent already knows the patient's medications and allergies.
7. Deletes the memory resource in a `finally` block. (The IAM role is left in place — it's cheap and reusable.)

## Architecture

```
  ┌──────────────┐                              ┌─────────────────────────────────────┐
  │  Your code   │ ──── 1. create_event ──────▶ │  AgentCore Memory                   │
  │ (messages[]) │       (each turn)            │                                     │
  │              │                              │  short-term events ──┐              │
  │              │                              │                      │              │
  │              │                              │   2. async extraction│              │
  │              │                              │      via YOUR model  ▼              │
  │              │                              │      + YOUR prompt  ┌──────────────┐│
  │              │                              │      (semanticOverr-│ Bedrock model││
  │              │                              │       ide)          │ in your acct ││
  │              │                              │         ▲           └──────┬───────┘│
  │              │                              │         │ assumes          │        │
  │              │                              │   memoryExecutionRoleArn   ▼        │
  │              │ ◀─── 3. retrieve_memories ── │              long-term records      │
  │              │       (resolved namespace)   │              (custom-extracted)     │
  └──────┬───────┘                              └─────────────────────────────────────┘
         │
         │ 4. inject records into system prompt, then messages.create(...)
         ▼
  ┌──────────────┐
  │ Claude via   │  5. assistant reply, now grounded in custom-extracted long-term memory
  │ Amazon       │
  │ Bedrock      │
  └──────────────┘
```

The lifecycle is the same handful of calls as tutorial 01, plus the execution role and the override config:

| Step | Where | How |
|---|---|---|
| **Role** | Once, before create | IAM role with a `bedrock-agentcore.amazonaws.com` trust policy + `bedrock:InvokeModel` permission |
| **Create** | Once, at startup | `create_memory_and_wait(name=..., strategies=[{customMemoryStrategy: {configuration: {semanticOverride: {...}}}}], memory_execution_role_arn=...)` |
| **Store** | After each turn | `create_event(memory_id, actor_id, session_id, messages=[(text, "USER"), (text, "ASSISTANT")])` — identical to built-in; the override only changes how extraction runs |
| **Wait** | Before first retrieval | Poll `retrieve_memories` until records appear (extraction is asynchronous; overrides add a model hop) |
| **Retrieve** | On resume / new session | `retrieve_memories(memory_id, namespace="/patients/<actor>/clinical-facts/", query=..., top_k=...)` |
| **Inject** | Building the next prompt | Fold retrieved `content.text` records into the `system` prompt |

## The override config used here

```python
{
    "customMemoryStrategy": {            # StrategyType.CUSTOM.value
        "name": "ClinicalFactsOverride",
        "description": "Semantic extraction overridden for clinical intake facts",
        "namespaceTemplates": ["/patients/{actorId}/clinical-facts/"],
        "configuration": {
            "semanticOverride": {        # wraps the built-in Semantic strategy
                "extraction": {
                    "appendToPrompt": EXTRACTION_ADDENDUM,     # keep clinical facts only
                    "modelId": OVERRIDE_MODEL_ID,
                },
                "consolidation": {
                    "appendToPrompt": CONSOLIDATION_ADDENDUM,  # merge rules (severity, recency)
                    "modelId": OVERRIDE_MODEL_ID,
                },
            }
        },
    }
}
```

This is the exact shape the SDK's `MemoryClient.add_custom_semantic_strategy()` helper builds internally, and what the AWS docs document for the `CreateMemory` API. The other override keys (`summaryOverride`, `userPreferenceOverride`, `episodicOverride`) follow the same `{step: {appendToPrompt, modelId}}` structure; `episodicOverride` additionally supports a `reflection` step.

## Prerequisites

- Python 3.10+
- AWS credentials with **all three** of: AgentCore Memory permissions, Amazon Bedrock model-invocation permissions, and IAM permissions to create a role (`iam:CreateRole`, `iam:PutRolePolicy`, `iam:GetRole`). The `AnthropicBedrock` client, `MemoryClient`, and `boto3` IAM/STS clients all resolve credentials from the standard AWS chain.
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region — both for the conversation and for the override extraction/consolidation model. Request it in the Bedrock console under *Model access*. (`us-west-2` is a safe default.)
- An **IAM execution role is required** for override strategies. The script creates one named `AgentCoreMemoryOverrideExecutionRole`; to reuse an existing role instead, set `MEMORY_EXECUTION_ROLE_ARN` and the script skips all IAM writes.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

# Optional: reuse an existing execution role instead of creating one
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export MEMORY_EXECUTION_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/<your-memory-execution-role>"

# Optional: use a cheaper/different model for the override steps (defaults to Sonnet 4.6)
export OVERRIDE_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0

python claude-sdk-ltm-custom-override.py
```

Expected output: a first conversation where the patient mentions traffic (small talk), their medications, an allergy, and family history; a wait while extraction runs with the override model; the extracted **clinical facts** printed back (the traffic remark absent); then a "second session" — starting with an empty `messages[]` array — where the agent confirms the patient's medications and allergy using only the long-term memory injected into the system prompt. The script then deletes the memory resource.

> **Note on timing:** extraction is asynchronous and an override adds an extra Bedrock hop. The script polls for up to ~2.5 minutes; if records haven't surfaced by then it warns and continues (they typically appear shortly after). Re-running retrieval a little later will pick them up. This is expected behavior, not an error.

## Key implementation notes

- **Overrides require an IAM execution role.** AgentCore assumes it to invoke your chosen Bedrock model. Plain built-in strategies (tutorial 01) need no role; this is the main operational difference.
- **You pay for the override Bedrock calls.** Override extraction/consolidation invocations bill against *your* account and count against *your* Bedrock quotas. Throttling can cause ingestion to fail — request quota increases and enable memory log delivery to observe ingestion errors.
- **`appendToPrompt` is additive.** It's appended to the built-in system prompt; write instructions that *narrow* or *clarify* the default, not contradict it. The record schema is fixed.
- **`create_event` is unchanged.** The write path is identical to built-in strategies — the override only changes how AgentCore processes the stored events. There is no separate "extract" API.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept wildcards — substitute `{actorId}` yourself before calling.
- **The SDK is stateless, so memory is injected via the prompt.** Across sessions, a Claude agent "remembers" by retrieving long-term records and folding them into the `system` prompt — there is no server-side session on the Anthropic side.
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a turn raises. The IAM role is intentionally left in place (cheap and reusable); delete it manually if you no longer need it. Comment out the delete block to keep the memory between runs — `get_or_create_memory` will reuse it by name.

## Where to go next

- The override primitives and AWS CLI walkthrough (boto3 + SDK surfaces): [`../../../../02-strategy-overrides/`](../../../../02-strategy-overrides/)
- Own the whole pipeline instead — self-managed strategy (S3 + SNS + Lambda): [`../../../../03-self-managed-strategy/`](../../../../03-self-managed-strategy/)
- The long-term memory overview (all strategies, retrieval, namespaces): [`../../../../README.md`](../../../../README.md)
- The same override pattern in a framework: [`../../with-strands-agent/02-custom-hook/`](../../with-strands-agent/02-custom-hook/)
- Built-in strategies with the Claude SDK (start here): [`../01-built-in-strategies/`](../01-built-in-strategies/)
