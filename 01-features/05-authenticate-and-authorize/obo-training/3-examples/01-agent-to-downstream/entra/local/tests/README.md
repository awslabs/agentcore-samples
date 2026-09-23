# Tests — Use Case 1 (Entra)

Two tiers of tests:

| Tier | Runs by default? | Needs creds? | Needs network? | Needs IdP? |
|---|---|---|---|---|
| Unit | yes | no | no | no |
| Integration | no (skipped) | yes (AWS + IdP) | yes | yes |

## Running unit tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

All unit tests mock boto3, webbrowser, and HTTP calls. They validate:
- Env var validation in both scripts.
- `ensure_workload_identity` and `ensure_microsoft_provider` happy-path and conflict-handling.
- JWT payload decoding (`decode_jwt_claims`).
- The local callback server's capture, error, and 404 paths.
- The end-to-end `main()` in `02_run_example.py` making the right AgentCore Identity calls in the right order.

## Running integration tests against real Entra ID

Integration tests exercise the actual OBO flow through AgentCore Identity. They are **gated** by `RUN_INTEGRATION=1` to keep them off your regular test runs.

### One-time setup

1. Complete `IDP_SETUP.md`.
2. Fill in `.env` from `config.example.env`.
3. Run `python 01_create_providers.py` to create the AgentCore resources.
4. Run `python generate_user_jwt.py` and sign in when the browser opens. This caches a user JWT to `.user-jwt-cache.json` (gitignored).

### Run

```bash
RUN_INTEGRATION=1 pytest tests/test_integration_obo.py -v
```

Integration tests will:
- Skip if the JWT cache is missing or expired (with a clear message).
- Call `GetWorkloadAccessTokenForJWT` with the cached user token.
- Perform the OBO exchange.
- Decode the returned Graph token and assert `aud` and `scp`.
- Call Graph `/me` with the OBO'd token and assert a 200 with a valid profile.

### When to re-run `generate_user_jwt.py`

- User JWTs typically last ~1 hour.
- The test suite will tell you when the cache is stale; just re-run the helper.

## CI note

If you want to run integration tests in CI, you'd need to:
1. Store the IdP client secret in a secret manager.
2. Provision the AgentCore workload + credential providers (one-time).
3. Either use an automated grant type like ROPC (Entra) to mint a user JWT inside CI, or pre-provision a long-lived test-user refresh token and exchange it at the start of each run.

The current setup treats integration tests as a developer-local capability. Extending to CI is out of scope for the training material.
