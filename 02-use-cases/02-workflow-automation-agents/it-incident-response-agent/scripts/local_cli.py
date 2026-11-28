#!/usr/bin/env python3
"""Local credential configuration using this sample's private CLI HOME.

No deploy or invocation is accepted here. Use the target-bound lifecycle wrapper.
"""

import os
import subprocess
import sys

from lifecycle import ROOT, cli_environment


def main():
    args = sys.argv[1:]
    if args != ["--version"] and args[:3] != ["add", "credentials", "oauth"]:
        raise SystemExit("Only --version or local add credentials oauth is supported")
    if args != ["--version"] and ((ROOT / ".sample-deployment.json").exists() or (ROOT / "agentcore/.cli").exists()):
        raise SystemExit("Configure credentials only before a fresh deployment")
    result = subprocess.run(
        [os.environ.get("AGENTCORE_CLI", "agentcore"), *args],
        cwd=ROOT,
        env=cli_environment(ROOT),
        timeout=120,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
