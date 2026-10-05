# local-test: the whole stack on a laptop, no AWS

Local stand-ins for Okta and AgentCore, so the real box image and the real edge run together on your laptop,
and a headless Chrome drives the browser end to end. No AWS calls, no credentials.

```
Chrome ──► workbench site :9402, webview site :9403   (the edge's handler, locally)
       ──► fake Okta :9400                            (OIDC, PKCE)
       ──► fake AgentCore :9401 ──► the box :8080 ──► VS Code
```

## Run it

Needs Docker Desktop, Node.js 22+, and Google Chrome.

```bash
./run.sh test     # unit tests of the fakes (no Docker)
./run.sh up       # build and start everything; then open http://localhost:9402/ (signed in as Ada)
./run.sh e2e      # a fresh stack, then the headless Chrome end-to-end test
./run.sh logs     # follow the logs
./run.sh down     # stop it and delete its workspace volume
```

To be someone else, start with `FAKE_OKTA_USER=grace ./run.sh up`. If a port is taken, `run.sh` picks a free
one in 9404-9419 and says so.

## Files

| Path | What |
|---|---|
| `run.sh`, `compose.yaml` | the stack |
| `fake-okta/` | an OIDC provider for tests |
| `fake-agentcore/` | a stand-in for the AgentCore Runtime data plane: the authorizer, sessions, `/ws`, limits |
| `edge-local/` | runs the edge's handler as the two sites |
| `e2e/` | the headless Chrome test |
| `generated/` | rendered config and logs (git-ignored) |
