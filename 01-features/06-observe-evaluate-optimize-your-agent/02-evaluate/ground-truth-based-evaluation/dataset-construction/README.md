# Dataset Construction via Actor Simulation

Build an evaluation dataset by simulating realistic multi-turn employee conversations. An LLM "actor" drives each conversation as an employee with a specific HR request, interacting with the deployed HR Assistant agent. The resulting sessions are then submitted to AgentCore for batch evaluation.

## What You'll Learn

| Concept | Description |
|---|---|
| **Actor-based simulation** | Use an LLM to role-play employees, generating natural multi-turn conversations automatically |
| **Controlled ground truth** | Attach assertions and expected behavior to each simulated scenario |
| **Batch evaluation** | Submit all simulated sessions at once via `start_batch_evaluation` |
| **Session metadata** | Pass per-session ground truth (assertions) to the batch evaluator |

## Why Simulate?

Real user sessions often lack ground truth — you don't know what the employee expected. Simulation gives you:

- **Controlled scenarios**: define exactly what the employee wants
- **Reproducible tests**: same persona, same goal, predictable behavior
- **Ground truth at scale**: attach assertions to every session automatically
- **No real users needed**: generate evaluation datasets before production launch

## Agent: HR Assistant

The HR Assistant agent has 5 tools over deterministic mock data:

| Tool | Description |
|---|---|
| `get_pto_balance` | Return remaining PTO days for an employee |
| `submit_pto_request` | Submit a time-off request |
| `lookup_hr_policy` | Look up company HR policy by topic |
| `get_benefits_summary` | Return benefit details (health, dental, vision, 401k, life insurance) |
| `get_pay_stub` | Retrieve a pay stub for a given pay period |

## Setup

### 1. Deploy the HR Assistant Agent

The HR Assistant is shared across all evaluation tutorials. Deploy it once from `../../utils/`:

```bash
cd ../../utils
pip install -r requirements.txt
python deploy.py
```

This writes `../../utils/agent_config.json`, which `simulate.py` reads automatically.

### 2. Run Simulated evaluation

```bash
pip install -r requirements.txt
python simulate.py
```

Optional flags:

```bash
# Preview scenarios without invoking the agent
python simulate.py --dry-run

# Use a specific region
python simulate.py --region us-west-2

# Point to a custom agent config
python simulate.py --config /path/to/agent_config.json
```

## How Simulation Works

### Actor Loop

For each scenario, the script runs a turn-by-turn simulation:

```
employee sends first_input ──► HR Assistant agent
                                          │
              ◄── agent_response ◄────────┘
                          │
              Actor LLM generates next employee message
              (based on persona + goal + full transcript)
                          │
              employee sends next_message ──► agent
                          │
              (repeat until goal_complete or max_turns)
```

The actor LLM is Claude Haiku (`us.anthropic.claude-haiku-4-5-20251001-v1:0`). It receives the full conversation transcript and generates natural employee responses. When it determines the goal is complete, it ends the conversation.

### Scenarios

Five scenarios cover the main HR Assistant use cases:

| Scenario | Employee Goal | Max Turns |
|---|---|---|
| `sim-pto-balance` | Check remaining PTO balance for EMP-001 | 4 |
| `sim-pto-request` | Submit PTO request for July 14–18, 2026 | 4 |
| `sim-remote-work-policy` | Understand remote work days per week | 4 |
| `sim-benefits-inquiry` | Learn about 401k matching details | 4 |
| `sim-pay-stub` | Retrieve January 2026 pay stub for EMP-001 | 4 |

### Batch evaluation

After all sessions are recorded in CloudWatch, a single `start_batch_evaluation` call submits them all. Each session's assertions are attached as `sessionMetadata.groundTruth`:

```python
bac.start_batch_evaluation(
    batchEvaluationName="hr_simulated_<id>",
    evaluators=[
        {"evaluatorId": "Builtin.GoalSuccessRate"},
        {"evaluatorId": "Builtin.Helpfulness"},
        {"evaluatorId": "Builtin.Correctness"},
    ],
    dataSourceConfig={
        "cloudWatchLogs": {
            "logGroupNames": ["aws/spans", "/aws/bedrock-agentcore/runtimes/..."],
            "filterConfig": {"sessionIds": [...]},
        }
    },
    evaluationMetadata={
        "sessionMetadata": [
            {
                "sessionId": "...",
                "groundTruth": {
                    "inline": {
                        "assertions": [{"text": "Agent called get_pto_balance"}, ...]
                    }
                }
            }
        ]
    },
)
```

## Expected Output

```
[1/3] Running 5 simulated scenarios ...

  [sim-pto-balance] session=3f8a2b1c...
    turn 1  employee > Hi, I'd like to check my PTO balance. My employee ID is EMP-001.
    turn 1  agent    < Employee EMP-001 has 10 remaining PTO days out of 15 total ...
    turn 2  employee > Great, that's all I needed to know. Thanks!
    [sim-pto-balance] Actor signalled goal complete.

[2/3] Submitting batch evaluation ...
  Batch name    : hr_simulated_a1b2c3d4
  Sessions      : 5
  Evaluators    : ['Builtin.GoalSuccessRate', 'Builtin.Helpfulness', 'Builtin.Correctness']
  batchEvaluationId: xxxx-xxxx-xxxx

[3/3] Polling batch evaluation status ...
  status = IN_PROGRESS
  status = COMPLETED

  Sessions completed : 5
  Sessions failed    : 0

  Evaluator                           Avg Score    Evaluated    Failed
  -----------------------------------------------------------------------
  Builtin.GoalSuccessRate             0.900        5            0
  Builtin.Helpfulness                 0.920        10           0
  Builtin.Correctness                 0.950        10           0
```

## Results Files

| File | Contents |
|---|---|
| `results/simulation_results.json` | Full transcripts for all simulated sessions |
| `results/batch_eval_results.json` | Batch evaluation ID, status, and per-evaluator summaries |

## Customizing Scenarios

Add your own scenarios to the `SCENARIOS` list in `simulate.py`:

```python
{
    "scenario_id": "sim-parental-leave",
    "scenario_description": "A new parent wants to understand parental leave options.",
    "actor_profile": {
        "traits": {"expectant_parent": True, "detail_oriented": True},
        "context": "Employee expecting a child wants to know about parental leave policy.",
        "goal": "Learn how many weeks of paid parental leave are available for primary caregivers.",
    },
    "first_input": "I'm expecting a child and want to understand our parental leave policy. How much time off do I get?",
    "max_turns": 4,
    "assertions": [
        "Agent calls lookup_hr_policy for parental_leave",
        "Agent reports 16 weeks for primary caregivers",
        "Agent mentions benefits continue during leave",
    ],
}
```
