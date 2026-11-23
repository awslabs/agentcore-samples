# A2A Distributed Deployment

Deploys each sub-agent as a **standalone A2A server**. The supervisor graph connects to them as remote clients using the same `GraphBuilder` topology as the local demo (`demo/run_demo.py`).

## Architecture

```
┌────────────────────┐
│  Supervisor (main)  │  ← runs the graph with A2AAgent remote clients
└────────┬───────────┘
         │ A2A protocol
    ┌────┼────┬──────────┐
    ▼    ▼    ▼          ▼
 :9001 :9002 :9003     (router runs locally
  Res.  Ana.  Writer    inside the supervisor)
```

## Start Sub-Agent Servers

In separate terminals:

```bash
uv run python -m deployment.serve_research   # http://0.0.0.0:9001
uv run python -m deployment.serve_analysis   # http://0.0.0.0:9002
uv run python -m deployment.serve_writer     # http://0.0.0.0:9003
```

## Run the Supervisor

```bash
uv run python -m deployment.main --prompt "Research the impact of AI on healthcare"
```

### Custom Endpoints

```bash
export RESEARCH_AGENT_URL=http://research-host:9001
export ANALYSIS_AGENT_URL=http://analysis-host:9002
export WRITER_AGENT_URL=http://writer-host:9003

uv run python -m deployment.main --prompt "..."
```

## JSON Output

```bash
uv run python -m deployment.main --prompt "..." --json
```
