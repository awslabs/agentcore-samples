"""Opt-in native CLI builds in a disposable project, not injected CDK Stack props.

RUN_CDK_TESTS=1 requires npm dependencies, AgentCore CLI 1.0.3, uv and cached
Linux ARM64 wheels. Run in a denied-external-network environment; see DEPLOYMENT.md.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_infrastructure import load_stack, resolve
from test_infrastructure import (
    test_boundary_is_attached_and_scopes_model_asset_and_logs as check_boundary,
)

ROOT = Path(__file__).resolve().parents[1]
KEY = "agentcoreSampleAssetKmsKeyArn"
ARN = "arn:aws:kms:us-west-2:111122223333:key/00000000-0000-0000-0000-000000000000"
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CDK_TESTS") != "1", reason="Opt in with RUN_CDK_TESTS=1; see DEPLOYMENT.md"
)


def uv_directory(*args, env=None):
    """Ask the installed uv for its locations; never guess a platform-specific default."""
    uv = shutil.which("uv", path=env["PATH"] if env else None)
    assert uv is not None, "Install uv and keep it on PATH"
    result = subprocess.run(  # noqa: S603 -- fixed uv path queries, no shell
        [uv, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=True
    )
    return Path(result.stdout.strip())


@pytest.fixture
def project(tmp_path):
    # Resolve defaults/overrides BEFORE changing HOME. Keep the caller's tool PATH;
    # uv-managed Python may live outside it, so preserve uv's installation root too.
    cache_dir = uv_directory("cache", "dir").resolve()
    python_dir = uv_directory("python", "dir").resolve()
    sample = tmp_path / "sample"
    # Allowlist source inputs; never copy local .env, credentials, targets or deployed state.
    for relative in (
        "agentcore/agentcore.json",
        "agentcore/cdk/bin/cdk.ts",
        "agentcore/cdk/lib/cdk-stack.ts",
        "agentcore/cdk/cdk.json",
        "agentcore/cdk/package.json",
        "agentcore/cdk/package-lock.json",
        "agentcore/cdk/tsconfig.json",
        "app/agent/main.py",
        "app/agent/pyproject.toml",
        "app/agent/uv.lock",
    ):
        destination = sample / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    (sample / "agentcore/cdk/node_modules").symlink_to(ROOT / "agentcore/cdk/node_modules", target_is_directory=True)
    # Do not inherit a real AWS profile, CDK context, target, deployed state or HOME.
    env = {"PATH": os.environ["PATH"]}
    env.update(
        UV_CACHE_DIR=str(cache_dir),
        UV_PYTHON_INSTALL_DIR=str(python_dir),
        HOME=str(tmp_path / "home"),
        CDK_HOME=str(tmp_path / "home/.cdk"),
        TMPDIR=str(tmp_path),
        AWS_CONFIG_FILE=str(tmp_path / "absent-config"),
        AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "absent-credentials"),
        AWS_EC2_METADATA_DISABLED="true",
        AWS_REGION="us-west-2",
        AWS_DEFAULT_REGION="us-west-2",
        UV_OFFLINE="true",
        UV_PYTHON_DOWNLOADS="never",
        NPM_CONFIG_OFFLINE="true",
        NPM_CONFIG_CACHE=str(tmp_path / "npm-cache"),
        AGENTCORE_DISABLE_KEYCHAIN="1",  # CLI 1.0.3 LocalSecrets guard, not global config routing.
        CDK_DISABLE_CLI_TELEMETRY="1",  # CDK CLI telemetry opt-out.
        PYTHONDONTWRITEBYTECODE="1",
        NODE_OPTIONS="--max-old-space-size=2048",
    )
    Path(env["HOME"]).mkdir()

    def command(*args, success=True):
        # Fixed local CLI commands only; subprocess output is retained on assertion failure.
        result = subprocess.run(args, cwd=sample, env=env, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result.stdout + result.stderr

    # The actual CLI global config is HOME/.agentcore/config.json, not AGENTCORE_CONFIG_DIR.
    command("agentcore", "config", "telemetry.enabled", "false")
    assert json.loads(command("agentcore", "config", "telemetry.enabled")) is False
    global_config = json.loads((Path(env["HOME"]) / ".agentcore/config.json").read_text())
    assert global_config["telemetry"]["enabled"] is False
    assert command("agentcore", "--version").splitlines()[0].strip() == "1.0.3"
    command("uv", "python", "find", "3.13", "--no-project")
    return sample, env, command


def configure(sample, value):
    path = sample / "agentcore/cdk/cdk.json"
    config = json.loads(path.read_text())
    config["context"][KEY] = value
    path.write_text(json.dumps(config))


def built_stack(sample):
    return load_stack(sample / "agentcore/cdk/cdk.out")


def check_kms(stack, expected):
    resources, region = stack
    boundary = next(r for r in resources.values() if r["Type"] == "AWS::IAM::ManagedPolicy")
    role = next(r for r in resources.values() if r["Type"] == "AWS::IAM::Policy")
    grants = []
    for policy in (boundary, role):
        decrypt = [s for s in policy["Properties"]["PolicyDocument"]["Statement"] if s["Action"] == "kms:Decrypt"]
        assert len(decrypt) == (0 if expected is None else 1)
        if decrypt:
            assert decrypt[0]["Resource"] == expected
            assert resolve(decrypt[0]["Condition"]["StringEquals"]["kms:ViaService"]) == f"s3.{region}.amazonaws.com"
            grants.append(decrypt[0])
    if grants:
        assert grants[0] == grants[1]  # Exact key and both S3 encryption contexts in role AND boundary.
    check_boundary(stack)


def test_persisted_context_survives_fresh_native_builds(project):
    sample, _, command = project
    configure(sample, ARN)
    for _ in range(2):
        # Remove only this fixture's products: every process must rediscover persisted context.
        for relative in ("agentcore/cdk/cdk.out", "agentcore/cdk/dist", "agentcore/.cache"):
            shutil.rmtree(sample / relative, ignore_errors=True)
        command("agentcore", "build", "--json")
        check_kms(built_stack(sample), ARN)


def test_documented_native_command_with_default_uv_locations(project, tmp_path):
    """A normal uv user need not export cache/install paths to run the opt-in command."""
    _, isolated, _ = project
    home = tmp_path / "normal-user"
    home.mkdir()
    env = dict(isolated, HOME=str(home), CDK_HOME=str(home / ".cdk"), RUN_CDK_TESTS="1")
    cache = Path(env.pop("UV_CACHE_DIR"))
    python = Path(env.pop("UV_PYTHON_INSTALL_DIR"))
    # Only fixture-owned links to already installed packages/interpreters; no auth/config copies.
    for args, existing in ((("cache", "dir"), cache), (("python", "dir"), python)):
        default = uv_directory(*args, env=env)
        assert default.is_relative_to(home), "This regression needs HOME-relative uv defaults"
        default.parent.mkdir(parents=True, exist_ok=True)
        default.symlink_to(existing, target_is_directory=True)
    uv = shutil.which("uv", path=env["PATH"])
    assert uv is not None
    result = subprocess.run(  # noqa: S603 -- real documented command, selected non-recursive case
        [
            uv,
            "run",
            "--project",
            "app/agent",
            "--frozen",
            "pytest",
            "tests/test_cdk_context.py::test_persisted_context_survives_fresh_native_builds",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("value", [None, "missing"])
def test_default_no_kms_warns_and_cleanup_is_empty(project, value):
    sample, _, command = project
    path = sample / "agentcore/cdk/cdk.json"
    if value == "missing":
        config = json.loads(path.read_text())
        del config["context"][KEY]
        path.write_text(json.dumps(config))
    else:
        configure(sample, None)
    command("agentcore", "build", "--json")
    check_kms(built_stack(sample), None)
    # Pinned CDK stores construct annotations in the assembly's additional metadata file.
    metadata_files = list((sample / "agentcore/cdk/cdk.out").glob("*.metadata.json"))
    assert len(metadata_files) == 1
    metadata = metadata_files[0].read_text()
    assert "Asset encryption has NOT been detected" in metadata
    assert f"context.{KEY}" in metadata
    command("agentcore", "remove", "all", "--yes")
    # No orphan boundary even if the removed Runtime's setting is invalid.
    configure(sample, "invalid-after-removal")
    command("agentcore", "build", "--json")
    assert not {r["Type"] for r in built_stack(sample)[0].values()} - {"AWS::CDK::Metadata"}


@pytest.mark.parametrize("value", ["", " ", False, 123, [], {}, "not-an-arn", ARN.replace(":key/", ":alias/")])
def test_malformed_context_fails(project, value):
    sample, _, command = project
    configure(sample, value)
    output = command("agentcore", "build", "--json", success=False)
    assert f"context.{KEY}" in output
    assert "must be null or a KMS key ARN" in output


def test_saved_target_in_another_region_with_stale_template(project):
    sample, _, command = project
    command("agentcore", "build", "--json")
    target = {"name": "default", "region": "us-east-1", "account": "111122223333"}
    (sample / "agentcore/aws-targets.json").write_text(json.dumps([target]))
    key = ARN.replace("us-west-2", target["region"])
    configure(sample, key)
    command("agentcore", "build", "--json")
    output = sample / "agentcore/cdk/cdk.out"
    assert (output / "AgentCore-CustomerSupport.template.json").exists()
    stack = built_stack(sample)
    assert stack[1] == target["region"]
    check_kms(stack, key)
