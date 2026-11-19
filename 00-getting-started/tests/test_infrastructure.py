"""Inspect the built artifact and synthesized IAM contract; never call AWS.

Run `agentcore build` before these tests. They skip explicitly in a source-only checkout.
"""

import json
import struct
from pathlib import Path
from zipfile import ZipFile

import main
import pytest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "agentcore/cdk/cdk.out"


def resolve(value):
    """Resolve just the pseudo-parameters/intrinsics used by this sample's policy."""
    parameters = {
        "AWS::Partition": "aws",
        "AWS::Region": "us-west-2",
        "AWS::AccountId": "111122223333",
        "AWS::URLSuffix": "amazonaws.com",
    }
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return parameters[value["Ref"]]
    if "Fn::Join" in value:
        separator, parts = value["Fn::Join"]
        return separator.join(resolve(part) for part in parts)
    if "Fn::Sub" in value:
        text = value["Fn::Sub"]
        for key, replacement in parameters.items():
            text = text.replace("${" + key + "}", replacement)
        return text
    raise AssertionError(f"Unexpected policy intrinsic: {value}")


@pytest.fixture
def resources():
    templates = list(OUTPUT.glob("*.template.json"))
    if not templates:
        pytest.skip("Run agentcore build to enable synthesized-infrastructure checks")
    assert len(templates) == 1, "Use a clean synthesis directory for this single-target sample"
    return json.loads(templates[0].read_text())["Resources"]


def test_runtime_configuration(resources):
    runtimes = [r for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime"]
    assert len(runtimes) == 1
    props = runtimes[0]["Properties"]
    assert props["AgentRuntimeArtifact"]["CodeConfiguration"]["Runtime"] == "PYTHON_3_13"
    assert props["ProtocolConfiguration"] == "HTTP"
    assert "AuthorizerConfiguration" not in props  # Service default is AWS_IAM, not anonymous.
    assert props["EnvironmentVariables"]["OTEL_SDK_DISABLED"] == "true"
    assert props["LifecycleConfiguration"] == {"IdleRuntimeSessionTimeout": 300, "MaxLifetime": 900}
    assert {r["Type"] for r in resources.values()} <= {
        "AWS::BedrockAgentCore::Runtime",
        "AWS::IAM::Role",
        "AWS::IAM::Policy",
        "AWS::IAM::ManagedPolicy",
        "AWS::CDK::Metadata",
    }


def test_boundary_is_attached_and_scopes_model_asset_and_logs(resources):
    roles = [r["Properties"] for r in resources.values() if r["Type"] == "AWS::IAM::Role"]
    assert len(roles) == 1
    boundary_id = roles[0]["PermissionsBoundary"]["Ref"]
    boundary = resources[boundary_id]
    assert boundary["Type"] == "AWS::IAM::ManagedPolicy"
    statements = boundary["Properties"]["PolicyDocument"]["Statement"]
    assert all(s["Effect"] == "Allow" for s in statements)
    assert len(statements) in {3, 4}  # Fourth statement is optional asset KMS decryption.
    by_service = {}
    for statement in statements:
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else actions
        assert all("*" not in action for action in actions)
        by_service[actions[0].split(":")[0]] = (set(actions), resolve(statement["Resource"]))
    assert by_service["bedrock"] == (
        {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"},
        f"arn:aws:bedrock:us-west-2::foundation-model/{main.MODEL_ID}",
    )
    runtime = next(
        r["Properties"] for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime"
    )
    source = runtime["AgentRuntimeArtifact"]["CodeConfiguration"]["Code"]["S3"]
    assert by_service["s3"] == (
        {"s3:GetObject", "s3:GetObjectVersion"},
        f"arn:aws:s3:::{resolve(source['Bucket'])}/{source['Prefix']}",
    )
    assert by_service["logs"][0] == {
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:DescribeLogStreams",
        "logs:PutLogEvents",
        "logs:PutResourcePolicy",
    }
    assert by_service["logs"][1].endswith(
        f"/aws/bedrock-agentcore/runtimes/{runtime['AgentRuntimeName']}*"
    )
    assert set(by_service) - {"kms"} == {"bedrock", "s3", "logs"}
    if "kms" in by_service:
        assert by_service["kms"][0] == {"kms:Decrypt"}
        assert ":kms:" in by_service["kms"][1] and ":key/" in by_service["kms"][1]
        decrypt = next(s for s in statements if "kms:Decrypt" in s["Action"])
        conditions = decrypt["Condition"]["StringEquals"]
        assert resolve(conditions["kms:ViaService"]) == "s3.us-west-2.amazonaws.com"
        contexts = [resolve(v) for v in conditions["kms:EncryptionContext:aws:s3:arn"]]
        assert contexts == [by_service["s3"][1], f"arn:aws:s3:::{resolve(source['Bucket'])}"]


def test_role_trust_is_service_and_account_scoped(resources):
    role = next(r["Properties"] for r in resources.values() if r["Type"] == "AWS::IAM::Role")
    statement = role["AssumeRolePolicyDocument"]["Statement"][0]
    assert statement["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    account = statement["Condition"]["StringEquals"]["aws:SourceAccount"]
    source_arn = resolve(statement["Condition"]["ArnLike"]["aws:SourceArn"])
    if isinstance(account, str):
        assert account.isdigit() and len(account) == 12
        assert f":{account}:runtime/" in source_arn
    else:
        assert account == {"Ref": "AWS::AccountId"}
        assert ":111122223333:runtime/" in source_arn


def test_zip_contains_current_code_and_linux_arm64_dependencies(resources):
    runtime = next(
        r["Properties"] for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime"
    )
    key = runtime["AgentRuntimeArtifact"]["CodeConfiguration"]["Code"]["S3"]["Prefix"]
    with ZipFile(OUTPUT / ("asset." + key)) as archive:
        names = archive.namelist()
        assert archive.read("main.py") == (ROOT / "app/agent/main.py").read_bytes()
        assert not any(".venv" in Path(name).parts or ".git" in Path(name).parts for name in names)
        assert not any(Path(name).name.startswith(".env") for name in names)
        assert not any(name.startswith(("tests/", "pytest/", "_pytest/")) for name in names)
        native = [name for name in names if name.endswith(".so")]
        assert native, "Expected compiled dependencies such as pydantic-core"
        for name in native:
            header = archive.read(name)[:20]
            assert header[:4] == b"\x7fELF", name
            assert header[5] == 1, name  # little endian
            assert struct.unpack_from("<H", header, 18)[0] == 183, name  # AArch64
