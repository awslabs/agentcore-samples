"""The same rules, on real botocore clients with botocore's Stubber: every call must be one of the canned ones,
in order, with the expected parameters (checked against the real service model too), so any extra call (an
UpdateAgentRuntime on a box that matches, a CreateCapacityProvider, a StopRuntimeSession) fails the test. No
credentials are used: the Stubber answers before anything is signed or sent."""

import datetime
import json

import boto3
import pytest
from botocore.stub import Stubber

ACCOUNT = "111122223333"
UID = "00uADA0000000000000A"
RT_ID = "devbox_vm_ada-klmnopqrst"
RT_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/{RT_ID}"
AP_ARN = f"arn:aws:elasticfilesystem:us-east-1:{ACCOUNT}:access-point/fsap-0123456789abcdef0"
CP_ID = "devbox_ada-abcdefghij"
CP_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:capacity-provider/{CP_ID}"
NOW = datetime.datetime(2026, 9, 29, tzinfo=datetime.timezone.utc)
ROLE_ID = "AROA" + "EXAMPLE1234567890"  # a made-up role id, joined at run time


def client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="x")


EXEC_ARN = f"arn:aws:iam::{ACCOUNT}:role/devbox-exec-ada"
AP_ID = "fsap-0123456789abcdef0"
IMAGE = f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/devbox-box:0123456789abcdef0123"
GATEWAY_URL = "https://devbox-tools-abcde12345.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


@pytest.fixture
def box(devbox, settings):
    """advance_box on real botocore clients, with a Stubber on each: Ada's record has her folder and role."""
    cl = {"acc": client("bedrock-agentcore-control"), "efs": client("efs"), "iam": client("iam")}
    plan = devbox.BoxPlan(
        ACCOUNT,
        "fs-0123456789abcdef0",
        "subnet-0123456789abcdef0",
        "sg-0123456789abcdef0",
        IMAGE,
        GATEWAY_URL,
        "https://d-1234567890.awsapps.com/start",
        f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary",
    )
    rec = {
        "key": devbox.uid_key(UID),
        "uid": UID,
        "name": "ada",
        "tier": "Power",
        "generation": 1,
        "accessPointId": AP_ID,
        "accessPointArn": AP_ARN,
        "execRoleArn": EXEC_ARN,
    }
    return {"cl": cl, "plan": plan, "rec": rec, "stub": {k: Stubber(c) for k, c in cl.items()}}


def wanted(devbox, settings, plan):
    user = devbox.User("ada", "Power", "")
    env = devbox.runtime_env(
        settings, user, uid=UID, generation=1, account=ACCOUNT, start_url=plan.start_url, gateway_url=plan.gateway_url
    )
    return devbox.runtime_request(
        settings,
        user,
        uid=UID,
        image_uri=IMAGE,
        exec_role_arn=EXEC_ARN,
        access_point_arn=AP_ARN,
        subnet_id=plan.subnet_id,
        security_group_id=plan.security_group_id,
        env=env,
    )


def described(want, status="READY", **extra):
    """GetAgentRuntime's answer for a runtime made from `want`."""
    cur = {k: v for k, v in want.items() if k not in ("tags", "agentRuntimeName")}
    cur.update(
        agentRuntimeArn=RT_ARN,
        agentRuntimeName="devbox_vm_ada",
        agentRuntimeId=RT_ID,
        agentRuntimeVersion="4",
        createdAt=NOW,
        lastUpdatedAt=NOW,
        status=status,
        **extra,
    )
    return cur


def folder_and_role(stub):
    """Every step starts by looking at the folder and the role."""
    stub["efs"].add_response(
        "describe_access_points",
        {"AccessPoints": [{"AccessPointId": AP_ID, "AccessPointArn": AP_ARN, "LifeCycleState": "available"}]},
        {"AccessPointId": AP_ID},
    )
    stub["iam"].add_response(
        "get_role",
        {"Role": {"Path": "/", "RoleName": "devbox-exec-ada", "RoleId": ROLE_ID, "Arn": EXEC_ARN, "CreateDate": NOW}},
        {"RoleName": "devbox-exec-ada"},
    )


def stubbed(box):
    class All:
        def __enter__(self):
            for s in box["stub"].values():
                s.__enter__()

        def __exit__(self, *a):
            for s in box["stub"].values():
                s.__exit__(*a)
                s.assert_no_pending_responses()

    return All()


def test_a_box_that_matches_is_left_alone(devbox, settings, box):
    want = wanted(devbox, settings, box["plan"])
    box["rec"].update(runtimeId=RT_ID, runtimeArn=RT_ARN)
    folder_and_role(box["stub"])
    box["stub"]["acc"].add_response(
        "get_agent_runtime", described(want, metadataConfiguration={"requireMMDSV2": True}), {"agentRuntimeId": RT_ID}
    )
    box["stub"]["acc"].add_response(
        "get_resource_policy", {"policy": json.dumps(devbox.runtime_resource_policy(RT_ARN))}, {"resourceArn": RT_ARN}
    )
    with stubbed(box):
        res = devbox.advance_box(box["cl"], settings, box["plan"], box["rec"])
    assert res["state"] == "ready" and box["rec"]["sessionId"] == devbox.session_id(UID, 1)


def test_a_new_microvm_runtime_is_made_once_then_locked_down(devbox, settings, box):
    """Create (in the VPC, with the EFS folder), wait, the resource policy, then MMDSv2: exactly these calls, these
    parameters, over the calls a page makes while it waits."""
    want = wanted(devbox, settings, box["plan"])
    acc, steps = box["stub"]["acc"], []
    folder_and_role(box["stub"])
    acc.add_response("list_agent_runtimes", {"agentRuntimes": []}, {})
    acc.add_response(
        "create_agent_runtime",
        {
            "agentRuntimeArn": RT_ARN,
            "agentRuntimeId": RT_ID,
            "agentRuntimeVersion": "1",
            "createdAt": NOW,
            "status": "CREATING",
        },
        want,
    )
    folder_and_role(box["stub"])
    acc.add_response("get_agent_runtime", described(want, "CREATING"), {"agentRuntimeId": RT_ID})
    folder_and_role(box["stub"])
    acc.add_response("get_agent_runtime", described(want), {"agentRuntimeId": RT_ID})
    acc.add_client_error("get_resource_policy", "ResourceNotFoundException", expected_params={"resourceArn": RT_ARN})
    acc.add_response(
        "put_resource_policy",
        {"policy": "{}"},
        {"resourceArn": RT_ARN, "policy": json.dumps(devbox.runtime_resource_policy(RT_ARN))},
    )
    acc.add_response(
        "update_agent_runtime",
        {
            "agentRuntimeArn": RT_ARN,
            "agentRuntimeId": RT_ID,
            "agentRuntimeVersion": "2",
            "createdAt": NOW,
            "lastUpdatedAt": NOW,
            "status": "UPDATING",
        },
        devbox.runtime_update_request(RT_ID, want, require_mmdsv2=True),
    )
    folder_and_role(box["stub"])
    acc.add_response(
        "get_agent_runtime", described(want, metadataConfiguration={"requireMMDSV2": True}), {"agentRuntimeId": RT_ID}
    )
    acc.add_response(
        "get_resource_policy", {"policy": json.dumps(devbox.runtime_resource_policy(RT_ARN))}, {"resourceArn": RT_ARN}
    )
    with stubbed(box):
        for _ in range(4):
            steps.append(devbox.advance_box(box["cl"], settings, box["plan"], box["rec"])["state"])
    assert steps == ["working", "working", "working", "ready"]
    assert "capacityProviderConfiguration" not in want and want["networkConfiguration"]["networkMode"] == "VPC"


def test_an_instances_runtime_under_the_microvm_name_is_never_updated(devbox, settings, box):
    box["rec"].update(runtimeId=RT_ID, runtimeArn=RT_ARN)
    folder_and_role(box["stub"])
    cur = described(
        wanted(devbox, settings, box["plan"]), capacityProviderConfiguration={"capacityProviderArn": CP_ARN}
    )
    box["stub"]["acc"].add_response("get_agent_runtime", cur, {"agentRuntimeId": RT_ID})
    with stubbed(box):
        res = devbox.advance_box(box["cl"], settings, box["plan"], box["rec"])
    assert res["state"] == "failed" and "Instances runtime" in res["message"]


def test_retire_deletes_each_session_then_the_capacity_provider(devbox, settings, monkeypatch):
    """DeleteCapacityProviderSession (it deletes the session's instance and EBS volume) for every generation, re-issued until
    Deleted, then DeleteCapacityProvider until it's gone: on the real data-plane and control-plane models."""
    monkeypatch.setattr(devbox, "SLEEP", lambda s: None)
    acc, acd = client("bedrock-agentcore-control"), client("bedrock-agentcore")
    ctx = devbox.Ctx(
        s=settings,
        aws=devbox.Aws(settings, clients={"bedrock-agentcore-control": acc, "bedrock-agentcore": acd}),
        state={"instances": {"ada": {"uid": UID, "generation": 2, "cpId": CP_ID}}},
        account=ACCOUNT,
    )
    s_acc, s_acd = Stubber(acc), Stubber(acd)
    for g in (1, 2):
        sid = devbox.session_id(UID, g)
        for status in ("Deleting", "Deleted"):
            s_acd.add_response(
                "delete_capacity_provider_session",
                {"capacityProviderArn": CP_ARN, "sessionId": sid, "status": status},
                {"capacityProviderId": CP_ID, "sessionId": sid},
            )
    s_acc.add_response(
        "delete_capacity_provider", {"capacityProviderId": CP_ID, "status": "DELETING"}, {"capacityProviderId": CP_ID}
    )
    s_acc.add_client_error(
        "get_capacity_provider", "ResourceNotFoundException", expected_params={"capacityProviderId": CP_ID}
    )
    with s_acc, s_acd:
        devbox.retire_capacity_providers(ctx, [{"name": "devbox_ada", "capacityProviderId": CP_ID}])
    s_acc.assert_no_pending_responses()
    s_acd.assert_no_pending_responses()
