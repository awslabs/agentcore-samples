"""Delete everything deploy.py created, also after a failed deploy. Usage: python cleanup.py"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
import deploy_utils as d

if __name__ == "__main__":
    config = d.load_config(HERE)
    for key in ("stateless", "stateful", "v1"):
        d.delete_runtime(config[key]["name"])
    d.delete_role(config["role"])
    d.delete_secret(config["secret"])
    for code_key in config["code_keys"]:
        d.delete_code(code_key)
    (HERE / d.CONFIG_FILE).unlink()
    print("✓ Cleanup complete")
