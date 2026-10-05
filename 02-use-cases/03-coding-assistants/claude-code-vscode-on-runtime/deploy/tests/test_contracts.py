"""What deploy hands the other components must be what they accept: the runtime environment goes
through the box's own config code, and the edge Lambda's environment through the edge's own
settings check. Each test is skipped while that component isn't in the tree."""

import importlib.util
import json
import shutil
import subprocess

import pytest

ACCOUNT = "111122223333"
UID = "00uADA0000000000000A"
GATEWAY_URL = "https://devbox-tools-abcde12345.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
RUNTIME_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_vm_ada-abcdefghij"


def box_config(devbox):
    path = devbox.REMOTE / "box/rootfs/opt/devbox/lib/devbox/config.py"
    if not path.exists():
        pytest.skip("box/ isn't built yet")
    spec = importlib.util.spec_from_file_location("box_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["ada", "grace"])
def test_the_box_accepts_the_runtime_env(devbox, settings, name):
    cfg = box_config(devbox)
    user = devbox.User(name, {"ada": "Power", "grace": "Standard"}[name], f"{name}@example.com")
    env = devbox.runtime_env(
        settings,
        user,
        uid=UID,
        generation=3,
        account=ACCOUNT,
        start_url="https://d-1234567890.awsapps.com/start",
        gateway_url=GATEWAY_URL,
    )
    ini = cfg.aws_config(env)
    assert f"sso_role_name = ClaudeCode-{user.tier}\n" in ini and f"sso_account_id = {ACCOUNT}\n" in ini
    tier = cfg.tier_dropin(env["DEVBOX_MODELS"])
    assert tier["availableModels"] == list(devbox.TIER_MODELS[user.tier])
    assert tier["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    mcp = cfg.managed_mcp(env["DEVBOX_TOOLS_GATEWAY_URL"])
    assert mcp["mcpServers"]["web-search"]["args"][0] == GATEWAY_URL
    # the supervisor waits for a mount of this type; EFS shows up as nfs4
    supervisor = (devbox.REMOTE / "box/rootfs/opt/devbox/lib/devbox/supervisor.py").read_text()
    assert 'env.get("DEVBOX_WORKSPACE_FSTYPE")' in supervisor and "nfs4".startswith(env["DEVBOX_WORKSPACE_FSTYPE"])


def test_the_edge_accepts_the_lambda_env(devbox, settings, tmp_path):
    handler = devbox.REMOTE / "edge/src/handler.mjs"
    if not handler.exists() or not shutil.which("node"):
        pytest.skip("edge/ isn't built yet, or there's no node")
    env = devbox.lambda_env(
        devbox.browser_config(settings, "dwebview1234.cloudfront.net"),
        "dworkbench123.cloudfront.net",
        "dwebview1234.cloudfront.net",
    )
    assert json.loads(env["DEVBOX_CONFIG_JSON"])["provision"] == {"path": "/api/box", "header": "X-Devbox-Token"}
    script = tmp_path / "check.mjs"
    script.write_text(
        f"import {{ loadSettings }} from {json.dumps(handler.as_uri())};\n"
        "const env = JSON.parse(process.argv[2]);\n"
        f"const s = loadSettings(env, {{ serverRoot: {json.dumps(devbox.SERVER_ROOT)}, ovs: {{ commit: {json.dumps(devbox.COMMIT)} }} }});\n"
        "console.log(JSON.stringify({ wb: s.workbenchOrigin, wv: s.webviewOrigin, ac: s.agentcore.origin }));\n"
    )
    r = subprocess.run(["node", str(script), json.dumps(env)], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {
        "wb": "https://dworkbench123.cloudfront.net",
        "wv": "https://dwebview1234.cloudfront.net",
        "ac": "https://bedrock-agentcore.us-east-1.amazonaws.com",
    }
