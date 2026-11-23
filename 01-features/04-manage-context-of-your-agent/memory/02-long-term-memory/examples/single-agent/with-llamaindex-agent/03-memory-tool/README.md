# Recall cross-session research facts with a LlamaIndex tool

How can an agent remember facts from a previous session **without adding every memory to every prompt**? This example writes a fictional research conversation to AgentCore Memory, lets a semantic strategy extract facts, and gives a new LlamaIndex `FunctionAgent` one read-only `FunctionTool` for recall. The model can supply a search string, not the memory ID, actor, strategy or namespace.

Four older scripts repeated the same pattern with research, legal, medical and investment prompts. This tutorial keeps one small research example, not domain-specific advice or a set of simulated month-long conversations. No Starter Toolkit, notebook, CLI deployment project or automatic teardown is required.

## Run the example

Use Python 3.13, a short-lived sandbox AWS profile, and a Region supporting AgentCore Memory and the selected Bedrock model. Set `AWS_PROFILE` and `AWS_DEFAULT_REGION`; check your account with `aws sts get-caller-identity`. Use fictional data only. Memory and Bedrock calls incur charges.

From the sample directory:

```bash
sample=$PWD
cd "$(mktemp -d)"; pwd  # keep the directory and receipt until cleanup succeeds
python3 -m venv .venv
.venv/bin/python -m pip install -r "$sample/requirements.txt"
.venv/bin/python "$sample/research_memory.py" init
.venv/bin/python "$sample/research_memory.py" seed
```

`init` creates a uniquely named memory with the semantic namespace `/research/{actorId}/` and retains its returned ID in `research-memory.json`. It does not adopt a fixed-name memory. The 30-day setting is **event** retention, not a lifetime guarantee for extracted records. `seed` writes one fictional user/assistant turn via LlamaIndex `AgentCoreMemory` using a new session ID.

```bash
.venv/bin/python "$sample/research_memory.py" recall --prompt 'orchard varieties'
.venv/bin/python "$sample/research_memory.py" ask --prompt 'Which varieties did we choose, and what do we measure?'
```

- `recall` calls the same LlamaIndex tool directly, **without** model inference. It searches the exact actor namespace `/research/demo-researcher/`, including its trailing slash. If no facts are available yet, check the memory strategy and retry later. Do not repeatedly seed data or assume a fixed sleep proves extraction completed.
- `ask` starts a fresh workflow with no previous chat messages. It uses Bedrock Converse and `search_research_memory` to answer from tool results; the example facts are Fuji and Gala apples and weekly rainfall in millimeters. Literal phrasing and model tool choices are not guaranteed. `demo-researcher` is a single-user fixture, not authentication. In a real app derive the actor from trusted identity and enforce access at the data plane.

The caller needs read/write event access and semantic retrieval for this memory (`CreateEvent`, `RetrieveMemoryRecords`); `ask` also needs model access to the indicated Haiku 4.5 inference profile and its foundation model destinations. `init`/`cleanup` additionally require Memory control-plane permissions. Do not grant administrator access.

## Reuse or clean up

To keep learning across sessions, keep the receipt and rerun `recall` or `ask`. Alternatively, use **a separate directory** and pass `--memory-id <approved-id>` to `seed`, `recall`, or `ask`. Use only a resource your owner approved for this data, with the same actor namespace and strategy. Supplying an ID never grants permission to delete it.

When finished, run `.venv/bin/python "$sample/research_memory.py" cleanup` in the **original** directory. Only the receipt's exact memory in the original account and Region is deleted, with a bounded wait. Do not delete by name or prefix. A failed/ambiguous create or teardown requires reconciliation with the original records; keep the receipt. If a process was killed, verify no operation is active before clearing the local `.lock`.

## Offline check and adjacent patterns

From the source directory, after installing the requirements, run `python -m unittest -v test_research_memory`. Tests use the real LlamaIndex adapter, agent/tool loop and SDK with controlled service responses. They don't demonstrate real background extraction.

For automatic retrieval on every turn, see [built-in memory blocks](../01-built-in-memory-block/) or [custom memory blocks](../02-custom-memory-block/). For only the current conversation, see the [short-term LlamaIndex examples](../../../../../01-short-term-memory/examples/single-agent/with-llamaindex-agent/). The [historical scripts](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/04-manage-context-of-your-agent/memory/02-long-term-memory/examples/single-agent/with-llamaindex-agent/03-memory-tool) are available for reference; do not run their teardown on reused resources.
