# Verification of Results

The SRE Agent includes tools for verifying that investigation results are accurate and based on actual data rather than hallucinated information.

## Ground Truth Verification

For result verification, we provide a data dump utility that creates a comprehensive ground truth dataset:

```bash
# Generate complete data dump for verification
cd backend/scripts
./dump_data_contents.sh
```

This script processes all files in the [`backend/data`](../backend/data) directory (including `.json`, `.txt`, and `.log` files) and creates a comprehensive dump at [`backend/data/all_data_dump.txt`](../backend/data/all_data_dump.txt). This file serves as ground truth for verifying that agent responses are factual and not fabricated.

## Report Verification

The [`reports`](../reports) folder contains investigation reports for several example queries. You can verify these reports against the ground truth data using the LLM-as-a-judge verification system:

```bash
# Verify a specific report against ground truth
python verify_report.py --report reports/example_report.md --ground-truth backend/data/all_data_dump.txt
```

## Example Verification Workflow

```bash
# 1. Generate an investigation report
sre-agent --prompt "Why are the payment-service pods crash looping?"

# 2. Create ground truth data dump
cd backend/scripts && ./dump_data_contents.sh && cd ../..

# 3. Verify the report contains only factual information
python verify_report.py --report reports/your_report_.md --ground-truth backend/data/all_data_dump.txt
```

>**⚠️ Important Note**: The system prompts and agent logic in [`sre_agent/agent_nodes.py`](../sre_agent/agent_nodes.py) require further refinement before production use. This implementation demonstrates the architectural approach and provides a foundation for building production-ready SRE agents, but the prompts, error handling, and agent coordination logic need additional tuning for real-world reliability.