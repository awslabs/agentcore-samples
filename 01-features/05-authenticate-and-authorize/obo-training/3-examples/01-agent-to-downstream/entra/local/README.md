# Use Case 1 — Entra ID flavor

User → Frontend → Agent on Runtime → Microsoft Graph `/me`, all under Microsoft Entra ID.

## What happens when you run this

The `02_run_example.py` script is an **interactive, guided walkthrough** — not just a script that does a thing. It's structured as five chapters:

| Chapter | What you'll learn |
|---|---|
| 1. Sign the user in | How AgentCore Identity stands in for a frontend to produce a user JWT |
| 2. Inspect the inbound user JWT | What `aud`, `oid`, `scp`, `appid` mean and why they matter |
| 3. Perform the OBO exchange | The two AgentCore API calls and what they do under the hood |
| 4. Compare inbound vs outbound tokens | Side-by-side diff showing exactly what OBO changed (and what it preserved) |
| 5. Call Graph with the OBO token | End-to-end proof: the token actually works on Microsoft Graph |

Each chapter prints an explanation, runs the API call, shows the result, then highlights the key learning moment. It pauses between chapters so you have time to read.

**Environment knobs:**

| Env var | Effect |
|---|---|
| `INTERACTIVE_NO_PAUSE=1` | Skip all "Press Enter" pauses — runs end-to-end without waiting. Useful for CI / demos. |
| `NO_COLOR=1` | Disable ANSI color codes. |

## Files

- [`IDP_SETUP.md`](./IDP_SETUP.md) — step-by-step Entra app registration
- `config.example.env` — env var template
- `requirements.txt` — Python dependencies
- `01_create_providers.py` — creates the AgentCore credential providers (run once)
- `02_run_example.py` — the full end-to-end run
- `callback_server.py` — minimal HTTP callback for the 3LO flow

## Quick start

```bash
# 1. Complete the IdP setup in IDP_SETUP.md and note the IDs/secrets.

# 2. Set up env
cp config.example.env .env
# Edit .env with your values

# 3. Install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. One-time: create credential providers in AgentCore Identity
python 01_create_providers.py

# 5. Run the end-to-end flow
python 02_run_example.py
```

After the run, you should see your Microsoft 365 profile printed, the inbound (user) JWT's decoded claims, and the outbound (Graph) JWT's decoded claims — proving the OBO exchange worked.

## What to look for in the output

- Inbound token `aud` = your **agent app's** client ID.
- Outbound token `aud` = `00000003-0000-0000-c000-000000000000` (the Graph resource ID).
- Both tokens have the same `oid` (Entra's stable user id) — this is how the user identity is preserved across the exchange.
- The outbound token's `appid` / `azp` is your agent app — that's the `act` equivalent in Entra's flavor.
