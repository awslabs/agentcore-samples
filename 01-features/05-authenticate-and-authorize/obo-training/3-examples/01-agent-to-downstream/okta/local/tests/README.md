# Tests — Use Case 1 (Okta)

Same two-tier approach as the Entra tests:

| Tier | Runs by default? | Needs creds? | Needs network? | Needs IdP? |
|---|---|---|---|---|
| Unit | yes | no | no | no |
| Integration | no (skipped) | yes (AWS + Okta) | yes | yes |

## Running unit tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Unit tests validate:
- Env var validation.
- `ensure_okta_client_provider` creates a `CustomOauth2` provider *without* OBO config.
- `ensure_okta_actor_provider` creates one *with* `TOKEN_EXCHANGE` grant and `actorTokenContent=NONE`.
- JWT decoding.
- The callback server.
- `main()` in `02_run_example.py` passes Okta-specific OBO parameters: `audience` and `customParameters={"subject_token_type": ...}`.

## Running integration tests against real Okta

```bash
# One-time setup
python 01_create_providers.py
python generate_user_jwt.py   # sign in when browser opens

# Run
RUN_INTEGRATION=1 pytest tests/test_integration_obo.py -v
```

The integration test:
1. Loads the cached user JWT.
2. Wraps it via `GetWorkloadAccessTokenForJWT`.
3. Performs the OBO exchange with `ON_BEHALF_OF_TOKEN_EXCHANGE` + Okta-specific parameters.
4. Decodes the returned token and asserts:
   - `sub` matches the user's original `sub` (OBO identity preservation).
   - `cid` has changed from the native app's client ID to the service app's client ID (actor rotation).
   - The downstream scope appears in `scp`.

This is the primary OBO correctness assertion for Okta — same user, new actor, new scope.

## Why no downstream API call?

The Entra example calls Microsoft Graph to prove the token works end-to-end. For Okta, there's no universally-present downstream API, so the integration test stops at validating the returned token's claims. In your own deployment you would point the test at your actual API2 service.
