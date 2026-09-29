"""Create the Cedar policies that authorise tool calls per user.

Cedar surfaces the **inbound** token's claims as principal tags, so policies can read
the signed-in user even though the interceptor swaps the credential before the request
reaches the API. That combination was verified live; see ../scripts/spikes/FINDINGS.md.

Policies are read from ../policies/*.cedar. `{gateway_arn}` in a file is substituted
with the real gateway ARN.

Two API constraints worth knowing:

  * Policy names allow NO hyphens: ^[A-Za-z][A-Za-z0-9_]*$, max 48 chars. The file
    name is converted, so `read_only.cedar` becomes `read_only`.
  * DeletePolicy is asynchronous. Recreating the same name immediately fails with
    ConflictException, so --replace waits for the name to free up.

    python deploy/03_create_policies.py
    python deploy/03_create_policies.py --replace     # re-apply edited .cedar files
    python deploy/03_create_policies.py --list        # show what is deployed
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SAMPLE_ROOT, account_id, clients, load_env, must_env, region

POLICY_DIR = SAMPLE_ROOT / "policies"


def policy_name(path: Path) -> str:
    """File stem -> a name the API accepts (letters, digits, underscore only)."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", path.stem)
    if not name[:1].isalpha():
        name = f"p_{name}"
    return name[:48]


def deployed(aws, pe_id: str) -> dict:
    return {p["name"]: p for p in aws["acc"].list_policies(policyEngineId=pe_id).get("policies", [])}


def wait_gone(aws, pe_id: str, name: str) -> None:
    for _ in range(24):
        if name not in deployed(aws, pe_id):
            return
        time.sleep(5)
    print(f"  ⚠ {name} still present after 120s; the create below may conflict")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replace", action="store_true", help="Delete and recreate each policy.")
    ap.add_argument("--list", action="store_true", help="List deployed policies and exit.")
    args = ap.parse_args()
    load_env()
    aws = clients()
    pe_id = must_env("POLICY_ENGINE_ID", "Run deploy/02_create_gateway.py first.")
    gw_id = must_env("GATEWAY_ID")
    gw_arn = f"arn:aws:bedrock-agentcore:{region()}:{account_id()}:gateway/{gw_id}"

    if args.list:
        for name, pol in sorted(deployed(aws, pe_id).items()):
            detail = aws["acc"].get_policy(policyEngineId=pe_id, policyId=pol["policyId"])
            statement = ((detail.get("definition") or {}).get("cedar") or {}).get("statement", "")
            print(f"\n  {name}  status={pol.get('status')}  enforcement={pol.get('enforcementMode')}")
            for line in statement.strip().split("\n"):
                print(f"    {line}")
        return

    files = sorted(POLICY_DIR.glob("*.cedar"))
    if not files:
        sys.exit(f"no .cedar files in {POLICY_DIR}")
    print(f"policy engine {pe_id}\ngateway {gw_arn}\n")

    existing = deployed(aws, pe_id)
    for path in files:
        name = policy_name(path)
        statement = path.read_text().replace("{gateway_arn}", gw_arn)
        if name in existing:
            if not args.replace:
                print(f"  • {name} already deployed (use --replace to re-apply)")
                continue
            aws["acc"].delete_policy(policyEngineId=pe_id, policyId=existing[name]["policyId"])
            print(f"  deleting {name}")
            wait_gone(aws, pe_id, name)
        aws["acc"].create_policy(
            policyEngineId=pe_id,
            name=name,
            definition={"cedar": {"statement": statement}},
            enforcementMode="ACTIVE",
            validationMode="IGNORE_ALL_FINDINGS",
        )
        print(f"  ✓ {name}  ({path.name})")

    # A policy in CREATING is not yet enforced, so settle before anyone tests.
    for _ in range(24):
        states = {p["name"]: p.get("status") for p in deployed(aws, pe_id).values()}
        if "CREATING" not in states.values():
            break
        time.sleep(5)
    print("\n  deployed:")
    for name, status in sorted(states.items()):
        print(f"    {name:28} {status}")
    print("\n  Next: python deploy/04_create_obo_provider.py")


if __name__ == "__main__":
    main()
