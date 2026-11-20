"""Offline lifecycle checks. Stubbed IAM requests must match exactly and in order."""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import Mock

import boto3
import cleanup
import deploy
import deployment_state as receipt
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

ACCOUNT = "111122223333"
NAME = "langgraph_bedrock_example"
ROLE = f"agentcore-{NAME}-role"
POLICY = f"{NAME}-policy"
ROLE_ID = "AROAXXXXXXXXXXXXXXXX1"
ROLE_DATA = {
    "RoleName": ROLE,
    "RoleId": ROLE_ID,
    "Arn": f"arn:aws:iam::{ACCOUNT}:role/{ROLE}",
    "Path": "/",
    "CreateDate": datetime(2026, 1, 1, tzinfo=UTC),
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "CONFIG", tmp_path / "runtime_config.json")
    monkeypatch.setattr(deploy, "AGENT_NAME", NAME)
    st = {
        "agent_name": NAME,
        "account_id": ACCOUNT,
        "region": "us-east-1",
        "bucket": "example",
        "key": "example/code.zip",
    }
    receipt.reserve(st)
    return st


@pytest.fixture
def aws(monkeypatch):
    session = boto3.Session(region_name="us-east-1")
    iam = session.client("iam", config=Config(signature_version=UNSIGNED))
    sts, s3, control = Mock(), Mock(), Mock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    clients = {"sts": sts, "s3": s3, "bedrock-agentcore-control": control, "iam": iam}
    monkeypatch.setattr(boto3, "client", lambda service, **_: clients[service])
    monkeypatch.setattr(boto3, "Session", lambda **_: Mock(client=clients.__getitem__))
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    with Stubber(iam) as stub:
        yield stub, clients
        stub.assert_no_pending_responses()


def created(stub):
    stub.add_response(
        "create_role",
        {"Role": ROLE_DATA},
        {"RoleName": ROLE, "AssumeRolePolicyDocument": ANY, "Description": f"Execution role for {NAME}"},
    )


def verify(stub, role_id=ROLE_ID):
    stub.add_response("get_role", {"Role": dict(ROLE_DATA, RoleId=role_id)}, {"RoleName": ROLE})


def deletion(stub, policies):
    verify(stub)
    stub.add_response("list_role_policies", {"PolicyNames": policies}, {"RoleName": ROLE})
    if POLICY in policies:
        stub.add_response("delete_role_policy", {}, {"RoleName": ROLE, "PolicyName": POLICY})
    verify(stub)
    stub.add_response("delete_role", {}, {"RoleName": ROLE})


def test_colliding_role_never_adopted(state, aws):
    stub, _ = aws
    stub.add_client_error("create_role", "EntityAlreadyExists")
    with pytest.raises(ClientError, match="EntityAlreadyExists"):
        deploy.create_execution_role(state)
    assert "role_id" not in json.loads(receipt.CONFIG.read_text())
    with pytest.raises(SystemExit, match="unconfirmed create"):
        cleanup.main()


def test_policy_failure_leaves_cleanable_role(state, aws):
    stub, _ = aws
    created(stub)
    stub.add_client_error("put_role_policy", "AccessDenied")
    with pytest.raises(ClientError, match="AccessDenied"):
        deploy.create_execution_role(state)
    verify(stub)
    deletion(stub, [])
    cleanup.main()
    assert not receipt.CONFIG.exists()


def test_replaced_role_blocks_all_mutations(state, aws):
    stub, clients = aws
    state.update(role_id=ROLE_ID, runtime_id="example-runtime")
    receipt.save(state)
    verify(stub, "AROAXXXXXXXXXXXXXXXX2")
    with pytest.raises(RuntimeError, match="not the role"):
        cleanup.main()
    assert not clients["bedrock-agentcore-control"].mock_calls
    assert receipt.CONFIG.exists()


def test_extra_policy_is_not_stripped(state, aws):
    stub, _ = aws
    state.update(role_id=ROLE_ID)
    receipt.save(state)
    verify(stub)
    verify(stub)
    stub.add_response("list_role_policies", {"PolicyNames": [POLICY, "external"]}, {"RoleName": ROLE})
    with pytest.raises(RuntimeError, match="additional inline policies"):
        cleanup.main()
    assert receipt.CONFIG.exists()


def test_existing_state_and_overlapping_operations_refused(state):
    with pytest.raises(SystemExit, match="already exists"):
        receipt.reserve({})
    assert json.loads(receipt.CONFIG.read_text()) == state
    with receipt.exclusive_operation(), pytest.raises(SystemExit, match="in progress"):
        cleanup.main()


def test_old_state_and_wrong_account_fail_closed(state, aws):
    _, clients = aws
    with pytest.raises(SystemExit, match="ownership"):
        cleanup.main()
    state.update(role_id=ROLE_ID)
    receipt.save(state)
    clients["sts"].get_caller_identity.return_value = {"Account": "222233334444"}
    with pytest.raises(SystemExit, match="Wrong AWS account"):
        cleanup.main()
    assert receipt.CONFIG.exists()


def test_upload_requires_a_new_object_and_records_version(state, aws, tmp_path, monkeypatch):
    _, clients = aws
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.py").write_text("# fixture only")
    run = deploy.subprocess.run

    def package(args, **kwargs):
        if args[0] != "uv":
            return run(args, **kwargs)
        # Only dependency acquisition is replaced; build a real zip from fixture files.
        directory = tmp_path / "deployment_package"
        directory.mkdir()
        (directory / "fixture.py").touch()

    monkeypatch.setattr(deploy.subprocess, "run", package)
    s3 = clients["s3"]
    s3.put_object.return_value = {"ETag": '"test"', "VersionId": "test-version"}
    deploy.build_and_upload_package(state)
    s3.head_bucket.assert_called_once_with(Bucket="example", ExpectedBucketOwner=ACCOUNT)
    s3.create_bucket.assert_not_called()
    s3.put_object.assert_called_once_with(
        Bucket="example", Key="example/code.zip", Body=ANY, IfNoneMatch="*", ExpectedBucketOwner=ACCOUNT
    )
    saved = json.loads(receipt.CONFIG.read_text())
    assert saved["etag"] == '"test"' and saved["version_id"] == "test-version"
    assert not (tmp_path / "deployment_package.zip").exists()


def test_failed_runtime_deletion_keeps_its_role_and_zip(state, aws):
    stub, clients = aws
    verify(stub)
    control = clients["bedrock-agentcore-control"]
    control.exceptions.ResourceNotFoundException = type("NotFound", (Exception,), {})
    control.get_agent_runtime.side_effect = [{"status": "READY"}, {"status": "DELETE_FAILED"}]
    state.update(role_id=ROLE_ID, runtime_id="example-runtime", runtime_arn="example-arn", etag='"test"')
    receipt.save(state)
    with pytest.raises(RuntimeError, match="DELETE_FAILED"):
        cleanup.main()
    assert state == json.loads(receipt.CONFIG.read_text())
    clients["s3"].delete_object.assert_not_called()


def test_happy_path_and_retry_after_s3_failure(state, aws):
    stub, clients = aws
    created(stub)
    stub.add_response("put_role_policy", {}, {"RoleName": ROLE, "PolicyName": POLICY, "PolicyDocument": ANY})
    assert deploy.create_execution_role(state) == ROLE_DATA["Arn"]
    assert json.loads(receipt.CONFIG.read_text())["role_id"] == ROLE_ID
    control = clients["bedrock-agentcore-control"]
    missing = type("NotFound", (Exception,), {})
    control.exceptions.ResourceNotFoundException = missing
    runtime = {"agentRuntimeId": "example-runtime", "agentRuntimeArn": "example-arn"}
    control.create_agent_runtime.return_value = runtime
    control.get_agent_runtime.return_value = {"status": "READY", "platformVersion": "V2"}
    deploy.create_runtime(ROLE_DATA["Arn"], state)
    timestamp = ROLE_DATA["CreateDate"]
    control.create_agent_runtime_endpoint.return_value = {
        "agentRuntimeEndpointArn": "example-ep",
        "createdAt": timestamp,
    }
    control.list_agent_runtime_endpoints.return_value = {"runtimeEndpoints": [{"name": "default", "status": "READY"}]}
    deploy.create_endpoint(runtime["agentRuntimeId"], state)
    state.update(etag='"test"', version_id="test-version")
    receipt.save(state)
    control.get_agent_runtime_endpoint.side_effect = [{"status": "READY", "createdAt": timestamp}, missing()]
    control.get_agent_runtime.side_effect = [{"status": "READY"}, missing()]
    s3 = clients["s3"]
    s3.delete_object.side_effect = [RuntimeError("fixture: S3 unavailable"), {}]
    verify(stub)
    with pytest.raises(RuntimeError, match="S3 unavailable"):
        cleanup.main()
    remaining = json.loads(receipt.CONFIG.read_text())
    assert "runtime_id" not in remaining and "etag" in remaining
    verify(stub)
    deletion(stub, [POLICY])
    cleanup.main()
    control.delete_agent_runtime_endpoint.assert_called_once_with(
        agentRuntimeId="example-runtime", endpointName="default"
    )
    control.delete_agent_runtime.assert_called_once_with(agentRuntimeId="example-runtime")
    s3.delete_object.assert_called_with(
        Bucket="example",
        Key="example/code.zip",
        ExpectedBucketOwner=ACCOUNT,
        IfMatch='"test"',
        VersionId="test-version",
    )
    assert not receipt.CONFIG.exists()
