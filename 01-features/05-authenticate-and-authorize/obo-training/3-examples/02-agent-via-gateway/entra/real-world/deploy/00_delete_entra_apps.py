"""
Delete the three Entra app registrations created by 00_create_entra_apps.py.

This is destructive on the Entra side — gone is gone. Re-running
00_create_entra_apps.py creates fresh apps with new client IDs and secrets.

The script:
  - Deletes by display name (the same names 00_create_entra_apps.py uses).
  - Skips apps that don't exist (idempotent).
  - Clears app-related values from .env so the next 00_create_entra_apps.py
    run starts clean.

Run:
    python deploy/00_delete_entra_apps.py [--yes]

Use --yes to skip the interactive confirmation. Otherwise the script asks
before each deletion.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


APP_DISPLAY_NAMES = [
    "agentcore-obo-uc2-frontend",
    "agentcore-obo-uc2-agent",
    "agentcore-obo-uc2-gateway",
]

ENV_KEYS_TO_CLEAR = [
    "FRONTEND_CLIENT_ID",
    "FRONTEND_CLIENT_SECRET",
    "AGENT_CLIENT_ID",
    "AGENT_CLIENT_SECRET",
    "GATEWAY_CLIENT_ID",
    "GATEWAY_CLIENT_SECRET",
    "AGENT_SCOPE",
    "GATEWAY_SCOPE",
]


def az_json(*args: str) -> object | None:
    proc = subprocess.run(
        ["az", *args, "--output", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def find_app(name: str) -> dict | None:
    apps = az_json("ad", "app", "list", "--display-name", name) or []
    return apps[0] if apps else None


def confirm(msg: str, *, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    answer = input(f"{msg} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def clear_env_value(env_path: Path, key: str) -> None:
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    prefix = f"{key}="
    changed = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}="
            changed = True
            break
    if changed:
        env_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", "-y", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args()

    if not shutil.which("az"):
        print("ERROR: Azure CLI (`az`) not found on PATH.", file=sys.stderr)
        sys.exit(1)

    account = az_json("account", "show")
    if not account or "tenantId" not in account:
        print("ERROR: Not signed in. Run `az login` first.", file=sys.stderr)
        sys.exit(1)
    print(f"Signed in to tenant {account['tenantId']}")

    real_world_root = Path(__file__).resolve().parent.parent
    env_path = real_world_root / ".env"

    deleted = 0
    for name in APP_DISPLAY_NAMES:
        app = find_app(name)
        if not app:
            print(f"  • {name}: not found")
            continue
        if not confirm(f"Delete app '{name}' (appId={app['appId']})?", auto_yes=args.yes):
            print(f"  • {name}: skipped")
            continue
        proc = subprocess.run(
            ["az", "ad", "app", "delete", "--id", app["appId"]],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"  ✓ {name}: deleted (appId={app['appId']})")
            deleted += 1
        else:
            print(f"  ✗ {name}: delete failed: {proc.stderr.strip() or proc.stdout.strip()}")

    if env_path.exists() and confirm("\nClear app-related values from .env?", auto_yes=args.yes):
        for key in ENV_KEYS_TO_CLEAR:
            clear_env_value(env_path, key)
        print("  ✓ Cleared *_CLIENT_ID, *_CLIENT_SECRET, AGENT_SCOPE, GATEWAY_SCOPE in .env")

    print()
    print(f"Done. {deleted} app(s) deleted.")
    if deleted:
        print("To recreate fresh: python deploy/00_create_entra_apps.py")


if __name__ == "__main__":
    main()
