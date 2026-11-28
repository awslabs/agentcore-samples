"""Offline deployment contracts; no live AWS, model, TURN or microphone."""

import io
import json
import uuid
from datetime import UTC, datetime, timezone
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber
from deploy import runtime_v2 as deploy
from fastapi.testclient import TestClient
from server.server import application

REGION, ACCOUNT, NAME = "us-west-2", "111111111111", "webrtc"
RID = "webrtc-0123456789"
ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/{RID}"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/voice"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/voice@sha256:" + "a" * 64
TOKEN = str(uuid.uuid4())


def plan(image=IMAGE, role=ROLE):
    return deploy.plan(REGION, ACCOUNT, NAME, image, role, "voice", ["subnet-01234567"], ["sg-01234567"], TOKEN)


@pytest.mark.parametrize(
    "image,role",
    [
        (IMAGE.replace(ACCOUNT, "222222222222"), ROLE),
        (IMAGE.split("@")[0] + ":latest", ROLE),
        (IMAGE, ROLE.replace(ACCOUNT, "222222222222")),
    ],
)
def test_imported_resources(image, role):
    with pytest.raises(ValueError):
        plan(image, role)


def test_create_and_cleanup(tmp_path):
    client = boto3.client(
        "bedrock-agentcore-control", region_name=REGION, aws_access_key_id="fixture", aws_secret_access_key="fixture"
    )
    stamp = datetime.now(UTC)
    result = {
        "agentRuntimeArn": ARN,
        "agentRuntimeId": RID,
        "agentRuntimeVersion": "1",
        "createdAt": stamp,
        "status": "CREATING",
    }
    ready = dict(
        result,
        status="READY",
        agentRuntimeName=NAME,
        lastUpdatedAt=stamp,
        roleArn=ROLE,
        lifecycleConfiguration={},
        platformVersion="V2",
    )
    req = plan()
    assert req["networkConfiguration"] == {
        "networkMode": "VPC",
        "networkModeConfig": {"subnets": ["subnet-01234567"], "securityGroups": ["sg-01234567"]},
    }
    assert req["protocolConfiguration"] == {"serverProtocol": "HTTP"} and req["platformVersion"] == "V2"
    assert req["environmentVariables"]["KVS_CHANNEL_NAME"] == "voice"
    receipt = tmp_path / "receipt.json"
    with Stubber(client) as stub:
        stub.add_response("create_agent_runtime", result, req)
        stub.add_response("get_agent_runtime", ready, {"agentRuntimeId": RID})
        deploy.create(client, REGION, ACCOUNT, req, receipt)
        with pytest.raises(FileExistsError):
            deploy.create(client, REGION, ACCOUNT, req, receipt)
        with pytest.raises(ValueError, match="mismatch"):
            deploy.cleanup(client, REGION, "222222222222", receipt)
        stub.add_response("get_agent_runtime", ready, {"agentRuntimeId": RID})
        stub.add_response("list_tags_for_resource", {"tags": {}}, {"resourceArn": ARN})
        with pytest.raises(ValueError, match="ownership"):
            deploy.cleanup(client, REGION, ACCOUNT, receipt)
        assert receipt.exists()
        stub.add_response("get_agent_runtime", ready, {"agentRuntimeId": RID})
        stub.add_response("list_tags_for_resource", {"tags": {deploy.TAG: TOKEN}}, {"resourceArn": ARN})
        stub.add_response("delete_agent_runtime", {"status": "DELETING"}, {"agentRuntimeId": RID})
        stub.add_client_error("get_agent_runtime", "ResourceNotFoundException", expected_params={"agentRuntimeId": RID})
        deploy.cleanup(client, REGION, ACCOUNT, receipt)
        stub.assert_no_pending_responses()
    assert not receipt.exists()


def test_websocket_plans_import_only_image_and_role():
    for sample in deploy.SAMPLES:
        req = deploy.plan(REGION, ACCOUNT, "bidi_" + sample.replace("-", "_"), IMAGE, ROLE, None, None, None, TOKEN)
        assert req["networkConfiguration"] == {"networkMode": "PUBLIC"}
        assert req["environmentVariables"] == {"AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION}
        assert req["protocolConfiguration"] == {"serverProtocol": "HTTP"} and req["platformVersion"] == "V2"


def test_ambiguous_create_does_not_cleanup_by_name(tmp_path):
    client = Mock(spec=["create_agent_runtime"])
    client.create_agent_runtime.side_effect = TimeoutError
    receipt = tmp_path / "receipt.json"
    with pytest.raises(TimeoutError):
        deploy.create(client, REGION, ACCOUNT, plan(), receipt)
    with pytest.raises(ValueError, match="Incomplete"):
        deploy.cleanup(client, REGION, ACCOUNT, receipt)
    assert json.loads(receipt.read_text())["token"] == TOKEN


def test_browser_signaling():
    aws = Mock(spec=["invoke_agent_runtime"])
    stream = io.BytesIO(b'{"iceServers":[]}')
    aws.invoke_agent_runtime.return_value = {"response": stream}
    origin = "http://localhost:7860"
    ui = TestClient(application(ARN, aws), base_url=origin)
    assert ui.get("/mode").json() == {"hosted": True}
    assert "cdn.jsdelivr" not in ui.get("/").text
    message = {"session": TOKEN, "action": "ice_config", "data": {}}
    assert ui.post("/signal", json=message).status_code == 403
    assert ui.post("/signal", json=message, headers={"origin": "https://example.org"}).status_code == 403
    assert ui.post("/signal", json=message, headers={"host": "example.org", "origin": origin}).status_code == 400
    aws.invoke_agent_runtime.assert_not_called()
    assert ui.post("/signal", json=message, headers={"origin": origin}).json() == {"iceServers": []}
    call = aws.invoke_agent_runtime.call_args.kwargs
    assert call["agentRuntimeArn"] == ARN and call["runtimeSessionId"] == TOKEN
    assert json.loads(call["payload"]) == {"action": "ice_config", "data": {}}
    assert stream.closed
