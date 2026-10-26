# test/integration/

Operational scripts for the **Pay for Secure Data (x402)** use case. Run them
from the use-case root (`02-use-cases/pay-for-x402-secure-data/`); each script
resolves its paths relative to this folder, so it does not matter which
directory you invoke them from as long as the repo layout is intact.

Mirrors the pattern used by the sibling
[`pay-for-api-agent`](../../pay-for-api-agent/test/integration) use case.

| Script | What it does |
|--------|--------------|
| `setup-roles.sh` | Creates the four IAM roles the notebook assumes into (`ControlPlane`, `Management`, `ProcessPayment`, `ResourceRetrieval`, prefixed `AgentCoreX402SecureData`) with the separation-of-duties policy model described in the main [README](../../README.md). Verifies the caller against `CONFIRM_AWS_ACCOUNT_ID` and derives a narrow trusted setup principal (refuses account-root trust). Idempotent — safe to re-run. Writes the role ARNs back into `.env`. |
| `setup-env.sh` / `setup_env.py` | Seeds `.env` from `env-sample.txt` on first run and generates a fresh `USER_ID`. Idempotent — existing values are left alone. |
| `deploy-agent.sh` | Deploys the agent to AgentCore Runtime via CDK. The container image is built in **AWS CodeBuild**, so no local Docker is needed. Sources `.env` so `MANAGER_ARN` / `PAYMENT_CONNECTOR_ID` and the t54 x402-secure guardrail config flow into the runtime. Writes `agent/cdk/outputs.json`. |
| `destroy-agent.sh` | `cdk destroy --force` the agent runtime stack. |
| `e2e-test.sh` | **Prerequisite gate** (skipped unless `RUN_AWS_X402_E2E=1`). Checks account confirmation, required config, amount caps, and SDK/CLI availability. Does **not** execute a live paid request. |
| `x402_agentcore_test.py` | Python prerequisite gate — the same account-confirmation, required-config, and amount-cap checks as `e2e-test.sh`. Does **not** execute a live paid request. |

## Typical order

```bash
# From 02-use-cases/pay-for-x402-secure-data/
bash test/integration/setup-env.sh      # seed .env + USER_ID (once)
# fill in .env: Coinbase CDP keys, INSTRUMENT_EMAIL, CONFIRM_AWS_ACCOUNT_ID
bash test/integration/setup-roles.sh    # create IAM roles (once per account)
jupyter notebook pay-for-x402-secure-data.ipynb
# …work through the notebook (creates Payments resources, runs the flow)…
bash test/integration/deploy-agent.sh   # deploy the agent runtime (optional)
bash test/integration/destroy-agent.sh  # when done
```

The notebook's §2 seeds `.env` and runs `setup-roles.sh` for you, and §8 runs
`deploy-agent.sh`, so running the scripts by hand is optional — whichever is
more comfortable.

## Prerequisite gates

The `RUN_AWS_X402_E2E=1` gates are safety checks you run **before** wiring a
funded test endpoint through the runtime — never a live payment by
themselves:

```bash
source .venv/bin/activate
python -m pip install -r test/integration/requirements.txt
RUN_AWS_X402_E2E=1 bash test/integration/e2e-test.sh
RUN_AWS_X402_E2E=1 PYTHONPATH="$PWD/agent/container" python test/integration/x402_agentcore_test.py
```
