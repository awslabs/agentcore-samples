# LlamaIndex + AgentCore memory — long-term (memory-as-tool)

Four domain-specific Python tutorials show how to expose AgentCore's long-term memory search as a LlamaIndex `FunctionTool` the LLM can call. Cross-session persistence is achieved by reusing `actor_id` + `memory_id` across different `session_id`s, with a semantic memory strategy for automatic extraction. The examples use the supported `bedrock-agentcore` SDK, not Starter Toolkit.

| Script | Domain | Cross-session use case |
|---|---|---|
| `academic-research-assistant-long-term-memory-tutorial.py` | Academic research | Research evolution, grant-proposal support over months |
| `legal-document-analyzer-long-term-memory-tutorial.py` | Legal | Multi-case precedent tracking, 12-month event retention |
| `medical-knowledge-assistant-long-term-memory-tutorial.py` | Medical | Longitudinal patient care, treatment outcomes |
| `investment-portfolio-advisor-long-term-memory-tutorial.py` | Finance | Multi-quarter performance tracking |

## Integration pattern

This standalone illustration creates a new memory on its first run. For later sessions, set `AGENTCORE_MEMORY_ID` to the exact returned ID of that memory; do not select an existing memory by a name prefix. The four scripts use domain-specific names and namespace prefixes—keep the creation, context, and retrieval namespaces consistent within each script.

```python
import os
import uuid

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from llama_index.memory.bedrock_agentcore import AgentCoreMemoryContext

region = os.getenv("AWS_REGION", "us-east-1")
memory_client = MemoryClient(region_name=region)
actor_id = "advisor-id"
memory_id = os.getenv("AGENTCORE_MEMORY_ID")
created_here = False

if not memory_id:
    memory_name = f"DomainLongTerm_{uuid.uuid4().hex[:8]}"
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[
            {
                StrategyType.SEMANTIC.value: {
                    "name": "domainLongTerm",
                    "namespaces": ["/domain/{actorId}/"],
                }
            }
        ],
        event_expiry_days=365,
        max_wait=300,
        poll_interval=10,
    )
    memory_id = memory["id"]
    created_here = True
    print(f"Reuse this ID in later sessions: AGENTCORE_MEMORY_ID={memory_id}")

# Reuse the same actor and memory ID, but change session_id for each session.
context = AgentCoreMemoryContext(
    actor_id=actor_id,
    memory_id=memory_id,
    session_id="q1-session",
    namespace=f"/domain/{actor_id}/",
)
```

Reuse only a memory whose strategy writes to the matching namespace. `event_expiry_days=365` sets short-term **event** retention; it is not a lifetime limit for extracted long-term records. Creation errors and timeouts propagate: inspect the uniquely named resource before retrying rather than repeatedly creating resources.

Memory search is exposed as a `FunctionTool` wrapping `search_long_term_memories()`, using the same actor-specific namespace (for this illustration, `namespace_prefix=f"/domain/{actor_id}/"`). Extraction is asynchronous; the scripts allow processing time, but a fixed delay does not guarantee that records are ready.

### Cleanup ownership

Keep the memory when continuing the cross-session example. When finished, delete only the memory you created and no longer need. This guard deliberately leaves a supplied/reused memory untouched:

```python
if created_here:
    memory_client.delete_memory_and_wait(memory_id=memory_id, max_wait=300, poll_interval=10)
```

The guard applies to the current interpreter session. If creation timed out or you are returning in a new session, verify the exact resource ID and ownership before cleaning up; do not delete a memory just because its name matches a prefix.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore memory permissions
- Bedrock model access (default: `us.anthropic.claude-haiku-4-5-20251001-v1:0`, matching `MODEL_ID` in the scripts)
- `pip install -r requirements.txt`

## See also

- [AgentCore memory documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
- Short-term counterparts: [`../../../../../01-short-term-memory/examples/single-agent/with-llamaindex-agent/`](../../../../../01-short-term-memory/examples/single-agent/with-llamaindex-agent/)
- Built-in strategies primer: [`../../../../01-built-in-strategies/`](../../../../01-built-in-strategies/)

## Running the Python Scripts

Install this directory's dependencies:

```bash
pip install -r requirements.txt
```

Run each script directly:

```bash
python academic-research-assistant-long-term-memory-tutorial.py
python investment-portfolio-advisor-long-term-memory-tutorial.py
python legal-document-analyzer-long-term-memory-tutorial.py
python medical-knowledge-assistant-long-term-memory-tutorial.py
```
