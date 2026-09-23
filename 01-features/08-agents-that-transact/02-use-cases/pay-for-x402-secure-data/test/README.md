# Tests

## Local Unit Tests

Local unit tests use mocks and do not require AWS credentials or live x402 payments:

```bash
cd 01-features/08-agents-that-transact/02-use-cases/pay-for-x402-secure-data
source .venv/bin/activate
PYTHONPATH="$PWD/agent/container" python -m unittest discover -s test/unit -p 'test_*.py' -v
```

These tests cover:

- FastAPI `/ping` and `/invocations`
- request payment context handling
- plugin-compatible HTTP 402 response shape and retry header pass-through
- registered x402 service operation validation and missing-payment-context behavior
- t54 x402-secure pass path storing request-scoped trust before target service payment
- low trust score and `is_scam=true` blocking before target payment
- missing, mismatched, low-score, or scam trust state blocking before target payment
- trust cache hits for repeated calls to the same target endpoint
- invalid service IDs, operations, or payloads failing before any trust-check or target payment
- agent tool wiring for trust check and registered x402 service calls

## Optional AWS/x402 Integration Prerequisite Gates

Integration prerequisite gates are skipped unless `RUN_AWS_X402_E2E=1`.

Only run them when all prerequisites are true:

- AWS account has AgentCore payments access.
- `CONFIRM_AWS_ACCOUNT_ID` matches the current caller identity.
- The current AgentCore payments beta service models are installed into your local AWS CLI/botocore model path.
- `.env` contains explicit test values.
- `PAY_TO` and `PAYMENT_AMOUNT` are set to small dedicated sample values.
- `PAYMENT_AMOUNT` is under `MAX_PAYMENT_AMOUNT_USDC`.

These gates do not execute a live paid data request by default. They verify configuration and safety prerequisites before you wire a funded public test endpoint through the runtime.

Normal integration output is redacted and must not include raw AWS responses, wallet addresses, account IDs, ARNs, session or instrument IDs, transactions, or payment proofs.

## Public Readiness Scan

Before publishing, scan the sample for sensitive artifacts. Brand and product terms such as `t54`, `x402-secure`, and `x402-secure-api.t54.ai` are expected and should not be treated as secrets.

```bash
cd 01-features/08-agents-that-transact/02-use-cases/pay-for-x402-secure-data
rg -n --hidden --glob '!.git' --glob '!.venv' --glob '!__pycache__' \
  'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|payment[ _-]proof|payment[P]roof|transaction[H]ash|wallet[ _-]address|account[ _-]id|payment_session_id=[A-Za-z0-9_-]+|payment_instrument_id=[A-Za-z0-9_-]+' .
```

Expected result: no live secrets, real account IDs, wallet addresses, ARNs, session or instrument IDs, transaction hashes, raw AWS responses, or payment proofs. The scan can still show public documentation phrases that describe what must not be committed; review those hits manually.
