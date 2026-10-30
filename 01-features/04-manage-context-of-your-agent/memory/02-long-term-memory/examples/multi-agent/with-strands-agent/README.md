# Long-term memory — multi-agent (Strands)

| Pattern | Folder | Example |
|---|---|---|
| **Built-in hook** | [`01-built-in-hook/`](./01-built-in-hook/) | `travel-booking-agent/travel-booking-assistant.py` — multiple agents share a memory resource via the built-in hook |
| **Custom hook** | [`02-custom-hook/`](./02-custom-hook/) | `healthcare-assistant-using-episodic/healthcare-data-assistant.py` — per-agent memory scoping with episodic strategy |

See also: short-term multi-agent branching in [`../../../../01-short-term-memory/examples/multi-agent/with-strands-agent/multi-agent-parallel-branches/`](../../../../01-short-term-memory/examples/multi-agent/with-strands-agent/multi-agent-parallel-branches/).

## Run

Install dependencies and run scripts from the relevant sub-folders:

```bash
# Install dependencies (in each sub-folder)
pip install -r requirements.txt

python 01-built-in-hook/travel-booking-agent/travel-booking-assistant.py
python 02-custom-hook/healthcare-assistant-using-episodic/healthcare-data-assistant.py
```
