"""Every request devbox.py sends is built by a pure function. Each one is checked here against the
botocore service model (no credentials) and against the design's invariants: a microVM box per person in
the VPC, their own EFS folder, an execution role that can mount only that folder and has no Bedrock,
and a resource policy that allows the owner's workbench and terminal and denies the rest."""

import hashlib
import json
import re

import pytest
from fake_aws import model, validate

ACCOUNT = "111122223333"
UID = "00uADA0000000000000A"
GRACE_UID = "00uGRACE00000000000G"
RUNTIME_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_vm_ada-abcdefghij"
GATEWAY_ID = "devbox-tools-abcde12345"
GATEWAY_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:gateway/{GATEWAY_ID}"
GATEWAY_URL = f"https://{GATEWAY_ID}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
IMAGE = f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/devbox-box:0123456789abcdef0123"
EXEC_ARN = f"arn:aws:iam::{ACCOUNT}:role/devbox-exec-ada"
GRACE_EXEC_ARN = f"arn:aws:iam::{ACCOUNT}:role/devbox-exec-grace"
FS_ID = "fs-0123456789abcdef0"
FS_ARN = f"arn:aws:elasticfilesystem:us-east-1:{ACCOUNT}:file-system/{FS_ID}"
AP_ARN = f"arn:aws:elasticfilesystem:us-east-1:{ACCOUNT}:access-point/fsap-0123456789abcdef0"
GRACE_AP_ARN = f"arn:aws:elasticfilesystem:us-east-1:{ACCOUNT}:access-point/fsap-0fedcba9876543210"
SUBNET, BOX_SG, EFS_SG = "subnet-0123456789abcdef0", "sg-0123456789abcdef0", "sg-0fedcba9876543210"
DENIED = [
    "bedrock-agentcore:InvokeAgentRuntimeCommand",
    "bedrock-agentcore:StopRuntimeSession",
    "bedrock-agentcore:InvokeAgentRuntimeForUser",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser",
]
ALLOWED = [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream",
    "bedrock-agentcore:InvokeAgentRuntimeCommandShell",
]


TIERS = {"ada": ("Power", "ada.lovelace@example.com"), "grace": ("Standard", "grace.hopper@example.com")}


def person(devbox, name):
    """A box owner as the provisioner sees one."""
    tier, login = TIERS[name]
    return devbox.User(name, tier, login)


def actions(doc):
    out = []
    for st in doc["Statement"]:
        a = st["Action"]
        out += [a] if isinstance(a, str) else a
    return out


@pytest.fixture
def ada(devbox):
    return person(devbox, "ada")


@pytest.fixture
def runtime_req(devbox, settings, ada):
    env = devbox.runtime_env(
        settings,
        ada,
        uid=UID,
        generation=1,
        account=ACCOUNT,
        start_url="https://d-1234567890.awsapps.com/start",
        gateway_url=GATEWAY_URL,
    )
    return devbox.runtime_request(
        settings,
        ada,
        uid=UID,
        image_uri=IMAGE,
        exec_role_arn=EXEC_ARN,
        access_point_arn=AP_ARN,
        subnet_id=SUBNET,
        security_group_id=BOX_SG,
        env=env,
    )


def exec_spec(devbox, settings, name="ada", ap_arn=AP_ARN):
    return devbox.exec_role_spec(
        ACCOUNT, "us-east-1", person(devbox, name), file_system_arn=FS_ARN, access_point_arn=ap_arn
    )


# ----------------------------------------------------------------------------- settings
def test_env_file_parsing(devbox):
    e = devbox.parse_env_file("A=1   # note\nB=\"x y # kept\"\n# comment\nC=\nD='q'\nE=    # blank = the default\n")
    assert e == {"A": "1", "B": "x y # kept", "C": "", "D": "q", "E": ""}


def test_the_shipped_devbox_env_example(devbox):
    """devbox.env is git-ignored; the example ships, with only the Okta domain and the client id to fill in."""
    e = devbox.parse_env_file((devbox.HERE / "devbox.env.example").read_text())
    assert e["OKTA_DOMAIN"] == "" and e["DEVBOX_OKTA_CLIENT_ID"] == ""
    assert devbox.load_settings(e).errors == ["OKTA_DOMAIN is empty"], (
        "the one thing a fresh clone must set before check"
    )
    e["OKTA_DOMAIN"] = "example.okta.com"
    assert e["IDC_START_URL"] == ""
    assert "DEVBOX_USERS" not in e and not [k for k in e if k.startswith("DEVBOX_UID_")], "nobody is named"
    assert e["DEVBOX_TIER_GROUPS"] == "Power=ai-claude-power Standard=ai-claude-standard"
    assert e["ORG_ADMIN_PROFILE"] == "org-admin" and e["AI_ADMIN_PROFILE"] == "ai-admin"  # the example file's names
    assert e["DEVBOX_COMPUTE"] == "microvm"
    assert not devbox.load_settings(e).errors


def test_shipped_settings_are_valid(settings):
    assert settings.tier_groups == {"Power": "ai-claude-power", "Standard": "ai-claude-standard"}
    assert settings.okta_group == "devbox-users" and not settings.warnings
    assert settings.region == "us-east-1" and settings.az == "us-east-1a"
    assert (settings.compute, settings.idle_seconds, settings.vm_idle_seconds) == ("microvm", 3600, 3600)
    assert settings.okta_issuer == "https://example.okta.com/oauth2/default"


def test_settings_errors(devbox, env):
    env.update(
        DEVBOX_TIER_GROUPS="Gold=x Power=ai-claude-power Standard=ai-claude-power",
        REGION="eu-west-1",
        DEVBOX_IDLE_SECONDS="10",
        EDGE_INVOKE_MODE="RESPONSE_STREAM",
    )
    errs = " | ".join(devbox.load_settings(env).errors)
    for bit in (
        "'Gold' isn't a tier",
        "names Standard or ai-claude-power twice",
        "REGION=eu-west-1",
        "DEVBOX_IDLE_SECONDS=10",
        "EDGE_INVOKE_MODE=RESPONSE_STREAM",
    ):
        assert bit in errs, bit
    assert "can't also be a tier group" in " ".join(
        devbox.load_settings({**env, "DEVBOX_TIER_GROUPS": "Power=devbox-users"}).errors
    )


def test_a_people_list_left_in_devbox_env_is_only_a_warning(devbox, env):
    """DEVBOX_USERS and DEVBOX_UID_* are gone; an old devbox.env still loads, and says to remove them."""
    s = devbox.load_settings(
        {**env, "DEVBOX_USERS": "ada:Power:ada@example.com", "DEVBOX_UID_ADA": "00uADA0000000000000A"}
    )
    assert not s.errors and any("aren't used any more" in w for w in s.warnings)
    assert devbox.load_settings({k: v for k, v in env.items() if k != "DEVBOX_TIER_GROUPS"}).tier_groups == {
        "Power": "ai-claude-power",
        "Standard": "ai-claude-standard",
    }, "the default tier groups"


def test_microvm_is_the_only_compute_type(devbox, env):
    """On capacity providers AgentCore refuses the /ws upgrade and the terminal, so deploy makes microVMs only."""
    assert devbox.load_settings({k: v for k, v in env.items() if k != "DEVBOX_COMPUTE"}).compute == "microvm", (
        "the default"
    )
    assert not devbox.load_settings({**env, "DEVBOX_COMPUTE": "MicroVM"}).errors
    errs = " | ".join(devbox.load_settings({**env, "DEVBOX_COMPUTE": "instances"}).errors)
    assert "no longer makes Instances boxes" in errs and "retire-instances" in errs
    assert any(
        "DEVBOX_COMPUTE=lambda must be microvm" in e
        for e in devbox.load_settings({**env, "DEVBOX_COMPUTE": "lambda"}).errors
    )


def test_idle_is_capped_at_the_microvm_lifetime(devbox, env):
    s = devbox.load_settings({**env, "DEVBOX_IDLE_SECONDS": "86400"})
    assert not s.errors and s.vm_idle_seconds == 28800
    assert devbox.vm_lifecycle(s) == {"idleRuntimeSessionTimeout": 28800, "maxLifetime": 28800}
    assert (
        devbox.vm_lifecycle(devbox.load_settings({**env, "DEVBOX_IDLE_SECONDS": "60"}))["idleRuntimeSessionTimeout"]
        == 60
    )


def test_buffered_is_the_only_edge_invoke_mode(devbox, env):
    """edge/src/handler.mjs isn't wrapped in streamifyResponse, so the function URL must stay BUFFERED."""
    assert "EDGE_INVOKE_MODE" not in env
    assert not devbox.load_settings({**env, "EDGE_INVOKE_MODE": "BUFFERED"}).errors
    assert devbox.load_settings({**env, "EDGE_INVOKE_MODE": "RESPONSE_STREAM"}).errors


def test_the_longest_box_name_fits_every_name_it_makes(devbox, env):
    name = "a" * 38
    u = devbox.User(name, "Power", "a@example.com")
    runtime_name = (
        model("bedrock-agentcore-control").operation_model("CreateAgentRuntime").input_shape.members["agentRuntimeName"]
    )
    assert u.runtime_name == f"devbox_vm_{name}" and re.fullmatch(runtime_name.metadata["pattern"], u.runtime_name)
    assert len(u.exec_role) <= 64 and u.efs_root == f"/devbox/{name}"
    validate("efs", "CreateAccessPoint", devbox.access_point_request(FS_ID, u, "t"))


def test_the_new_runtime_name_never_looks_like_an_old_instances_box(devbox, ada):
    """retire-instances deletes what LEGACY_NAME matches, so it must never match a microVM runtime."""
    assert devbox.LEGACY_NAME.match(ada.legacy_name) and ada.legacy_name == "devbox_ada"
    assert not devbox.LEGACY_NAME.match(ada.runtime_name)
    assert not devbox.LEGACY_NAME.match("devbox_vm_" + "a" * 38)


# ----------------------------------------------------------------------------- identity
def test_session_id_format(devbox):
    sid = devbox.session_id(UID, 1)
    assert re.fullmatch(r"dbx-[0-9a-f]{64}", sid) and len(sid) == 68
    assert sid != devbox.session_id(UID, 2)
    # fits InvokeAgentRuntime (33–256), and DeleteCapacityProviderSession (retire-instances) in the service models
    shape = model("bedrock-agentcore").operation_model("DeleteCapacityProviderSession").input_shape.members["sessionId"]
    assert shape.metadata["min"] <= len(sid) <= shape.metadata["max"]
    assert re.fullmatch(shape.metadata["pattern"], sid)
    inv = model("bedrock-agentcore").operation_model("InvokeAgentRuntime").input_shape.members["runtimeSessionId"]
    assert inv.metadata["min"] <= len(sid) <= inv.metadata["max"]
    # "dbx-" + hex(sha256(uid + ":" + generation))
    assert sid == "dbx-" + hashlib.sha256(f"{UID}:1".encode()).hexdigest()


def test_recover_generation(devbox):
    assert devbox.recover_generation(UID, devbox.session_id(UID, 7)) == 7
    assert devbox.recover_generation(UID, "dbx-nope") is None


def test_okta_uid_from_scim_external_ids(devbox):
    assert devbox.okta_uid_from_external_ids([{"Issuer": "x", "Id": "abc"}, {"Issuer": "y", "Id": UID}]) == UID
    assert devbox.okta_uid_from_external_ids([{"Issuer": "x", "Id": "ada.lovelace@example.com"}]) is None
    assert devbox.okta_uid_from_external_ids(None) is None


def test_authorizer_is_exactly_spec_4(devbox, settings):
    assert devbox.authorizer(settings, UID) == {
        "customJWTAuthorizer": {
            "discoveryUrl": "https://example.okta.com/oauth2/default/.well-known/openid-configuration",
            "allowedAudience": ["api://default"],
            "allowedClients": ["0oaDEVBOXSPA1234567"],
            "allowedScopes": ["devbox"],
            "customClaims": [
                {
                    "inboundTokenClaimName": "groups",
                    "inboundTokenClaimValueType": "STRING_ARRAY",
                    "authorizingClaimMatchValue": {
                        "claimMatchValue": {"matchValueStringList": ["devbox-users"]},
                        "claimMatchOperator": "CONTAINS_ANY",
                    },
                },
                {
                    "inboundTokenClaimName": "uid",
                    "inboundTokenClaimValueType": "STRING",
                    "authorizingClaimMatchValue": {
                        "claimMatchValue": {"matchValueString": UID},
                        "claimMatchOperator": "EQUALS",
                    },
                },
            ],
        }
    }


# ----------------------------------------------------------------------------- IAM
def test_shared_roles_validate(devbox):
    roles = devbox.iam_roles(ACCOUNT, "us-east-1")
    assert set(roles) == {"devbox-edge-lambda", "devbox-tools-gateway"}, (
        "no operator or instance role: there are no capacity providers"
    )
    for name, spec in roles.items():
        validate("iam", "CreateRole", devbox.role_request(name, spec["trust"], spec["description"]))
        for pname, doc in spec["inline"].items():
            validate("iam", "PutRolePolicy", {"RoleName": name, "PolicyName": pname, "PolicyDocument": json.dumps(doc)})
        for m in spec["managed"]:
            validate("iam", "AttachRolePolicy", {"RoleName": name, "PolicyArn": m})


@pytest.mark.parametrize("name", ["ada", "grace"])
def test_each_persons_execution_role_validates(devbox, settings, name):
    spec = exec_spec(devbox, settings, name)
    role = person(devbox, name).exec_role
    assert role == f"devbox-exec-{name}"
    validate("iam", "CreateRole", devbox.role_request(role, spec["trust"], spec["description"]))
    for pname, doc in spec["inline"].items():
        validate("iam", "PutRolePolicy", {"RoleName": role, "PolicyName": pname, "PolicyDocument": json.dumps(doc)})
    assert spec["managed"] == []


def test_execution_role_has_no_bedrock_and_pulls_one_repo(devbox, settings):
    spec = exec_spec(devbox, settings)
    (doc,) = spec["inline"].values()
    acts = actions(doc)
    assert not [a for a in acts if a.startswith("bedrock")], (
        "the box can read these credentials: no Bedrock or AgentCore"
    )
    assert not [a for a in acts if "GetWorkloadAccessToken" in a]
    assert {a.split(":")[0] for a in acts} == {"ecr", "logs", "elasticfilesystem"}
    for st in doc["Statement"]:
        a = [st["Action"]] if isinstance(st["Action"], str) else st["Action"]
        if any(x in ("ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer") for x in a):
            assert st["Resource"] == f"arn:aws:ecr:us-east-1:{ACCOUNT}:repository/devbox-box"
        if any(x.startswith("logs:") and x != "logs:DescribeLogGroups" for x in a):
            assert "/aws/bedrock-agentcore/runtimes/devbox_vm_ada-" in st["Resource"], (
                "only this person's runtime's logs"
            )
    trust = spec["trust"]["Statement"][0]
    assert (
        trust["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
        and "aws:SourceAccount" in trust["Condition"]["StringEquals"]
    )


def test_execution_role_mounts_only_its_own_access_point(devbox, settings):
    """AgentCore's filesystem doc: ClientMount + ClientWrite on the file system, with an elasticfilesystem:AccessPointArn
    condition. With that condition ada's box can't mount grace's folder, or the file system's root."""
    (doc,) = exec_spec(devbox, settings)["inline"].values()
    efs = [
        st
        for st in doc["Statement"]
        if any(
            a.startswith("elasticfilesystem:")
            for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"])
        )
    ]
    assert len(efs) == 2
    st, describe = efs
    assert st["Effect"] == "Allow" and sorted(st["Action"]) == [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
    ]
    assert st["Resource"] == FS_ARN
    assert st["Condition"] == {"ArnEquals": {"elasticfilesystem:AccessPointArn": AP_ARN}}
    assert "elasticfilesystem:ClientRootAccess" not in actions(doc)
    assert devbox.EFS_CLIENT_ACTIONS == ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    # CreateAgentRuntime also wants these two (live: "Execution role is missing required filesystem permissions"),
    # on this person's file system and access point only
    assert describe["Effect"] == "Allow" and sorted(describe["Action"]) == sorted(devbox.EFS_DESCRIBE_ACTIONS)
    assert sorted(devbox.EFS_DESCRIBE_ACTIONS) == [
        "elasticfilesystem:DescribeAccessPoints",
        "elasticfilesystem:DescribeMountTargets",
    ]
    assert sorted(describe["Resource"]) == sorted([FS_ARN, AP_ARN]) and "Condition" not in describe
    (grace_doc,) = exec_spec(devbox, settings, "grace", GRACE_AP_ARN)["inline"].values()
    assert GRACE_AP_ARN in json.dumps(grace_doc) and AP_ARN not in json.dumps(grace_doc)
    assert GRACE_AP_ARN not in json.dumps(doc)


def test_edge_lambda_role_is_logs_only(devbox):
    spec = devbox.iam_roles(ACCOUNT, "us-east-1")["devbox-edge-lambda"]
    acts = actions(spec["inline"]["own-logs-only"])
    assert acts and all(a.startswith("logs:") for a in acts)
    assert spec["managed"] == []
    assert spec["trust"]["Statement"][0]["Principal"] == {"Service": "lambda.amazonaws.com"}


def test_no_role_grants_bedrock_model_access(devbox, settings):
    specs = {
        **devbox.iam_roles(ACCOUNT, "us-east-1"),
        **{f"devbox-exec-{n}": exec_spec(devbox, settings, n) for n in TIERS},
        "devbox-provisioner": devbox.provisioner_spec(
            ACCOUNT,
            "us-east-1",
            FS_ID,
            f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary",
            subnet_id=SUBNET,
            security_group_id=BOX_SG,
        ),
    }
    for name, spec in specs.items():
        for doc in spec["inline"].values():
            assert not [a for a in actions(doc) if a.startswith("bedrock:")], name


# ----------------------------------------------------------------------------- the box: a microVM runtime
def test_runtime_request(devbox, runtime_req, settings):
    validate("bedrock-agentcore-control", "CreateAgentRuntime", runtime_req)
    assert runtime_req["agentRuntimeName"] == "devbox_vm_ada"
    assert "capacityProviderConfiguration" not in runtime_req, "that would make it an Instances runtime"
    assert runtime_req["networkConfiguration"] == {
        "networkMode": "VPC",
        "networkModeConfig": {"subnets": [SUBNET], "securityGroups": [BOX_SG]},
    }
    assert "requireServiceS3Endpoint" not in runtime_req["networkConfiguration"]["networkModeConfig"], (
        "CreateAgentRuntime refuses it; the VPC's own S3 gateway endpoint carries the image layers"
    )
    assert runtime_req["filesystemConfigurations"] == [
        {"efsAccessPoint": {"accessPointArn": AP_ARN, "mountPath": "/mnt/workspace"}}
    ]
    assert runtime_req["roleArn"] == EXEC_ARN
    assert runtime_req["protocolConfiguration"] == {"serverProtocol": "HTTP"}
    assert runtime_req["requestHeaderConfiguration"] == {
        "requestHeaderAllowlist": ["Authorization", "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath"]
    }
    assert runtime_req["lifecycleConfiguration"] == {"idleRuntimeSessionTimeout": 3600, "maxLifetime": 28800}
    assert runtime_req["authorizerConfiguration"] == devbox.authorizer(settings, UID)
    assert runtime_req["tags"]["devbox-user"] == "ada"


def test_mount_path_is_one_level_under_mnt(devbox):
    """AgentCore's rule: /mnt/ plus exactly one folder (the model's pattern)."""
    shape = model("bedrock-agentcore-control").shape_for("EfsAccessPointConfiguration").members["mountPath"]
    assert re.fullmatch(shape.metadata["pattern"], devbox.MOUNT_PATH) and devbox.MOUNT_PATH == "/mnt/workspace"


def test_microvm_lifetime_is_the_most_a_microvm_allows(devbox):
    """idle and max are 60..28800 s on microVMs (the model's own limit is the Instances one, 1209600)."""
    shape = model("bedrock-agentcore-control").shape_for("LifecycleConfiguration")
    assert devbox.VM_MAX_LIFETIME == 28800 <= shape.members["maxLifetime"].metadata["max"]
    assert shape.members["idleRuntimeSessionTimeout"].metadata["min"] == 60


def test_runtime_env_is_spec_8(devbox, runtime_req):
    env = runtime_req["environmentVariables"]
    assert set(env) == {
        "DEVBOX_OWNER",
        "DEVBOX_OWNER_UID",
        "DEVBOX_SESSION_ID",
        "DEVBOX_TIER",
        "DEVBOX_SSO_ROLE",
        "DEVBOX_ACCOUNT_ID",
        "DEVBOX_SSO_START_URL",
        "DEVBOX_SSO_REGION",
        "DEVBOX_MODELS",
        "DEVBOX_TOOLS_GATEWAY_URL",
        "DEVBOX_WORKSPACE_FSTYPE",
    }
    assert env["DEVBOX_SESSION_ID"] == devbox.session_id(UID, 1), "generation 1 of the new runtime"
    assert env["DEVBOX_WORKSPACE_FSTYPE"] == "nfs", "the box waits for the EFS mount, not the microVM's placeholder"
    assert (
        env["DEVBOX_SSO_ROLE"] == "ClaudeCode-Power" and env["DEVBOX_TIER"] == "Power" and env["DEVBOX_OWNER"] == "ada"
    )
    assert json.loads(env["DEVBOX_MODELS"]) == {
        "opus": "us.anthropic.claude-opus-5",
        "sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    assert env["DEVBOX_TOOLS_GATEWAY_URL"] == GATEWAY_URL


def test_standard_tier_gets_no_opus(devbox, settings):
    grace = person(devbox, "grace")
    env = devbox.runtime_env(
        settings, grace, uid=GRACE_UID, generation=1, account=ACCOUNT, start_url="u", gateway_url="g"
    )
    assert (
        set(json.loads(env["DEVBOX_MODELS"])) == {"sonnet", "haiku"} and env["DEVBOX_SSO_ROLE"] == "ClaudeCode-Standard"
    )


def test_runtime_update_requests(devbox, runtime_req):
    upd = devbox.runtime_update_request("devbox_vm_ada-abcdefghij", runtime_req)
    validate("bedrock-agentcore-control", "UpdateAgentRuntime", upd)
    assert upd["metadataConfiguration"] == {"requireMMDSV2": True}
    assert "agentRuntimeName" not in upd and "tags" not in upd and "capacityProviderConfiguration" not in upd
    assert upd["networkConfiguration"] == runtime_req["networkConfiguration"], (
        "an update replaces the whole configuration"
    )
    assert upd["filesystemConfigurations"] == runtime_req["filesystemConfigurations"]
    plain = devbox.runtime_update_request("devbox_vm_ada-abcdefghij", runtime_req, require_mmdsv2=False)
    assert "metadataConfiguration" not in plain
    current = {
        **{k: v for k, v in runtime_req.items() if k != "tags"},
        "agentRuntimeId": "devbox_vm_ada-abcdefghij",
        "metadataConfiguration": {"requireMMDSV2": True},
        "status": "READY",
    }
    env = dict(runtime_req["environmentVariables"], DEVBOX_SESSION_ID=devbox.session_id(UID, 2))
    reset = devbox.runtime_update_from_current(current, env)
    validate("bedrock-agentcore-control", "UpdateAgentRuntime", reset)
    assert reset["environmentVariables"]["DEVBOX_SESSION_ID"] == devbox.session_id(UID, 2)
    assert reset["authorizerConfiguration"] == runtime_req["authorizerConfiguration"]
    assert reset["networkConfiguration"] == runtime_req["networkConfiguration"]
    assert reset["filesystemConfigurations"] == runtime_req["filesystemConfigurations"]
    assert reset["metadataConfiguration"] == {"requireMMDSV2": True}
    without = devbox.runtime_update_from_current(
        {k: v for k, v in current.items() if k != "metadataConfiguration"}, env
    )
    assert "metadataConfiguration" not in without, "don't force MMDSv2 on a runtime that refused it"


def test_runtime_drift(devbox, runtime_req):
    current = {k: v for k, v in runtime_req.items() if k != "tags"}
    assert devbox.runtime_drift(current, runtime_req) == []
    changed = json.loads(json.dumps(current))
    changed["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] = IMAGE[:-4] + "ffff"
    changed["environmentVariables"]["DEVBOX_SESSION_ID"] = "dbx-other"
    changed["filesystemConfigurations"][0]["efsAccessPoint"]["accessPointArn"] = GRACE_AP_ARN
    assert devbox.runtime_drift(changed, runtime_req) == [
        "agentRuntimeArtifact",
        "filesystemConfigurations",
        "environmentVariables",
    ]
    assert devbox.mmdsv2_needed(current) and not devbox.mmdsv2_needed(
        {"metadataConfiguration": {"requireMMDSV2": True}}
    )


def test_runtime_drift_ignores_fields_the_service_adds(devbox, runtime_req):
    current = json.loads(json.dumps({k: v for k, v in runtime_req.items() if k != "tags"}))
    current["authorizerConfiguration"]["customJWTAuthorizer"]["allowedWorkloadConfiguration"] = {
        "workloadIdentities": ["x"]
    }
    current["lifecycleConfiguration"]["somethingNew"] = 1
    current["networkConfiguration"]["networkModeConfig"]["requireServiceS3Endpoint"] = False
    assert devbox.runtime_drift(current, runtime_req) == []
    current["environmentVariables"]["LEFTOVER"] = "1"
    assert devbox.runtime_drift(current, runtime_req) == ["environmentVariables"]


def test_runtime_resource_policy_allows_the_terminal_and_denies_the_rest(devbox):
    doc = devbox.runtime_resource_policy(RUNTIME_ARN)
    validate("bedrock-agentcore-control", "PutResourcePolicy", {"resourceArn": RUNTIME_ARN, "policy": json.dumps(doc)})
    deny = [s for s in doc["Statement"] if s["Effect"] == "Deny"]
    allow = [s for s in doc["Statement"] if s["Effect"] == "Allow"]
    assert (
        len(deny) == 1
        and deny[0]["Principal"] == "*"
        and deny[0]["Resource"] == RUNTIME_ARN
        and "Condition" not in deny[0]
    )
    assert sorted(deny[0]["Action"]) == sorted(DENIED)
    assert (
        len(allow) == 1
        and allow[0]["Principal"] == "*"
        and allow[0]["Resource"] == RUNTIME_ARN
        and "Condition" not in allow[0]
    )
    assert sorted(allow[0]["Action"]) == sorted(ALLOWED), (
        "the owner's workbench (/invocations, /ws) and terminal (/ws/shells)"
    )
    assert not set(allow[0]["Action"]) & set(deny[0]["Action"])
    assert sorted(devbox.RUNTIME_ALLOWED_ACTIONS) == sorted(ALLOWED) and sorted(
        devbox.RUNTIME_DENIED_ACTIONS
    ) == sorted(DENIED)


# ----------------------------------------------------------------------------- EFS
def test_file_system_request(devbox):
    req = devbox.file_system_request()
    validate("efs", "CreateFileSystem", req)
    assert (req["CreationToken"], req["PerformanceMode"], req["ThroughputMode"], req["Encrypted"]) == (
        "devbox",
        "generalPurpose",
        "elastic",
        True,
    )
    assert {"Key": "Name", "Value": "devbox"} in req["Tags"] and {"Key": "devbox", "Value": "remote-dev-box"} in req[
        "Tags"
    ]
    assert "AvailabilityZoneName" not in req, "Regional (not One Zone): the mount target decides the AZ"
    validate("efs", "DescribeFileSystems", {"CreationToken": req["CreationToken"]})


def test_mount_target_request(devbox):
    req = devbox.mount_target_request(FS_ID, SUBNET, EFS_SG)
    validate("efs", "CreateMountTarget", req)
    assert req == {"FileSystemId": FS_ID, "SubnetId": SUBNET, "SecurityGroups": [EFS_SG]}
    validate(
        "efs",
        "ModifyMountTargetSecurityGroups",
        {"MountTargetId": "fsmt-0123456789abcdef0", "SecurityGroups": [EFS_SG]},
    )


@pytest.mark.parametrize("name", ["ada", "grace"])
def test_access_point_request(devbox, settings, name):
    u = person(devbox, name)
    req = devbox.access_point_request(FS_ID, u, devbox.request_id(name) + "x" * 80)
    validate("efs", "CreateAccessPoint", req)
    assert len(req["ClientToken"]) <= 64
    assert req["PosixUser"] == {"Uid": 1000, "Gid": 1000}, (
        "every file operation runs as the box's dev user, whatever uid the box has"
    )
    assert req["RootDirectory"] == {
        "Path": f"/devbox/{name}",
        "CreationInfo": {"OwnerUid": 1000, "OwnerGid": 1000, "Permissions": "0750"},
    }
    assert {"Key": "devbox-user", "Value": name} in req["Tags"] and {"Key": "Name", "Value": f"devbox-{name}"} in req[
        "Tags"
    ]


def test_access_point_drift(devbox, ada):
    req = devbox.access_point_request(FS_ID, ada, "t")
    ap = {"AccessPointId": "fsap-1", "PosixUser": req["PosixUser"], "RootDirectory": req["RootDirectory"]}
    assert devbox.access_point_drift(ap, ada) == []
    ap = {
        **ap,
        "PosixUser": {"Uid": 0, "Gid": 0, "SecondaryGids": [1]},
        "RootDirectory": {"Path": "/devbox/ada", "CreationInfo": {"OwnerUid": 0, "OwnerGid": 0, "Permissions": "0777"}},
    }
    assert devbox.access_point_drift(ap, ada) == ["POSIX user 0:0", "creation info 0:0 0777"]


def test_file_system_policy(devbox):
    """Without a file system policy EFS lets in any NFS client that reaches the mount target, with no IAM at all.
    This one allows only each person's role, only through their own access point, only over TLS."""
    doc = devbox.file_system_policy(FS_ARN, {"grace": (GRACE_EXEC_ARN, GRACE_AP_ARN), "ada": (EXEC_ARN, AP_ARN)})
    validate("efs", "PutFileSystemPolicy", {"FileSystemId": FS_ID, "Policy": json.dumps(doc)})
    allow = [s for s in doc["Statement"] if s["Effect"] == "Allow"]
    deny = [s for s in doc["Statement"] if s["Effect"] == "Deny"]
    assert [(s["Principal"], s["Condition"]) for s in allow] == [
        ({"AWS": EXEC_ARN}, {"ArnEquals": {"elasticfilesystem:AccessPointArn": AP_ARN}}),
        ({"AWS": GRACE_EXEC_ARN}, {"ArnEquals": {"elasticfilesystem:AccessPointArn": GRACE_AP_ARN}}),
    ]
    for s in allow:
        assert (
            sorted(s["Action"]) == ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
            and s["Resource"] == FS_ARN
        )
    assert not [s for s in allow if s["Principal"] in ("*", {"AWS": "*"})], "no anonymous NFS client"
    assert "elasticfilesystem:ClientRootAccess" not in [a for s in allow for a in s["Action"]]
    (tls,) = deny
    assert tls["Condition"] == {"Bool": {"aws:SecureTransport": "false"}} and tls["Principal"] == {"AWS": "*"}
    assert len({s["Sid"] for s in doc["Statement"]}) == len(doc["Statement"])
    validate("efs", "DescribeFileSystemPolicy", {"FileSystemId": FS_ID})


def test_file_system_arn(devbox):
    assert devbox.file_system_arn(ACCOUNT, "us-east-1", FS_ID) == FS_ARN


# ----------------------------------------------------------------------------- the S3 gateway endpoint
def test_s3_gateway_endpoint_carries_ecr_layers_only(devbox):
    req = devbox.s3_endpoint_request("vpc-0123456789abcdef0", "rtb-0123456789abcdef0", "us-east-1")
    validate("ec2", "CreateVpcEndpoint", req)
    assert (req["VpcEndpointType"], req["ServiceName"], req["RouteTableIds"]) == (
        "Gateway",
        "com.amazonaws.us-east-1.s3",
        ["rtb-0123456789abcdef0"],
    )
    doc = json.loads(req["PolicyDocument"])
    assert doc == devbox.s3_endpoint_policy("us-east-1")
    (st,) = doc["Statement"]
    assert (st["Effect"], st["Principal"], st["Action"]) == ("Allow", "*", "s3:GetObject")
    assert st["Resource"] == "arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*"
    # AgentCore's session-storage bucket (acr-storage-*) is only for managed session storage, which the box doesn't use
    assert "acr-storage" not in req["PolicyDocument"]
    validate(
        "ec2",
        "ModifyVpcEndpoint",
        {
            "VpcEndpointId": "vpce-0123456789abcdef0",
            "PolicyDocument": req["PolicyDocument"],
            "AddRouteTableIds": ["rtb-1"],
            "RemoveRouteTableIds": ["rtb-2"],
        },
    )


# ----------------------------------------------------------------------------- .state.json
def legacy_state():
    """deploy/.state.json as the Instances deploy left it."""
    cp = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:capacity-provider/devbox_ada-STrMhcDNYm"
    return {
        "account": ACCOUNT,
        "boxes": {
            "ada": {
                "name": "ada",
                "uid": UID,
                "generation": 1,
                "cpArn": cp,
                "cpId": "devbox_ada-STrMhcDNYm",
                "runtimeArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_ada-7MCjP8DEFc",
                "runtimeId": "devbox_ada-7MCjP8DEFc",
                "sessionId": "dbx-" + "0" * 64,
            },
            "grace": {
                "name": "grace",
                "uid": GRACE_UID,
                "generation": 4,
                "cpId": "devbox_grace-zVqNAXPg41",
                "runtimeArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_grace-QBPhAYAds3",
            },
        },
    }


def test_migrate_state_keeps_the_instances_records_for_retire(devbox):
    state = devbox.migrate_state(legacy_state())
    assert state["instances"] == legacy_state()["boxes"], (
        "retire-instances needs their uid, generation and capacity provider"
    )
    assert state["boxes"] == {"ada": {"name": "ada", "uid": UID}, "grace": {"name": "grace", "uid": GRACE_UID}}, (
        "a fresh microVM record: the new runtime starts at generation 1"
    )
    again = devbox.migrate_state(json.loads(json.dumps(state)))
    assert again == state, "idempotent"
    vm = {"name": "ada", "uid": UID, "generation": 2, "compute": "microvm", "runtimeArn": RUNTIME_ARN}
    assert devbox.migrate_state({"boxes": {"ada": dict(vm)}}) == {"boxes": {"ada": vm}}, "a microVM record stays"
    assert devbox.is_legacy_record(legacy_state()["boxes"]["grace"]) and not devbox.is_legacy_record(vm)
    assert not devbox.is_legacy_record({"name": "ada", "uid": UID}), (
        "a record with only a uid (before the runtime exists)"
    )


# ----------------------------------------------------------------------------- network
def test_network_requests_validate(devbox):
    for name in devbox.SUBNETS:
        validate("ec2", "CreateSubnet", devbox.subnet_request("vpc-0123456789abcdef0", name, "us-east-1a"))
    validate("ec2", "CreateVpc", {"CidrBlock": devbox.VPC_CIDR, "TagSpecifications": devbox.tag_spec("vpc", "devbox")})
    validate(
        "ec2",
        "AuthorizeSecurityGroupEgress",
        {"GroupId": BOX_SG, "IpPermissions": devbox.sg_egress_wanted(EFS_SG)},
    )
    validate("ec2", "RevokeSecurityGroupEgress", {"GroupId": BOX_SG, "IpPermissions": devbox.DEFAULT_EGRESS})
    validate(
        "ec2",
        "AuthorizeSecurityGroupIngress",
        {"GroupId": EFS_SG, "IpPermissions": devbox.efs_sg_ingress_wanted(BOX_SG)},
    )
    validate(
        "ec2",
        "CreateSecurityGroup",
        {
            "GroupName": devbox.EFS_SG,
            "VpcId": "vpc-0123456789abcdef0",
            "Description": "x",
            "TagSpecifications": devbox.tag_spec("security-group", devbox.EFS_SG),
        },
    )


def test_cidrs_match_spec(devbox):
    assert devbox.VPC_CIDR == "10.40.0.0/16"
    assert devbox.SUBNETS == {
        "devbox-box": "10.40.1.0/24",
        "devbox-firewall": "10.40.2.0/28",
        "devbox-public": "10.40.3.0/24",
    }


def test_security_group_rules(devbox):
    dns = {("udp", 53, 53, "10.40.0.2/32"), ("tcp", 53, 53, "10.40.0.2/32")}
    nfs = {("tcp", 2049, 2049, EFS_SG)}
    # no port 80 (a Host header is a string anyone can set; nothing on the allowlist needs plain HTTP)
    assert {devbox.rule_key(r) for r in devbox.sg_egress_wanted(EFS_SG)} == {("tcp", 443, 443, "0.0.0.0/0")} | dns | nfs
    assert not [r for r in devbox.sg_egress_wanted() if r["FromPort"] == 2049], "no NFS rule until devbox-efs exists"
    # the mount target's group: NFS in from the boxes' group, and nothing else
    assert [devbox.rule_key(r) for r in devbox.efs_sg_ingress_wanted(BOX_SG)] == [("tcp", 2049, 2049, BOX_SG)]
    # a describe_security_group_rules row keys the same way as the IpPermission that made it
    assert devbox.rule_key(
        {"IsEgress": True, "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "CidrIpv4": "0.0.0.0/0"}
    ) == ("tcp", 443, 443, "0.0.0.0/0")
    assert devbox.rule_key(
        {
            "IsEgress": True,
            "IpProtocol": "tcp",
            "FromPort": 2049,
            "ToPort": 2049,
            "ReferencedGroupInfo": {"GroupId": EFS_SG},
        }
    ) == ("tcp", 2049, 2049, EFS_SG)


def test_route_plan_is_spec_9(devbox):
    plan = devbox.route_plan({"firewall_endpoint": "vpce-1", "nat": "nat-1", "igw": "igw-1"})
    assert plan == {
        "devbox-rt-box": [("0.0.0.0/0", "VpcEndpointId", "vpce-1")],
        "devbox-rt-firewall": [("0.0.0.0/0", "NatGatewayId", "nat-1")],
        "devbox-rt-public": [("0.0.0.0/0", "GatewayId", "igw-1"), ("10.40.1.0/24", "VpcEndpointId", "vpce-1")],
    }
    for table in plan.values():
        for dest, kind, target in table:
            validate("ec2", "CreateRoute", {"RouteTableId": "rtb-1", "DestinationCidrBlock": dest, kind: target})


def test_microvm_availability_zones(devbox):
    """AgentCore VPC mode's supported AZ ids in us-east-1 (docs: agentcore-vpc.html); check tests DEVBOX_AZ against them."""
    assert devbox.MICROVM_AZ_IDS["us-east-1"] == ("use1-az1", "use1-az2", "use1-az4")


def test_this_boto3_can_make_a_microvm_with_efs(devbox):
    assert devbox.has_microvm_efs_api()


def shipped_allowlist(devbox, settings, fs_id=FS_ID):
    return devbox.parse_allowlist(
        (devbox.TEMPLATES / "egress-allowlist.txt").read_text(),
        devbox.allowlist_values(ACCOUNT, GATEWAY_URL, settings, fs_id),
    )


def test_allowlist(devbox, settings):
    domains = shipped_allowlist(devbox, settings)
    host = f"{GATEWAY_ID}.gateway.bedrock-agentcore.us-east-1.amazonaws.com"
    for d in (
        "bedrock-runtime.us-east-1.amazonaws.com",
        "bedrock.us-east-1.amazonaws.com",
        "sts.us-east-1.amazonaws.com",
        "oidc.us-east-1.amazonaws.com",
        "portal.sso.us-east-1.amazonaws.com",
        host,
        "api.ecr.us-east-1.amazonaws.com",
        f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com",
        "prod-us-east-1-starport-layer-bucket.s3.us-east-1.amazonaws.com",
        "logs.us-east-1.amazonaws.com",
        f".{FS_ID}.efs.us-east-1.amazonaws.com",
    ):
        assert d in domains
    assert not [d for d in domains if "{" in d]
    text = (devbox.TEMPLATES / "egress-allowlist.txt").read_text()
    assert "FOUND IN USE" in text
    validate("network-firewall", "CreateRuleGroup", devbox.allowlist_rule_group_request(domains))
    with pytest.raises(ValueError):
        devbox.parse_allowlist("{{MISSING}}\n", {})
    with pytest.raises(ValueError):
        devbox.parse_allowlist("not a domain!\n", {})


def test_allowlist_has_this_file_systems_mount_target_name_only(devbox, settings):
    """AgentCore resolves <az-id>.<fs-id>.efs.<region>.amazonaws.com when it mounts (docs: runtime-filesystem-configurations)."""
    domains = shipped_allowlist(devbox, settings)
    assert devbox.domain_allowed(f"use1-az1.{FS_ID}.efs.us-east-1.amazonaws.com", domains)
    assert not devbox.domain_allowed("use1-az1.fs-0badbadbadbadbad0.efs.us-east-1.amazonaws.com", domains), (
        "not every file system"
    )
    assert f"*.{FS_ID}.efs.us-east-1.amazonaws.com" in devbox.dns_domains(domains)
    before = shipped_allowlist(devbox, settings, "")  # before the file system exists: a placeholder, never a wildcard
    assert ".fs-pending.efs.us-east-1.amazonaws.com" in before and not [d for d in before if d.startswith(".efs.")]


def test_allowlist_follows_idc_region(devbox, env):
    """aws sso login in the box talks to IDC_REGION's endpoints, so enforce mode must allow those, not us-east-1's."""
    s = devbox.load_settings({**env, "IDC_REGION": "eu-west-1"})
    assert not s.errors
    domains = shipped_allowlist(devbox, s)
    assert "oidc.eu-west-1.amazonaws.com" in domains and "portal.sso.eu-west-1.amazonaws.com" in domains
    assert "oidc.us-east-1.amazonaws.com" not in domains and "portal.sso.us-east-1.amazonaws.com" not in domains
    assert f"{GATEWAY_ID}.gateway.bedrock-agentcore.us-east-1.amazonaws.com" in domains
    assert any("IDC_REGION=Europe" in e for e in devbox.load_settings({**env, "IDC_REGION": "Europe"}).errors)


def test_dns_firewall_requests(devbox):
    domains = devbox.dns_domains(["bedrock-runtime.us-east-1.amazonaws.com", ".example.com", "a.example.com"])
    assert domains == ["bedrock-runtime.us-east-1.amazonaws.com", "example.com", "*.example.com", "a.example.com"]
    for name in (devbox.DNS_ALLOW_LIST, devbox.DNS_ANY_LIST):
        validate(
            "route53resolver",
            "CreateFirewallDomainList",
            {"CreatorRequestId": devbox.request_id(name), "Name": name, "Tags": devbox.tag_list(name)},
        )
    validate(
        "route53resolver",
        "UpdateFirewallDomains",
        {"FirewallDomainListId": "rslvr-fdl-1", "Operation": "REPLACE", "Domains": domains},
    )
    rules = devbox.dns_rules_wanted("rslvr-fdl-1", "rslvr-fdl-2")
    for want in rules:
        validate(
            "route53resolver",
            "CreateFirewallRule",
            {"CreatorRequestId": "x", "FirewallRuleGroupId": "rslvr-frg-1", **want},
        )
        validate("route53resolver", "UpdateFirewallRule", {"FirewallRuleGroupId": "rslvr-frg-1", **want})
    # 100: the allowlist, trusting the CNAME chain (AWS names are aliases); 200: everything else
    assert rules[0]["Action"] == "ALLOW" and rules[0]["Priority"] == 100
    assert rules[0]["FirewallDomainRedirectionAction"] == "TRUST_REDIRECTION_DOMAIN"
    assert (rules[1]["Action"], rules[1]["Priority"], rules[1]["BlockResponse"]) == ("BLOCK", 200, "NXDOMAIN")
    assert devbox.dns_blocks_the_rest([{**rules[1]}], "rslvr-fdl-2")
    assert not devbox.dns_blocks_the_rest([{**rules[1], "Action": "ALERT"}], "rslvr-fdl-2")
    assert devbox.dns_rule_matches({**rules[1]}, rules[1])
    assert not devbox.dns_rule_matches({**rules[1], "Action": "ALERT"}, rules[1])
    assert not devbox.dns_rule_matches(
        {**rules[0], "FirewallDomainRedirectionAction": "INSPECT_REDIRECTION_DOMAIN"}, rules[0]
    )
    validate(
        "route53resolver",
        "AssociateFirewallRuleGroup",
        {
            "CreatorRequestId": "x",
            "FirewallRuleGroupId": "rslvr-frg-1",
            "VpcId": "vpc-0123456789abcdef0",
            "Priority": devbox.DNS_ASSOCIATION_PRIORITY,
            "Name": devbox.DNS_RULE_GROUP,
            "MutationProtection": "DISABLED",
        },
    )
    assert 100 < devbox.DNS_ASSOCIATION_PRIORITY < 9900
    dest = devbox.dns_query_log_destination(ACCOUNT, "us-east-1")
    assert dest == f"arn:aws:logs:us-east-1:{ACCOUNT}:log-group:/devbox/dns-queries:*"
    validate(
        "route53resolver",
        "CreateResolverQueryLogConfig",
        {"Name": devbox.DNS_QUERY_LOG, "DestinationArn": dest, "CreatorRequestId": "x", "Tags": devbox.tag_list()},
    )
    validate(
        "route53resolver",
        "UpdateFirewallConfig",
        {"ResourceId": "vpc-0123456789abcdef0", "FirewallFailOpen": "DISABLED"},
    )
    with pytest.raises(ValueError):
        devbox.dns_domains([f"h{i}.example.com" for i in range(1001)])


def test_the_shipped_allowlist_fits_dns_firewall(devbox, settings):
    validate(
        "route53resolver",
        "UpdateFirewallDomains",
        {
            "FirewallDomainListId": "rslvr-fdl-1",
            "Operation": "REPLACE",
            "Domains": devbox.dns_domains(shipped_allowlist(devbox, settings)),
        },
    )


def test_domain_matching(devbox):
    allow = ["bedrock-runtime.us-east-1.amazonaws.com", ".example.com"]
    assert devbox.domain_allowed("bedrock-runtime.us-east-1.amazonaws.com", allow)
    assert devbox.domain_allowed("example.com", allow) and devbox.domain_allowed("a.b.example.com", allow)
    assert not devbox.domain_allowed("evil-example.com", allow)
    assert not devbox.domain_allowed("bedrock-runtime.us-east-1.amazonaws.com.evil.net", allow)
    assert devbox.unlisted(["x.example.com", "github.com", "GitHub.com", ""], allow) == ["github.com"]


def test_firewall_policies(devbox):
    allow_arn = f"arn:aws:network-firewall:us-east-1:{ACCOUNT}:stateful-rulegroup/devbox-allowlist"
    other_arn = f"arn:aws:network-firewall:us-east-1:{ACCOUNT}:stateful-rulegroup/other"
    doc = devbox.firewall_policy_doc(allow_arn, ["aws:drop_established", "aws:alert_established"])
    validate("network-firewall", "CreateFirewallPolicy", devbox.firewall_policy_request(doc))
    assert doc["StatefulEngineOptions"] == {"RuleOrder": "STRICT_ORDER"}
    assert [r["ResourceArn"] for r in doc["StatefulRuleGroupReferences"]] == [allow_arn]
    assert any("drop" in a for a in doc["StatefulDefaultActions"]) and any(
        "alert" in a for a in doc["StatefulDefaultActions"]
    )
    assert devbox.policy_uses(doc, allow_arn) and not devbox.policy_uses(doc, other_arn)
    assert not devbox.policy_uses(doc, None)
    ag = devbox.allowlist_rule_group(["a.example.com"])["RulesSource"]["RulesSourceList"]
    assert ag["TargetTypes"] == ["TLS_SNI", "HTTP_HOST"] and ag["GeneratedRulesType"] == "ALLOWLIST"
    validate(
        "network-firewall",
        "CreateFirewall",
        devbox.firewall_request(
            "arn:aws:network-firewall:us-east-1:1:firewall-policy/p",
            "vpc-0123456789abcdef0",
            "subnet-0123456789abcdef0",
        ),
    )


def test_covers_ignores_service_defaults(devbox):
    want = {"StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"}, "StatefulDefaultActions": ["aws:alert_established"]}
    assert devbox.covers(
        {**want, "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER", "StreamExceptionPolicy": "DROP"}}, want
    )
    assert not devbox.covers({**want, "StatefulDefaultActions": ["aws:drop_strict"]}, want)
    assert devbox.covers({}, {"StatefulDefaultActions": []}) and not devbox.covers(
        {"StatefulDefaultActions": ["aws:drop_strict"]}, {"StatefulDefaultActions": []}
    )


def test_firewall_logging_one_destination_per_call(devbox):
    steps = devbox.logging_steps([])
    assert [[c["LogType"] for c in s] for s in steps] == [["ALERT"], ["ALERT", "FLOW"]]
    for s in steps:
        validate(
            "network-firewall",
            "UpdateLoggingConfiguration",
            {"FirewallName": "devbox-fw", "LoggingConfiguration": {"LogDestinationConfigs": s}},
        )
        assert all(c["LogDestination"] == {"logGroup": "/devbox/network-firewall"} for c in s)
    assert devbox.logging_steps(devbox.firewall_logging_wanted()) == []


# ----------------------------------------------------------------------------- tools gateway
def test_gateway_requests(devbox):
    role = f"arn:aws:iam::{ACCOUNT}:role/devbox-tools-gateway"
    engine_arn = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:policy-engine/DevboxToolsPolicies-abcde12345"
    gw = devbox.gateway_request(role)
    validate("bedrock-agentcore-control", "CreateGateway", gw)
    assert gw["authorizerType"] == "AWS_IAM" and gw["name"] == "devbox-tools"
    validate("bedrock-agentcore-control", "CreateGatewayTarget", devbox.gateway_target_request(GATEWAY_ID))
    validate("bedrock-agentcore-control", "CreatePolicyEngine", devbox.policy_engine_request())
    stmt = devbox.cedar_statement(ACCOUNT, "us-east-1", GATEWAY_ID)
    validate(
        "bedrock-agentcore-control", "CreatePolicy", devbox.cedar_policy_request("DevboxToolsPolicies-abcde12345", stmt)
    )
    assert "principal is AgentCore::IamEntity" in stmt and 'principal.id like "*AWSReservedSSO_ClaudeCode-*"' in stmt
    assert f'AgentCore::Gateway::"{GATEWAY_ARN}"' in stmt
    validate(
        "bedrock-agentcore-control", "UpdateGateway", devbox.attach_policy_engine_request(GATEWAY_ID, role, engine_arn)
    )
    rbp = devbox.gateway_resource_policy(GATEWAY_ARN, ACCOUNT)
    validate("bedrock-agentcore-control", "PutResourcePolicy", {"resourceArn": GATEWAY_ARN, "policy": json.dumps(rbp)})
    assert actions(rbp) == ["bedrock-agentcore:InvokeGateway"] and rbp["Statement"][0]["Resource"] == GATEWAY_ARN
    assert "AWSReservedSSO_ClaudeCode-" in rbp["Statement"][0]["Condition"]["ArnLike"]["aws:PrincipalArn"]
    assert devbox.gateway_host(GATEWAY_URL) == f"{GATEWAY_ID}.gateway.bedrock-agentcore.us-east-1.amazonaws.com"


def test_gateway_role_can_search_but_not_call_models(devbox):
    doc = devbox.iam_roles(ACCOUNT, "us-east-1")["devbox-tools-gateway"]["inline"]["devbox-tools-gateway"]
    assert "bedrock-agentcore:InvokeWebSearch" in actions(doc)
    assert not [a for a in actions(doc) if a.startswith("bedrock:")]


# ----------------------------------------------------------------------------- edge
def test_edge_lambda_requests(devbox, settings):
    env = devbox.lambda_env(devbox.browser_config(settings, ""), "", "")
    req = devbox.lambda_create_request(
        f"arn:aws:iam::{ACCOUNT}:role/devbox-edge-lambda",
        f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/devbox-edge:abc",
        env,
    )
    validate("lambda", "CreateFunction", req)
    assert req["PackageType"] == "Image" and req["Architectures"] == ["arm64"] and req["MemorySize"] == 1024
    assert "VpcConfig" not in req
    url = devbox.function_url_request()
    validate("lambda", "CreateFunctionUrlConfig", url)
    assert url["AuthType"] == "AWS_IAM" and url["InvokeMode"] == "BUFFERED", (
        "edge/src/handler.mjs returns buffered responses only"
    )


def test_oac_is_lambda_always_sigv4(devbox):
    req = devbox.oac_request()
    validate("cloudfront", "CreateOriginAccessControl", req)
    c = req["OriginAccessControlConfig"]
    assert (c["OriginAccessControlOriginType"], c["SigningBehavior"], c["SigningProtocol"]) == (
        "lambda",
        "always",
        "sigv4",
    )


@pytest.mark.parametrize("site", ["workbench", "webview"])
def test_distribution_config(devbox, site):
    cfg = devbox.distribution_config(site, "abcdefghij1234567890.lambda-url.us-east-1.on.aws", "E2QWRUHAPOMQZL")
    validate(
        "cloudfront",
        "CreateDistributionWithTags",
        {"DistributionConfigWithTags": {"DistributionConfig": cfg, "Tags": {"Items": devbox.tag_list("x")}}},
    )
    origin = cfg["Origins"]["Items"][0]
    assert "S3OriginConfig" not in origin and origin["DomainName"].endswith(".lambda-url.us-east-1.on.aws")
    assert origin["OriginAccessControlId"] == "E2QWRUHAPOMQZL"
    assert origin["CustomHeaders"] == {"Quantity": 1, "Items": [{"HeaderName": "x-devbox-site", "HeaderValue": site}]}
    assert origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    default, (static,) = cfg["DefaultCacheBehavior"], cfg["CacheBehaviors"]["Items"]
    assert static["PathPattern"] == "/stable-*/static/*"
    assert static["CachePolicyId"] == "658327ea-f89d-4fab-a63d-7e88639e58f6" and static["Compress"] is True
    assert "OriginRequestPolicyId" not in static
    assert default["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    assert default["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    for b in (default, static):
        assert b["AllowedMethods"]["Items"] == ["GET", "HEAD"]
        assert (b.get("ResponseHeadersPolicyId") == "67f7725c-6f97-4210-82d7-5512b31e9d03") == (site == "workbench")
    assert cfg["ViewerCertificate"] == {"CloudFrontDefaultCertificate": True}
    assert cfg["Logging"]["Enabled"] is False
    assert devbox.distribution_drift(json.loads(json.dumps(cfg)), cfg) == []


def test_lambda_permissions_for_each_distribution(devbox):
    arn = f"arn:aws:cloudfront::{ACCOUNT}:distribution/E2QWRUHAPOMQZL"
    reqs = devbox.lambda_permission_requests("workbench", arn)
    assert sorted(r["Action"] for r in reqs) == ["lambda:InvokeFunction", "lambda:InvokeFunctionUrl"]
    for r in reqs:
        validate("lambda", "AddPermission", r)
        assert r["Principal"] == "cloudfront.amazonaws.com" and r["SourceArn"] == arn


def test_the_browser_config_names_nobody(devbox, settings):
    """The same config for everyone; the page asks POST /api/box for the signed-in person's box."""
    cfg = devbox.browser_config(settings, "dwebview123.cloudfront.net")
    assert cfg == {
        "region": "us-east-1",
        "commit": "072586267e68ece9a47aa43f8c108e0dcbf44622",
        "serverRoot": "/stable-072586267e68ece9a47aa43f8c108e0dcbf44622",
        "agentcoreBase": "https://bedrock-agentcore.us-east-1.amazonaws.com",
        "okta": {
            "issuer": "https://example.okta.com/oauth2/default",
            "clientId": "0oaDEVBOXSPA1234567",
            "scopes": "openid profile email offline_access devbox",
        },
        "webviewOrigin": "https://dwebview123.cloudfront.net",
        "provision": {"path": "/api/box", "header": "X-Devbox-Token"},
    }
    env = devbox.lambda_env(cfg, "dwb.cloudfront.net", "dwebview123.cloudfront.net")
    assert set(env) == {"DEVBOX_CONFIG_JSON", "WORKBENCH_ORIGIN", "WEBVIEW_ORIGIN"}
    assert json.loads(env["DEVBOX_CONFIG_JSON"]) == cfg and env["WORKBENCH_ORIGIN"] == "https://dwb.cloudfront.net"
    assert sum(len(k) + len(v) for k, v in env.items()) <= 4096, "and it stays small however many people have a box"


# ----------------------------------------------------------------------------- group-driven boxes
def test_a_new_persons_box_name(devbox):
    a = devbox.box_name_for("grace.hopper@example.com", GRACE_UID)
    assert a == "gracehopper" + devbox.uid_key(GRACE_UID)[:6] and devbox.BOX_NAME.match(a)
    assert devbox.box_name_for("grace.hopper@example.com", UID) != a, (
        "same name, another person: another box and folder"
    )
    assert devbox.box_name_for("grace.hopper@example.com", GRACE_UID) == a, "the same person always gets the same name"
    for login in ("", "1234@example.com", "Ünïcødé-Ñame@x", "x" * 200):
        n = devbox.box_name_for(login, UID)
        assert devbox.BOX_NAME.match(n) and len(f"devbox_vm_{n}") <= 48 and len(f"devbox-exec-{n}") <= 64, login


def test_exactly_one_tier_group(devbox, settings):
    assert devbox.tier_for(settings, ["devbox-users", "ai-claude-power"]) == ("Power", "")
    assert devbox.tier_for(settings, ["devbox-users", "ai-claude-standard", "other"]) == ("Standard", "")
    none, why = devbox.tier_for(settings, ["devbox-users"])
    assert none is None and "none of the tier groups" in why
    both, why = devbox.tier_for(settings, ["devbox-users", "ai-claude-power", "ai-claude-standard"])
    assert both is None and "more than one tier group" in why, "nobody guesses which"


def test_the_provisioners_settings_round_trip(devbox, settings):
    s2 = devbox.load_settings(devbox.settings_env(settings))
    assert not s2.errors
    for f in (
        "region",
        "az",
        "compute",
        "idle_seconds",
        "tier_groups",
        "okta_domain",
        "okta_auth_server",
        "okta_audience",
        "okta_group",
        "okta_client_id",
        "idc_region",
        "geo",
        "models",
    ):
        assert getattr(s2, f) == getattr(settings, f), f
    plan = devbox.BoxPlan(
        ACCOUNT,
        FS_ID,
        SUBNET,
        BOX_SG,
        IMAGE,
        GATEWAY_URL,
        "https://d-1.awsapps.com/start",
        f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary",
    )
    env = devbox.provisioner_env(settings, plan)
    assert (
        devbox.BoxPlan(**json.loads(env["DEVBOX_PLAN"])) == plan
        and sum(len(k) + len(v) for k, v in env.items()) <= 4096
    )
    assert "PROFILE" not in "".join(
        v for k, v in json.loads(env["DEVBOX_SETTINGS"]).items() if k.endswith("PROFILE") and v != "-"
    )


def test_box_records_round_trip(devbox):
    rec = {
        "key": devbox.uid_key(UID),
        "uid": UID,
        "name": "ada",
        "tier": "Power",
        "generation": 3,
        "login": "",
        "x": None,
    }
    item = devbox.ddb_item(rec)
    assert item["generation"] == {"N": "3"} and "login" not in item and "x" not in item
    assert devbox.ddb_rec(item) == {k: v for k, v in rec.items() if v not in (None, "")}
    validate("dynamodb", "CreateTable", devbox.box_table_request())
    assert (
        devbox.box_table_request()["DeletionProtectionEnabled"] is True
        and devbox.box_table_request()["BillingMode"] == "PAY_PER_REQUEST"
    )


def test_the_provisioner_lambda_and_api(devbox, settings):
    env = {"DEVBOX_SETTINGS": "{}", "DEVBOX_PLAN": "{}"}
    req = devbox.provisioner_create_request(f"arn:aws:iam::{ACCOUNT}:role/devbox-provisioner", b"PK", env)
    validate("lambda", "CreateFunction", req)
    assert (req["Runtime"], req["Handler"], req["Architectures"]) == ("python3.13", "provisioner.handler", ["arm64"])
    assert "VpcConfig" not in req
    auth = devbox.api_authorizer_request("a1b2c3", settings)
    validate("apigatewayv2", "CreateAuthorizer", auth)
    assert auth["IdentitySource"] == ["$request.header.X-Devbox-Token"], "a custom header: CloudFront passes it as is"
    assert auth["JwtConfiguration"] == {"Issuer": settings.okta_issuer, "Audience": ["api://default"]}
    validate(
        "apigatewayv2",
        "CreateIntegration",
        devbox.api_integration_request("a1b2c3", "arn:aws:lambda:us-east-1:1:function:f"),
    )
    route = devbox.api_route_request("a1b2c3", "au1", "in1")
    validate("apigatewayv2", "CreateRoute", route)
    assert (route["RouteKey"], route["AuthorizationType"], route["AuthorizationScopes"]) == (
        "POST /api/box",
        "JWT",
        ["devbox"],
    )
    perm = devbox.api_permission_request(ACCOUNT, "us-east-1", "a1b2c3")
    validate("lambda", "AddPermission", perm)
    assert perm["SourceArn"].endswith("a1b2c3/*/POST/api/box") and perm["Principal"] == "apigateway.amazonaws.com"


def test_the_provisioner_may_make_only_bounded_execution_roles(devbox):
    boundary = f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary"
    (doc,) = devbox.provisioner_spec(ACCOUNT, "us-east-1", FS_ID, boundary, subnet_id=SUBNET, security_group_id=BOX_SG)[
        "inline"
    ].values()
    validate(
        "iam", "PutRolePolicy", {"RoleName": "devbox-provisioner", "PolicyName": "p", "PolicyDocument": json.dumps(doc)}
    )
    by = {st["Sid"]: st for st in doc["Statement"]}
    make = by["ExecRolesOnlyWithTheBoundary"]
    assert set(make["Action"]) == {"iam:CreateRole", "iam:PutRolePolicy"}
    assert make["Resource"] == f"arn:aws:iam::{ACCOUNT}:role/devbox-exec-*"
    assert make["Condition"] == {"StringEquals": {"iam:PermissionsBoundary": boundary}}
    assert by["PassExecRolesToAgentCoreOnly"]["Condition"] == {
        "StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}
    }
    deny = by["NeverLiftOrWidenTheBoundary"]
    assert deny["Effect"] == "Deny" and {
        "iam:DeleteRolePermissionsBoundary",
        "iam:PutRolePermissionsBoundary",
        "iam:AttachRolePolicy",
        "iam:CreatePolicyVersion",
    } <= set(deny["Action"])
    iam_allowed = {
        a
        for st in doc["Statement"]
        if st["Effect"] == "Allow"
        for a in actions({"Statement": [st]})
        if a.startswith("iam:")
    }
    assert iam_allowed == {"iam:CreateRole", "iam:PutRolePolicy", "iam:GetRole", "iam:TagRole", "iam:PassRole"}
    for st in doc["Statement"]:
        if st["Effect"] == "Allow" and any(a.startswith("iam:") for a in actions({"Statement": [st]})):
            res = [st["Resource"]] if isinstance(st["Resource"], str) else st["Resource"]
            assert all(r.endswith(":role/devbox-exec-*") for r in res), st["Sid"]
    assert not [a for a in actions(doc) if a.startswith("bedrock:")], "no model access"
    assert by["TheBoxRecords"]["Resource"].endswith(":table/devbox-boxes")
    # CreateAgentRuntime has no resource type: an ARN in Resource never matches (AccessDenied on every call, seen live).
    # So "*", narrowed by its condition keys: our tag, the box subnet and security group.
    make_rt = by["MakeRuntimesInTheBoxSubnetOnly"]
    assert (make_rt["Action"], make_rt["Resource"]) == ("bedrock-agentcore:CreateAgentRuntime", "*")
    assert make_rt["Condition"] == {
        "StringEquals": {"aws:RequestTag/devbox": "remote-dev-box"},
        "ForAllValues:StringEquals": {
            "bedrock-agentcore:subnets": [SUBNET],
            "bedrock-agentcore:securityGroups": [BOX_SG],
        },
    }
    assert [
        st["Sid"] for st in doc["Statement"] if "bedrock-agentcore:CreateAgentRuntime" in actions({"Statement": [st]})
    ] == ["MakeRuntimesInTheBoxSubnetOnly"]
    # ...and it makes the DEFAULT endpoint as the caller, checked on the literal "runtime/*" (seen live).
    endpoint = by["TheDefaultEndpointOfANewRuntime"]
    assert (endpoint["Action"], endpoint["Resource"]) == (
        "bedrock-agentcore:CreateAgentRuntimeEndpoint",
        f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/*",
    )
    # ...and copies the runtime's tags onto it and onto the workload identity, as the caller (seen live): only our tags.
    tag = by["OurTagsOnANewRuntimeItsEndpointAndWorkloadIdentity"]
    assert tag["Action"] == "bedrock-agentcore:TagResource" and tag["Resource"] == [
        f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/*",
        f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:workload-identity-directory/default",
        f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:workload-identity-directory/default/workload-identity/*",
    ]
    assert tag["Condition"] == {
        "StringEquals": {"aws:RequestTag/devbox": "remote-dev-box"},
        "ForAllValues:StringEquals": {"aws:TagKeys": ["devbox", "devbox-user"]},
    }
    assert [
        st["Sid"] for st in doc["Statement"] if "bedrock-agentcore:TagResource" in actions({"Statement": [st]})
    ] == ["OurTagsOnANewRuntimeItsEndpointAndWorkloadIdentity"]


def test_the_runtime_request_meets_the_provisioners_conditions(devbox, runtime_req):
    """What advance_box sends is what MakeRuntimesInTheBoxSubnetOnly allows: our tag, the box subnet and group only."""
    (doc,) = devbox.provisioner_spec(
        ACCOUNT,
        "us-east-1",
        FS_ID,
        f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary",
        subnet_id=SUBNET,
        security_group_id=BOX_SG,
    )["inline"].values()
    cond = next(st for st in doc["Statement"] if st["Sid"] == "MakeRuntimesInTheBoxSubnetOnly")["Condition"]
    assert all(runtime_req["tags"].get(k.split("/", 1)[1]) == v for k, v in cond["StringEquals"].items())
    tag = next(st for st in doc["Statement"] if st["Sid"] == "OurTagsOnANewRuntimeItsEndpointAndWorkloadIdentity")[
        "Condition"
    ]
    assert set(runtime_req["tags"]) <= set(tag["ForAllValues:StringEquals"]["aws:TagKeys"]), (
        "every tag we send is allowed"
    )
    net = runtime_req["networkConfiguration"]["networkModeConfig"]
    assert set(net["subnets"]) <= set(cond["ForAllValues:StringEquals"]["bedrock-agentcore:subnets"])
    assert set(net["securityGroups"]) <= set(cond["ForAllValues:StringEquals"]["bedrock-agentcore:securityGroups"])


def test_the_boundary_is_at_most_what_a_box_needs(devbox):
    doc = devbox.exec_boundary(ACCOUNT, "us-east-1", FS_ID)
    validate("iam", "CreatePolicy", {"PolicyName": "devbox-exec-boundary", "PolicyDocument": json.dumps(doc)})
    acts = set(actions(doc))
    assert not [a for a in acts if a.startswith(("bedrock", "iam:", "sts:", "s3:"))], acts
    assert {
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
    } <= acts and "elasticfilesystem:ClientRootAccess" not in acts
    mount = next(st for st in doc["Statement"] if "elasticfilesystem:ClientMount" in st["Action"])
    assert mount["Resource"] == FS_ARN, "only the box file system"
    (exec_doc,) = devbox.exec_role_spec(
        ACCOUNT, "us-east-1", person(devbox, "ada"), file_system_arn=FS_ARN, access_point_arn=AP_ARN
    )["inline"].values()
    assert set(actions(exec_doc)) <= acts, "every execution role fits inside the boundary"


def test_the_file_system_policy_names_nobody(devbox):
    doc = devbox.file_system_policy_static(FS_ARN)
    validate("efs", "PutFileSystemPolicy", {"FileSystemId": FS_ID, "Policy": json.dumps(doc)})
    assert {st["Effect"] for st in doc["Statement"]} == {"Deny"}, (
        "no Allow: each role's own policy grants its own folder"
    )
    tls = next(st for st in doc["Statement"] if st["Sid"] == "TlsOnly")
    assert tls["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
    assert any(
        st["Action"] == "elasticfilesystem:ClientRootAccess" and "Condition" not in st for st in doc["Statement"]
    ), "no root"


def test_the_workbench_sends_api_to_the_provisioner(devbox):
    cfg = devbox.distribution_config(
        "workbench",
        "abc.lambda-url.us-east-1.on.aws",
        "E2QWRUHAPOMQZL",
        api_domain="a1b2c3d4e5.execute-api.us-east-1.amazonaws.com",
    )
    validate(
        "cloudfront",
        "CreateDistributionWithTags",
        {"DistributionConfigWithTags": {"DistributionConfig": cfg, "Tags": {"Items": devbox.tag_list("x")}}},
    )
    _edge, api = cfg["Origins"]["Items"]
    assert api["Id"] == "devbox-api" and api["DomainName"].endswith(".execute-api.us-east-1.amazonaws.com")
    assert api["OriginAccessControlId"] == "" and api["CustomHeaders"] == {"Quantity": 0}
    _static, path = cfg["CacheBehaviors"]["Items"]
    assert (path["PathPattern"], path["TargetOriginId"]) == ("/api/*", "devbox-api")
    assert "POST" in path["AllowedMethods"]["Items"] and path["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    assert path["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac", (
        "every viewer header but Host: the token"
    )
    assert devbox.distribution_drift(json.loads(json.dumps(cfg)), cfg) == []
    before = devbox.distribution_config("workbench", "abc.lambda-url.us-east-1.on.aws", "E2QWRUHAPOMQZL")
    drift = devbox.distribution_drift(json.loads(json.dumps(before)), cfg)
    assert "origin devbox-api" in drift and any("behavior" in d for d in drift), drift
    assert (
        len(
            devbox.distribution_config("webview", "abc.lambda-url.us-east-1.on.aws", "E", api_domain="x")["Origins"][
                "Items"
            ]
        )
        == 1
    )


def test_ecr_and_docker_auth(devbox):
    validate("ecr", "CreateRepository", devbox.ecr_repository_request("devbox-box"))
    assert devbox.ecr_repository_request("devbox-box")["imageScanningConfiguration"] == {"scanOnPush": True}
    assert devbox.docker_auth_config("1.dkr.ecr.us-east-1.amazonaws.com", "QVdTOng=") == {
        "auths": {"1.dkr.ecr.us-east-1.amazonaws.com": {"auth": "QVdTOng="}}
    }


def test_context_hash(devbox, tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("1")
    (tmp_path / ".dockerignore").write_text("build/cache\nnode_modules\n")
    a = devbox.context_hash(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{20}", a)
    (tmp_path / "node_modules" / "x.js").write_text("2")
    (tmp_path / "build" / "cache").mkdir(parents=True)
    (tmp_path / "build" / "cache" / "big.tgz").write_text("x")
    assert devbox.context_hash(tmp_path) == a, ".dockerignore'd paths don't change the tag"
    (tmp_path / "Dockerfile").write_text("FROM scratch\nLABEL x=1\n")
    assert devbox.context_hash(tmp_path) != a


def test_context_hash_with_an_allowlist_dockerignore(devbox, tmp_path):
    """The box's style: exclude everything, then re-include what the Dockerfile copies."""
    (tmp_path / ".dockerignore").write_text("*\n!Dockerfile\n!rootfs\n!proxy/server.mjs\n**/__pycache__\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    for f in (
        "rootfs/etc/x.json",
        "rootfs/opt/lib/__pycache__/m.pyc",
        "proxy/server.mjs",
        "proxy/node_modules/ws/a.js",
        "test/t.mjs",
        "README.md",
    ):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("1")
    a = devbox.context_hash(tmp_path)
    for f in ("rootfs/etc/x.json", "proxy/server.mjs"):  # sent to Docker: a change is a new tag
        (tmp_path / f).write_text("2")
        b = devbox.context_hash(tmp_path)
        assert b != a, f
        a = b
    for f in ("rootfs/opt/lib/__pycache__/m.pyc", "proxy/node_modules/ws/a.js", "test/t.mjs", "README.md"):  # not sent
        (tmp_path / f).write_text("3")
        assert devbox.context_hash(tmp_path) == a, f
    rules = devbox.dockerignore_rules(tmp_path)
    assert devbox.docker_excludes("test/t.mjs", rules) and not devbox.docker_excludes("rootfs/etc/x.json", rules)


def test_the_real_box_context_is_hashed_by_its_dockerignore(devbox):
    box = devbox.REMOTE / "box"
    if not (box / ".dockerignore").exists():
        pytest.skip("box/ isn't built yet")
    rules = devbox.dockerignore_rules(box)
    assert not devbox.docker_excludes("rootfs", rules) and not devbox.docker_excludes("proxy/server.mjs", rules)
    assert devbox.docker_excludes("test/proxy.test.mjs", rules)


# ----------------------------------------------------------------------------- invariants across the file
def test_no_s3_bucket_anywhere(devbox):
    """S3 appears only as the gateway endpoint (and its policy): no bucket, no S3 client, no S3 origin."""
    src = (devbox.HERE / "devbox.py").read_text()
    assert (
        'client("s3")' not in src
        and "S3OriginConfig" not in src
        and "create_bucket" not in src
        and "put_bucket" not in src
    )


def test_deploy_never_makes_or_changes_a_capacity_provider(devbox):
    src = (devbox.HERE / "devbox.py").read_text()
    assert "create_capacity_provider" not in src and "update_capacity_provider" not in src
    assert 'capacityProviderConfiguration": ' not in src.split("def runtime_request", 1)[1].split("\ndef ", 1)[0]


def test_guides(devbox, settings):
    text = devbox.okta_steps(settings, "d1234abcd.cloudfront.net")
    for bit in (
        "https://d1234abcd.cloudfront.net/callback",
        "https://d1234abcd.cloudfront.net/",
        "never a wildcard",
        "openid profile email offline_access devbox",
        "app.clientId",
        "Matches regex ^(devbox\\-users|ai\\-claude\\-power|ai\\-claude\\-standard)$",
        "DPoP",
        "devbox-users",
        "exactly one tier group",
        "Setting up your dev box",
        "priority 1",
        '"Any scopes"',
        "Token Preview",
        "must be denied",
    ):
        assert bit in text
    spike = devbox.spike_checklist(settings, {"ada": {"runtimeArn": RUNTIME_ARN}}, "d1234abcd.cloudfront.net", IMAGE)
    for bit in (
        "op: diag",
        "/commands",
        "stopruntimesession",
        "network allowlist",
        "2 GB",
        "/mnt/workspace",
        "MMDSv2",
        "__devbox.getToken()",
        "__devbox.sessionId",
        "Idle stop",
        "60-minute",
        "Cold start",
        "marker file",
        "DNS Firewall",
        "Port 80",
        "deploy/README.md › Spike checklist",
        "EFS mount works",
        "424",
        "devbox-exec-<name>",
        "TCP 2049",
        ".efs.us-east-1.amazonaws.com",  # microVM + EFS
        "Terminal opens",
        "shell-probe.py",
        "/ws works on microVM",
        "ws-probe.sh",
        "multi-user",
        "single-user",
        "Sign-out",
        "__devbox.signOut()",
        "/v1/logout",
        "https://d1234abcd.cloudfront.net/#signout",
    ):  # The sign-out item
        assert bit in spike, bit
    for gone in (
        "DEVBOX_SG_SELF_INBOUND",
        "Operator role trust",
        "capacity provider",
        "describe-instances",
    ):  # Instances only
        assert gone not in spike, gone
    assert f"docker image ls {IMAGE}" in spike, "the full ECR reference: build_and_push tags only that"
    readme = (devbox.HERE / "README.md").read_text()
    numbered = re.findall(r"^ ?(\d+)\. ", spike, re.MULTILINE)
    assert numbered == [str(i) for i in range(1, len(numbered) + 1)]
    section = readme.split("## Spike checklist", 1)[1].split("\n## ", 1)[0]
    assert re.findall(r"^(\d+)\. ", section, re.MULTILINE) == numbered, (
        "deploy/README.md › Spike checklist has exactly the items deploy prints"
    )
    for title in ("EFS mount works", "Terminal opens", "/ws works on microVM"):
        assert title in section
    top = (devbox.REMOTE / "OVERVIEW.md").read_text()  # the setup guide mirrors it too
    top_items = (
        re.findall(r"^(\d+)\. ", top.split("## Spike checklist", 1)[1].split("\n## ", 1)[0], re.MULTILINE)
        if "## Spike checklist" in top
        else []
    )
    assert top_items == numbered, "OVERVIEW.md › Spike checklist has exactly the items deploy prints"
    note = devbox.okta_domain_change("dold.cloudfront.net", "dnew.cloudfront.net")
    for bit in (
        "https://dold.cloudfront.net/callback",
        "https://dnew.cloudfront.net/callback",
        "Trusted Origin",
        "https://dnew.cloudfront.net/",
    ):
        assert bit in note
    assert "arn%3Aaws%3Abedrock-agentcore" in spike


def test_the_readme_documents_every_subcommand(devbox):
    readme = (devbox.HERE / "README.md").read_text()
    for cmd in (
        "check",
        "deploy",
        "network allowlist",
        "network pause|resume",
        "status",
        "reset-box <user>",
        "retire-instances",
        "undeploy [--delete-volumes]",
    ):
        assert f"devbox.py {cmd}" in readme, cmd
    assert "DEVBOX_COMPUTE" in readme and "devbox_vm_<name>" in readme and "/devbox/<name>" in readme


def test_every_paged_call_names_real_fields(devbox):
    """paged(call, key, token, send=…): the response must have `key` and `token`, and the request must take `send`
    (or `token`), or the second page would be a parameter error nobody sees until an account has that many."""
    services = {
        "acc": "bedrock-agentcore-control",
        "ec2": "ec2",
        "efs": "efs",
        "r53r": "route53resolver",
        "r53": "route53resolver",
        "iam": "iam",
        "logs": "logs",
        "sso": "sso-admin",
        "api": "apigatewayv2",
        "apigw": "apigatewayv2",
    }
    src = (devbox.HERE / "devbox.py").read_text()
    calls = set(re.findall(r'paged\((?:ctx\.aws\.)?(\w+)\.(\w+), "(\w+)"(?:, "(\w+)")?(?:, send="(\w+)")?', src))
    assert len(calls) > 15
    for var, op, key, token, send in calls:
        m = model(services[var])
        name = next(o for o in m.operation_names if re.sub(r"(?<!^)(?=[A-Z])", "_", o).lower() == op)
        opm = m.operation_model(name)
        token = token or "nextToken"
        assert key in opm.output_shape.members, (op, key)
        assert token in opm.output_shape.members, (op, token)
        assert (send or token) in opm.input_shape.members, (op, send or token)
    got = []
    pages = iter([{"MountTargets": [1], "NextMarker": "m2"}, {"MountTargets": [2]}])
    assert devbox.paged(
        lambda **kw: got.append(kw) or next(pages), "MountTargets", "NextMarker", send="Marker", FileSystemId="fs-1"
    ) == [1, 2]
    assert got == [{"FileSystemId": "fs-1"}, {"FileSystemId": "fs-1", "Marker": "m2"}]


def test_the_validator_is_strict():
    """The checks above only mean something if the validator rejects what botocore alone lets through."""
    good = {
        "agentRuntimeName": "devbox_vm_ada",
        "roleArn": EXEC_ARN,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": IMAGE}},
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {"subnets": [SUBNET], "securityGroups": [BOX_SG]},
        },
        "filesystemConfigurations": [{"efsAccessPoint": {"accessPointArn": AP_ARN, "mountPath": "/mnt/workspace"}}],
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 3600, "maxLifetime": 28800},
    }
    validate("bedrock-agentcore-control", "CreateAgentRuntime", good)
    for bad in (
        {"agentRuntimeName": "devbox-vm-ada"},  # pattern
        {"agentRuntimeName": "d" * 49},  # pattern length
        {"lifecycleConfiguration": {"idleRuntimeSessionTimeout": 1209601}},  # max value
        {"networkConfiguration": {"networkMode": "PRIVATE"}},  # enum
        {
            "filesystemConfigurations": [{"efsAccessPoint": {"accessPointArn": AP_ARN, "mountPath": "/mnt/a/b"}}]
        },  # mount path
        {"filesystemConfigurations": [{"efsAccessPoint": {"accessPointArn": FS_ARN, "mountPath": "/mnt/workspace"}}]},
    ):  # not an AP
        with pytest.raises(AssertionError):
            validate("bedrock-agentcore-control", "CreateAgentRuntime", {**good, **bad})
    with pytest.raises(AssertionError):
        validate("efs", "CreateAccessPoint", {"ClientToken": "t" * 65, "FileSystemId": FS_ID})  # max length


def test_prebuild_runs_in_the_context_and_reports_failures(devbox, tmp_path):
    devbox.prebuild(tmp_path, ["sh", "-c", "echo built > out.txt"])
    assert (tmp_path / "out.txt").read_text() == "built\n"
    with pytest.raises(RuntimeError, match="no node here"):
        devbox.prebuild(tmp_path, ["sh", "-c", "echo 'no node here' >&2; exit 3"])


def test_the_provisioner_bundle_imports_on_its_own(devbox, tmp_path):
    """The Lambda zip: devbox.py, provisioner.py, the templates and a trimmed boto3. Unpacked alone (no site-packages),
    the handler must import, load its settings, and turn away a request with no verified token."""
    import io
    import subprocess
    import sys
    import zipfile

    z = devbox.provisioner_zip()
    assert z == devbox.provisioner_zip(), "deterministic: deploy updates the function only when something changed"
    assert len(z) < 5_000_000, f"{len(z)} bytes: is a whole site-packages in it again?"
    names = zipfile.ZipFile(io.BytesIO(z)).namelist()
    assert {
        "devbox.py",
        "provisioner.py",
        "six.py",
        "templates/iam/exec-policy.json",
        "templates/iam/runtime-resource-policy.json",
    } <= set(names)
    assert not [n for n in names if n.startswith("botocore/data/ec2/")] and [
        n for n in names if n.startswith("botocore/data/efs/")
    ]
    zipfile.ZipFile(io.BytesIO(z)).extractall(tmp_path)
    s = devbox.load_settings(
        {**devbox.parse_env_file((devbox.HERE / "devbox.env.example").read_text()), "OKTA_DOMAIN": "example.okta.com"}
    )
    env = {
        "DEVBOX_SETTINGS": json.dumps(devbox.settings_env(s)),
        "DEVBOX_PLAN": json.dumps(
            devbox.BoxPlan(
                ACCOUNT,
                FS_ID,
                SUBNET,
                BOX_SG,
                IMAGE,
                GATEWAY_URL,
                "https://d-1.awsapps.com/start",
                f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary",
            ).__dict__
        ),
        "PATH": "/usr/bin:/bin",
    }
    code = (
        f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import provisioner, boto3; "
        f"assert boto3.__file__.startswith({str(tmp_path)!r}), boto3.__file__; "
        "[boto3.client(x, region_name='us-east-1', aws_access_key_id='a', aws_secret_access_key='b') for x in "
        f"{devbox.PROVISIONER_SERVICES!r}]; print(provisioner.handler({{}}, None)['statusCode'], provisioner.SETTINGS.errors)"
    )
    r = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.split() == ["401", "[]"]
