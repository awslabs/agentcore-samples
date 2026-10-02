"""Delete the two Entra app registrations this sample created.

Run this LAST — after `deploy/teardown.py`. The gateway resource app is the
gateway's audience and the portal's login client, so deleting it while either
still exists breaks them in ways that look like configuration bugs.

Deletes by display name:
  - agentcore-consent-github-gateway  (gateway audience + portal login client)
  - agentcore-consent-github-frontend (the BFF's OIDC client)

Run from the sample root:
    python deploy/00_delete_entra_apps.py --yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

APP_NAMES = [
    "agentcore-consent-github-gateway",
    "agentcore-consent-github-frontend",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Delete without prompting.")
    args = parser.parse_args()

    if not shutil.which("az"):
        print("ERROR: Azure CLI (`az`) not found on PATH.", file=sys.stderr)
        sys.exit(1)

    found: list[tuple[str, str]] = []
    for name in APP_NAMES:
        proc = subprocess.run(
            ["az", "ad", "app", "list", "--display-name", name, "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"  ⚠ Could not list apps named {name}", file=sys.stderr)
            continue
        for app in json.loads(proc.stdout or "[]"):
            found.append((name, app["appId"]))

    if not found:
        print("Nothing to delete — no matching app registrations found.")
        return

    print("Will delete these Entra app registrations:")
    for name, app_id in found:
        print(f"  - {name} ({app_id})")
    if not args.yes and input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return

    for name, app_id in found:
        proc = subprocess.run(
            ["az", "ad", "app", "delete", "--id", app_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            print(f"  ✓ Deleted {name}")
        else:
            err = (proc.stderr or proc.stdout).strip().splitlines()
            print(f"  ⚠ Failed to delete {name}: {err[-1] if err else 'unknown'}", file=sys.stderr)


if __name__ == "__main__":
    main()
