"""Deploy three runtimes: the SDK v2 server in stateless and stateful mode, and an SDK v1 server.

Usage: python deploy.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
import deploy_utils as d

NAME = "mcp_e2e_compat"
STATELESS, STATEFUL, V1 = f"{NAME}_stateless", f"{NAME}_stateful", f"{NAME}_v1"
SECRET = f"{NAME}/request-state"
ROLE = f"agentcore-{NAME}-role"
CODE_KEY, CODE_KEY_V1 = f"{NAME}/code.zip", f"{NAME}/code_v1.zip"

if __name__ == "__main__":
    # Saved first, by name, so cleanup.py can delete whatever exists even if a later step fails.
    config = {
        "stateless": {"name": STATELESS},
        "stateful": {"name": STATEFUL},
        "v1": {"name": V1},
        "secret": SECRET,
        "role": ROLE,
        "code_keys": [CODE_KEY, CODE_KEY_V1],
    }
    d.save_config(HERE, config)
    d.ensure_bucket()
    # A 2026-07-28 client's elicitation is a multi round-trip request, and its rounds can reach different
    # microVMs, so both SDK v2 runtimes seal requestState with one shared key (as in 02-client-e2e).
    secret_arn = d.ensure_secret(SECRET)
    role_arn = d.ensure_role(ROLE, secret_arn=secret_arn)
    env = {"REQUEST_STATE_SECRET_ARN": secret_arn}

    # One SDK v2 code zip, deployed twice: the runtimes differ only in MCP_LEGACY_STATEFUL.
    d.upload_code(HERE, CODE_KEY)
    config["stateless"] = d.deploy_runtime(STATELESS, role_arn, CODE_KEY, "mcp_server.py", env=env)
    d.save_config(HERE, config)
    config["stateful"] = d.deploy_runtime(
        STATEFUL, role_arn, CODE_KEY, "mcp_server.py", env={**env, "MCP_LEGACY_STATEFUL": "true"}
    )
    d.save_config(HERE, config)
    # The existing server stays on SDK v1, so it gets its own requirements and none of the v2 helpers.
    d.upload_code(HERE, CODE_KEY_V1, agents="agents_v1", requirements="requirements-v1.txt", helpers=[])
    config["v1"] = d.deploy_runtime(V1, role_arn, CODE_KEY_V1, "mcp_server_v1.py")
    d.save_config(HERE, config)

    d.smoke_test(config["stateless"]["runtime_arn"])
    d.smoke_test(config["stateful"]["runtime_arn"])
    d.smoke_test(
        config["v1"]["runtime_arn"], version="2025-11-25"
    )  # the SDK v1 server answers 2026-07-28 with HTTP 400
    print("\nNext: python invoke.py")
