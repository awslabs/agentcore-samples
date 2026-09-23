# LlamaIndex + AgentCore memory — long-term (memory-as-tool)

Four domain-specific notebooks showing how to expose AgentCore's long-term memory search as a LlamaIndex `FunctionTool` the LLM can call. Cross-session persistence is achieved by reusing `actor_id` + `memory_id` across different `session_id`s, with a `SemanticStrategy` for automatic extraction.

| Notebook | Domain | Cross-session use case |
|---|---|---|
| `academic-research-assistant-long-term-memory-tutorial.py` | Academic research | Research evolution, grant-proposal support over months |
| `legal-document-analyzer-long-term-memory-tutorial.py` | Legal | Multi-case precedent tracking, 12-month retention |
| `medical-knowledge-assistant-long-term-memory-tutorial.py` | Medical | Longitudinal patient care, treatment outcomes |
| `investment-portfolio-advisor-long-term-memory-tutorial.py` | Finance | Multi-quarter performance tracking |

## Integration pattern

```python
from llama_index.memory.bedrock_agentcore import AgentCoreMemory, AgentCoreMemoryContext
from bedrock_agentcore_starter_toolkit.operations.memory.manager import MemoryManager
from bedrock_agentcore_starter_toolkit.operations.memory.models.strategies import SemanticStrategy

memory = memory_manager.get_or_create_memory(
    name="DomainLongTerm",
    strategies=[SemanticStrategy(name="domainLongTerm")],
    event_expiry_days=365,
)

# Same actor across sessions, different session_id per interaction
context = AgentCoreMemoryContext(
    actor_id="advisor-id",
    memory_id=memory.id,
    session_id="q1-session",
    namespace="/domain/",
)
```

Memory search is exposed as a `FunctionTool` wrapping `search_long_term_memories()`. Extracted records are available ~90–120 s after events are written.

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

Install dependencies (if a `requirements.txt` is present):

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
