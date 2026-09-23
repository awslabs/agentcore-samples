# Long-term memory — LlamaIndex single-agent

Three integration patterns matching the LlamaIndex model.

| Pattern | Folder | Notes |
|---|---|---|
| **Built-in memory block** — a custom `BaseMemoryBlock` wired into the `Memory` class; the framework calls it automatically (retrieve every turn, persist on flush) | [`01-built-in-memory-block/`](./01-built-in-memory-block/) | Personal Knowledge Assistant — recall user facts across sessions |
| **Custom memory block** — a sophisticated block that owns its extraction: conditional storage, client-side distillation, score-filtered retrieval | [`02-custom-memory-block/`](./02-custom-memory-block/) | Customer Support Assistant — self-managed extraction in-process |
| **memory-as-tool** — memory ops exposed as LlamaIndex tools the LLM invokes | [`03-memory-tool/`](./03-memory-tool/) | Four domain examples: academic research, investment advisor, legal doc analyzer, medical knowledge |

> **API note:** the block-based patterns (01, 02) use LlamaIndex's current `Memory` +
> `BaseMemoryBlock` API. `ChatMemoryBuffer` is **deprecated** and not used. Each block
> implements `_aget` (retrieve, every turn) and `_aput` (persist, on short-term flush).

See the [long-term memory README](../../../README.md) for the underlying strategies and APIs.

## Running the Python Scripts

Navigate into each sub-folder, install its requirements, and run the script:

```bash
# 01-built-in-memory-block/
cd 01-built-in-memory-block
pip install -r requirements.txt
python llamaindex-ltm-built-in-memory-block.py
```

```bash
# 02-custom-memory-block/
cd 02-custom-memory-block
pip install -r requirements.txt
python llamaindex-ltm-custom-memory-block.py
```

```bash
# 03-memory-tool/
cd 03-memory-tool
pip install -r requirements.txt
python academic-research-assistant-long-term-memory-tutorial.py
python investment-portfolio-advisor-long-term-memory-tutorial.py
python medical-knowledge-assistant-long-term-memory-tutorial.py
```

