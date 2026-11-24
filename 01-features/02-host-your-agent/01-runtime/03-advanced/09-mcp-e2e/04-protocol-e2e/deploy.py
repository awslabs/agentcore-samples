"""Deploy the protocol demo with JWT inbound auth: Cognito user pool, IAM role, code zip, agent runtime.

Usage: python deploy.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
import deploy_utils as d
from mcp_client import HttpMcpClient, cognito_token

NAME = "mcp_e2e_protocol"
USER_POOL = NAME.replace("_", "-")
ROLE = f"agentcore-{NAME}-role"
CODE_KEY = f"{NAME}/code.zip"

if __name__ == "__main__":
    # Saved first, by name, so cleanup.py can delete whatever exists even if a later step fails.
    config = {"name": NAME, "user_pool": USER_POOL, "role": ROLE, "code_key": CODE_KEY}
    d.save_config(HERE, config)
    d.ensure_bucket()
    cognito = d.ensure_cognito(USER_POOL)
    # runtime_config.json holds the demo user's password; it's readable only by you and ignored by git.
    config["cognito"] = cognito
    d.save_config(HERE, config)
    role_arn = d.ensure_role(ROLE)
    d.upload_code(HERE, CODE_KEY)
    runtime = d.deploy_runtime(NAME, role_arn, CODE_KEY, "mcp_server.py", authorizer=d.jwt_authorizer(cognito))
    d.save_config(HERE, {**config, **runtime})
    token = cognito_token(d.REGION, cognito["client_id"], cognito["username"], cognito["password"])
    d.smoke_test(runtime["runtime_arn"], HttpMcpClient(runtime["runtime_arn"], d.REGION, token))
    print("\nNext: python invoke.py")
