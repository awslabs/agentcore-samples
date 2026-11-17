"""Deploy the finance reports server: DynamoDB table, IAM role, code zip, agent runtime.

Usage: python deploy.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
import deploy_utils as d

NAME = "mcp_e2e_utilities"
TABLE = f"{NAME}_finance"
ROLE = f"agentcore-{NAME}-role"
CODE_KEY = f"{NAME}/code.zip"
# A short idle timeout, so invoke.py --idle-check can show HealthyBusy keeping a session alive.
IDLE_TIMEOUT = 120

if __name__ == "__main__":
    # Saved first, by name, so cleanup.py can delete whatever exists even if a later step fails.
    config = {"name": NAME, "table": TABLE, "idle_timeout": IDLE_TIMEOUT, "role": ROLE, "code_key": CODE_KEY}
    d.save_config(HERE, config)
    d.ensure_bucket()
    table_arn = d.ensure_table(TABLE)
    role_arn = d.ensure_role(ROLE, table_arn=table_arn)
    d.upload_code(HERE, CODE_KEY)
    runtime = d.deploy_runtime(
        NAME, role_arn, CODE_KEY, "mcp_server.py", env={"FINANCE_TABLE": TABLE}, idle_timeout=IDLE_TIMEOUT
    )
    d.save_config(HERE, {**config, **runtime})
    d.smoke_test(runtime["runtime_arn"])
    print("\nNext: python invoke.py")
