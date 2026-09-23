# test/integration/

Operational scripts for the **Pay-For-API** use case. Run them from the
use-case root (`02-use-cases/01-pay-for-api/`); each script resolves its
paths relative to this folder, so it does not matter which directory you
invoke them from as long as the repo layout is intact.

Mirrors the pattern used by
[`agentcore-payments/test/integration/`](../../../../agentcore-payments/test/integration).

| Script | What it does |
|--------|--------------|
| `setup-roles.sh` | Creates the four IAM roles the notebook assumes into (`ControlPlane`, `Management`, `ProcessPayment`, `ResourceRetrieval`) with the separation-of-duties policy model described in the main [README](../../README.md). Idempotent — safe to re-run. Writes the role ARNs back into `.env`. |
| `setup-env.sh` | Interactive env setup. Copies `env-sample.txt` → `.env` on first run, then walks through the empty values (role ARNs, Coinbase CDP credentials, seller payout wallet) and prompts only for the ones that are still blank. Re-run with `--force-reprompt` to replace already-set values. |
| `deploy-seller.sh` | `npm install` the seller Lambda's `node_modules`, then `cdk bootstrap` (first run only) and `cdk deploy` the seller stack. Writes `seller/cdk/outputs.json` and prints `SellerApiUrl`. |
| `destroy-seller.sh` | `cdk destroy --force` the seller stack. |

## Typical order

```bash
# From 02-use-cases/01-pay-for-api/
bash test/integration/setup-roles.sh   # create IAM roles (once per account)
bash test/integration/setup-env.sh     # prompt for Coinbase creds + other secrets
bash test/integration/deploy-seller.sh # deploy the paid API
# paste SellerApiUrl into .env as SELLER_API_URL
jupyter notebook pay-for-api.ipynb
# …work through the notebook…
bash test/integration/destroy-seller.sh   # when done
```

The notebook's §3 also invokes `deploy-seller.sh` for you, so running the
script manually is optional — whichever is more comfortable.
