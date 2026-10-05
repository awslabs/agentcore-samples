"""Whole commands against the in-memory AWS (tests/fake_aws.py): deploy from nothing (microVM boxes on EFS),
re-deploy, check mode, the old Instances boxes (left alone by deploy, deleted by retire-instances),
network pause/resume/allowlist, reset-box, status and both undeploys."""

import json

import fake_aws
import pytest
from botocore.exceptions import WaiterError
from conftest import CLIENT_ID, PEOPLE, jwt_event, make_ctx

MUTATING = (
    "Create",
    "Update",
    "Put",
    "Delete",
    "Attach",
    "Detach",
    "Associate",
    "Disassociate",
    "Authorize",
    "Revoke",
    "Replace",
    "Allocate",
    "Release",
    "Modify",
    "Add",
    "Remove",
    "Tag",
    "Untag",
    "Stop",
)
from conftest import ADA, GRACE

ACCOUNT = fake_aws.ACCOUNT
OPERATOR, INSTANCE_ROLE = "devbox-cp-operator", "AmazonBedrockAgentCoreCapacityProviderDefaultInstanceRole-devbox"


def mutations(log):
    return [(s, op) for s, op, _ in log if op.startswith(MUTATING)]


def first(world, op, **match):
    for i, (_, o, p) in enumerate(world.log):
        if o == op and all(p.get(k) == v for k, v in match.items()):
            return i
    raise AssertionError(f"{op} {match} was never called")


def deploy(devbox, settings, world, check=False):
    devbox.Report.reset()
    ctx = make_ctx(devbox, settings, world, check=check)
    assert devbox.cmd_deploy(ctx) == 0
    return ctx


def fake_env(devbox):
    e = devbox.parse_env_file((devbox.HERE / "devbox.env.example").read_text())  # devbox.env itself is git-ignored
    e["OKTA_DOMAIN"] = "example.okta.com"
    e["DEVBOX_OKTA_CLIENT_ID"] = CLIENT_ID
    return e


def name_of(obj):
    return next((t["Value"] for t in obj.get("Tags", []) if t["Key"] == "Name"), None)


def group_id(world, name):
    return next(g["GroupId"] for g in world.sgs.values() if g["GroupName"] == name)


def rules(world, gid, egress):
    return sorted(
        (r["IpProtocol"], r["FromPort"], r.get("CidrIpv4") or r["ReferencedGroupInfo"]["GroupId"])
        for r in world.sg_rules.values()
        if r["GroupId"] == gid and r["IsEgress"] == egress
    )


def runtimes_by_name(world):
    return {r["agentRuntimeName"]: r for r in world.runtimes.values()}


def seed_instances(devbox, world, generations=(("ada", ADA, 1), ("grace", GRACE, 4))):
    """What the Instances deploy left: per person a capacity provider devbox_<name> and a runtime devbox_<name>
    on it, the operator, instance and shared execution roles, and a .state.json with each uid and generation."""
    arn = lambda kind, rid: f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:{kind}/{rid}"
    role = lambda n: f"arn:aws:iam::{ACCOUNT}:role/{n}"
    for n, managed in (
        (OPERATOR, "BedrockAgentCoreRuntimeInstancesOperatorRolePolicy"),
        (INSTANCE_ROLE, "BedrockAgentCoreRuntimeInstancesInstanceRolePolicy"),
        ("devbox-exec", None),
    ):
        world.roles[n] = {
            "trust": {"Version": "2012-10-17", "Statement": []},
            "managed": [f"arn:aws:iam::aws:policy/{managed}"] if managed else [],
            "inline": {} if managed else {"pull-box-image-and-log": {"Version": "2012-10-17", "Statement": []}},
        }
    world.profiles[INSTANCE_ROLE] = [INSTANCE_ROLE]
    boxes = {}
    for name, uid, gen in generations:
        cid, rid = f"devbox_{name}-{world.suffix()}", f"devbox_{name}-{world.suffix()}"
        world.cps[cid] = {
            "capacityProviderId": cid,
            "capacityProviderArn": arn("capacity-provider", cid),
            "name": f"devbox_{name}",
            "status": "READY",
            "permissionsConfiguration": {"capacityProviderOperatorRoleArn": role(OPERATOR)},
            "computeConfiguration": {
                "ec2Configuration": {
                    "launchTemplateSource": {
                        "launchParameters": {
                            "instanceProfileArn": f"arn:aws:iam::{ACCOUNT}:instance-profile/{INSTANCE_ROLE}"
                        }
                    }
                }
            },
        }
        world.runtimes[rid] = {
            "agentRuntimeId": rid,
            "agentRuntimeArn": arn("runtime", rid),
            "agentRuntimeName": f"devbox_{name}",
            "agentRuntimeVersion": "3",
            "status": "READY",
            "roleArn": role("devbox-exec"),
            "capacityProviderConfiguration": {"capacityProviderArn": arn("capacity-provider", cid)},
            "filesystemConfigurations": [
                {"capacityProviderVolume": {"volumeName": "workspace", "mountPath": "/mnt/workspace"}}
            ],
            "environmentVariables": {"DEVBOX_SESSION_ID": devbox.session_id(uid, gen)},
        }
        boxes[name] = {
            "name": name,
            "uid": uid,
            "generation": gen,
            "cpId": cid,
            "cpArn": arn("capacity-provider", cid),
            "runtimeArn": arn("runtime", rid),
            "runtimeId": rid,
            "sessionId": devbox.session_id(uid, gen),
        }
    devbox.STATE_FILE.write_text(json.dumps({"account": ACCOUNT, "boxes": boxes}))
    return boxes


# ----------------------------------------------------------------------------- deploy
def box_name(devbox, who):
    uid, login, _ = PEOPLE[who]
    return devbox.box_name_for(login, uid)


def test_deploy_from_nothing(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert devbox.Report.problems == 0

    # images: the edge bundle first, then both built once, tagged by content hash
    assert sandbox["prebuilds"] == [("edge", ["build/build.sh"])]
    assert len(sandbox["builds"]) == 2 and all(
        u.startswith(f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/devbox-") for u in sandbox["builds"]
    )

    # EFS: one encrypted file system and its mount target; nobody's folder yet, and a policy that names nobody
    (fs,) = world.efs_fs.values()
    assert (fs["CreationToken"], fs["Encrypted"], fs["ThroughputMode"], fs["PerformanceMode"]) == (
        "devbox",
        True,
        "elastic",
        "generalPurpose",
    )
    box_subnet = next(s for s in world.subnets.values() if name_of(s) == "devbox-box")
    (mt,) = world.efs_mts.values()
    assert mt["SubnetId"] == box_subnet["SubnetId"] and world.efs_mt_sgs[mt["MountTargetId"]] == [
        group_id(world, "devbox-efs")
    ]
    assert world.efs_aps == {} and world.runtimes == {} and world.cps == {}, (
        "no box until its owner opens the workbench"
    )
    policy = json.loads(world.efs_policies[fs["FileSystemId"]])
    assert {s["Effect"] for s in policy["Statement"]} == {"Deny"} and policy == devbox.file_system_policy_static(
        fs["FileSystemArn"]
    )

    # the provisioner: its table, the boundary, its role and Lambda, the HTTP API, and /api/* on the workbench
    table = world.tables["devbox-boxes"]
    assert table["protected"] and table["billing"] == "PAY_PER_REQUEST" and table["items"] == {}
    boundary = f"arn:aws:iam::{ACCOUNT}:policy/devbox-exec-boundary"
    assert boundary in world.managed_policies
    assert set(world.roles) == {"devbox-edge-lambda", "devbox-tools-gateway", "devbox-provisioner"}, (
        "no per-person role yet"
    )
    fn = world.fns["devbox-provisioner"]
    assert (fn["Runtime"], fn["Handler"], fn["Architectures"]) == ("python3.13", "provisioner.handler", ["arm64"])
    assert world.fn_concurrency["devbox-provisioner"] == 5
    plan = devbox.BoxPlan(**json.loads(fn["Environment"]["Variables"]["DEVBOX_PLAN"]))
    (gw,) = world.gateways.values()
    assert plan == devbox.BoxPlan(
        ACCOUNT,
        fs["FileSystemId"],
        box_subnet["SubnetId"],
        group_id(world, "devbox-box"),
        next(u for u in sandbox["builds"] if "/devbox-box:" in u),
        gw["gatewayUrl"],
        "https://d-1234567890.awsapps.com/start",
        boundary,
    )
    assert (
        devbox.load_settings(json.loads(fn["Environment"]["Variables"]["DEVBOX_SETTINGS"])).tier_groups
        == settings.tier_groups
    )
    (api,) = world.apis.values()
    (auth,) = api["_authorizers"].values()
    assert auth["IdentitySource"] == ["$request.header.X-Devbox-Token"]
    assert auth["JwtConfiguration"] == {"Issuer": settings.okta_issuer, "Audience": [settings.okta_audience]}
    (route,) = api["_routes"].values()
    (integ,) = api["_integrations"].values()
    assert (route["RouteKey"], route["AuthorizationType"], route["AuthorizationScopes"]) == (
        "POST /api/box",
        "JWT",
        ["devbox"],
    )
    assert route["Target"] == f"integrations/{integ['IntegrationId']}" and integ["IntegrationUri"] == fn["FunctionArn"]
    assert (
        api["_stages"]["$default"]["AutoDeploy"] is True
        and api["_stages"]["$default"]["DefaultRouteSettings"]["ThrottlingRateLimit"] == 10
    )
    (perm,) = world.fn_policies["devbox-provisioner"]
    assert perm["Principal"] == {"Service": "apigateway.amazonaws.com"} and perm["Condition"]["ArnLike"][
        "AWS:SourceArn"
    ].endswith("/*/POST/api/box")

    # edge: two distributions; only the workbench sends /api/* to the provisioner; the browser config names nobody
    sites = {
        d["config"]["Origins"]["Items"][0]["CustomHeaders"]["Items"][0]["HeaderValue"]: (i, d)
        for i, d in world.dists.items()
    }
    assert set(sites) == {"workbench", "webview"}
    wb_cfg = sites["workbench"][1]["config"]
    assert [o["Id"] for o in wb_cfg["Origins"]["Items"]] == ["devbox-edge", "devbox-api"]
    assert wb_cfg["Origins"]["Items"][1]["DomainName"] == f"{api['ApiId']}.execute-api.us-east-1.amazonaws.com"
    assert [(b["PathPattern"], b["TargetOriginId"]) for b in wb_cfg["CacheBehaviors"]["Items"]] == [
        ("/stable-*/static/*", "devbox-edge"),
        ("/api/*", "devbox-api"),
    ]
    assert len(sites["webview"][1]["config"]["Origins"]["Items"]) == 1
    for did, d in sites.values():
        arn = f"arn:aws:cloudfront::{ACCOUNT}:distribution/{did}"
        got = {(s["Action"], s["Condition"]["ArnLike"]["AWS:SourceArn"]) for s in world.fn_statements}
        assert ("lambda:InvokeFunctionUrl", arn) in got and ("lambda:InvokeFunction", arn) in got
    env = world.fn["Environment"]["Variables"]
    cfg = json.loads(env["DEVBOX_CONFIG_JSON"])
    assert cfg["okta"]["clientId"] == CLIENT_ID and cfg["webviewOrigin"] == f"https://{sites['webview'][1]['domain']}"
    assert cfg["provision"] == {"path": "/api/box", "header": "X-Devbox-Token"} and "boxes" not in cfg
    assert env["WORKBENCH_ORIGIN"] == f"https://{sites['workbench'][1]['domain']}"
    assert (world.fn_url["AuthType"], world.fn_url["InvokeMode"]) == ("AWS_IAM", "BUFFERED")

    # Ada opens the workbench for the first time: the page asks /api/box until her box is ready
    status, body, calls = visit("ada")
    assert status == 200 and calls > 1, (status, body)
    name = box_name(devbox, "ada")
    assert body["box"]["name"] == name and body["box"]["generation"] == 1 and body["box"]["terminal"] is True
    runtimes = runtimes_by_name(world)
    rt = runtimes[f"devbox_vm_{name}"]
    assert body["box"]["runtimeArn"] == rt["agentRuntimeArn"]
    (ap,) = world.efs_aps.values()
    assert ap["RootDirectory"]["Path"] == f"/devbox/{name}" and ap["PosixUser"] == {"Uid": 1000, "Gid": 1000}
    assert ap["RootDirectory"]["CreationInfo"] == {"OwnerUid": 1000, "OwnerGid": 1000, "Permissions": "0750"}
    assert "capacityProviderConfiguration" not in rt
    assert rt["networkConfiguration"] == {
        "networkMode": "VPC",
        "networkModeConfig": {"subnets": [box_subnet["SubnetId"]], "securityGroups": [group_id(world, "devbox-box")]},
    }
    assert rt["filesystemConfigurations"] == [
        {"efsAccessPoint": {"accessPointArn": ap["AccessPointArn"], "mountPath": "/mnt/workspace"}}
    ]
    assert rt["roleArn"] == f"arn:aws:iam::{ACCOUNT}:role/devbox-exec-{name}"
    assert rt["lifecycleConfiguration"] == {"idleRuntimeSessionTimeout": 3600, "maxLifetime": 28800}
    claims = rt["authorizerConfiguration"]["customJWTAuthorizer"]["customClaims"]
    assert {
        "inboundTokenClaimName": "uid",
        "inboundTokenClaimValueType": "STRING",
        "authorizingClaimMatchValue": {"claimMatchValue": {"matchValueString": ADA}, "claimMatchOperator": "EQUALS"},
    } in claims
    assert rt["requestHeaderConfiguration"] == {
        "requestHeaderAllowlist": ["Authorization", "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath"]
    }
    assert rt["environmentVariables"]["DEVBOX_SESSION_ID"] == devbox.session_id(ADA, 1)
    assert rt["environmentVariables"]["DEVBOX_TIER"] == "Power" and set(
        json.loads(rt["environmentVariables"]["DEVBOX_MODELS"])
    ) == {"opus", "sonnet", "haiku"}
    assert rt["metadataConfiguration"] == {"requireMMDSV2": True}, "MMDSv2 set after the create"
    rbp = json.loads(world.rbps[rt["agentRuntimeArn"]])
    allow = next(s for s in rbp["Statement"] if s["Effect"] == "Allow")
    deny = next(s for s in rbp["Statement"] if s["Effect"] == "Deny")
    assert "bedrock-agentcore:InvokeAgentRuntimeCommandShell" in allow["Action"], "the owner's terminal"
    assert sorted(deny["Action"]) == sorted(devbox.RUNTIME_DENIED_ACTIONS) and deny["Principal"] == "*"
    role = world.roles[f"devbox-exec-{name}"]
    assert role["boundary"] == boundary, "every role the provisioner makes carries the boundary"
    doc = role["inline"]["pull-image-log-mount-own-folder"]
    efs = next(s for s in doc["Statement"] if "elasticfilesystem:ClientMount" in s["Action"])
    assert efs["Condition"]["ArnEquals"]["elasticfilesystem:AccessPointArn"] == ap["AccessPointArn"]
    assert not [
        a
        for s in doc["Statement"]
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
        if a.startswith("bedrock")
    ]
    rec = devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    assert (rec["name"], rec["uid"], rec["tier"], rec["generation"], rec["step"]) == (name, ADA, "Power", 1, "ready")
    assert "lockUntil" not in rec, "the lock goes with the last save"

    # the next visit answers at once, and changes nothing
    before = len(world.log)
    assert visit("ada")[0::2] == (200, 1)
    assert [m for m in mutations(world.log[before:]) if m[0] != "dynamodb"] == [], (
        "only its own record: the lock, then the save"
    )

    # Grace is Standard: no Opus; two people never share a folder or a role
    status, body, _ = visit("grace")
    gname = box_name(devbox, "grace")
    assert status == 200 and body["box"]["name"] == gname
    g = runtimes_by_name(world)[f"devbox_vm_{gname}"]
    assert set(json.loads(g["environmentVariables"]["DEVBOX_MODELS"])) == {"sonnet", "haiku"}
    assert g["environmentVariables"]["DEVBOX_SSO_ROLE"] == "ClaudeCode-Standard"
    assert len({a["AccessPointArn"] for a in world.efs_aps.values()}) == 2 and g["roleArn"] != rt["roleArn"]

    # who gets no box, and is told why
    assert visit(("00uNOBODY00000000000", "nobody@example.com", ["ai-claude-power"]))[:2] == (
        403,
        {"message": "you're not in the devbox-users group: ask an admin to add you"},
    )
    status, body, _ = visit(
        ("00uBOTH000000000000B", "both@example.com", ["devbox-users", "ai-claude-power", "ai-claude-standard"])
    )
    assert status == 403 and "more than one tier group" in body["message"]
    status, body, _ = visit(("00uNOTIER0000000000N", "notier@example.com", ["devbox-users"]))
    assert status == 403 and "none of the tier groups" in body["message"] and "groups claim" in body["message"]
    status, body, _ = visit(
        "ada",
        event=jwt_event(
            ADA, "ada.lovelace@example.com", ["devbox-users", "ai-claude-power"], client_id="0oaOTHERAPP0000000000"
        ),
    )
    assert status == 403 and "isn't for the Dev Box app" in body["message"]
    assert len(world.runtimes) == 2, "a refused visit makes nothing"

    # order: what a step needs exists first
    assert first(world, "PutFileSystemPolicy") < first(world, "CreateMountTarget"), (
        "no NFS client reaches it before its policy"
    )
    assert first(world, "CreateFileSystem") < first(world, "CreateRuleGroup", RuleGroupName="devbox-allowlist"), (
        "the allowlist names it"
    )
    assert first(world, "CreateGateway") < first(world, "CreateRuleGroup", RuleGroupName="devbox-allowlist"), (
        "the allowlist names the gateway"
    )
    assert (
        first(world, "CreatePolicy")
        < first(world, "CreateRole", RoleName="devbox-provisioner")
        < first(world, "CreateFunction", FunctionName="devbox-provisioner")
    )
    assert first(world, "CreateApi") < first(world, "CreateDistributionWithTags"), "the workbench sends /api/* to it"
    assert first(world, "CreateVpcEndpoint") < first(world, "CreateAgentRuntime"), (
        "a new microVM pulls its image through the VPC"
    )

    # state and the printed next steps
    state = json.loads(devbox.STATE_FILE.read_text())
    assert state.get("boxes", {}) == {} and "instances" not in state
    assert state["efs"] == {
        "fileSystemId": fs["FileSystemId"],
        "fileSystemArn": fs["FileSystemArn"],
        "mountTargetId": mt["MountTargetId"],
    }
    wb = sites["workbench"][1]["domain"]
    assert f"https://{wb}/callback" in out and "Spike checklist" in out and "EFS mount works" in out
    assert (
        "use1-az1: AgentCore microVM VPC mode supports it" in out
        and "group ai-claude-power (the Power tier) is in Identity Center" in out
    )
    assert "Matches regex ^(devbox\\-users|ai\\-claude\\-power|ai\\-claude\\-standard)$" in out
    assert out.count("until the claim carries it, nobody can open a box") == 2, (
        "at step 6, and again before the Okta steps"
    )


def test_deploy_brings_existing_boxes_up_to_date(devbox, settings, world, sandbox, visit, capsys):
    """A box made by the provisioner gets a new image (or a tier change) from deploy, and is mirrored in .state.json."""
    deploy(devbox, settings, world)
    visit("ada")
    deploy(devbox, settings, world)
    name = box_name(devbox, "ada")
    state = json.loads(devbox.STATE_FILE.read_text())
    assert state["boxes"][name]["uid"] == ADA and state["boxes"][name]["tier"] == "Power"
    assert f"{name} (Power)" in capsys.readouterr().out
    rec = devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    rec["tier"] = "Standard"  # Ada moved to the Standard group, and visited once (the provisioner saves the tier)
    world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)] = devbox.ddb_item(rec)
    deploy(devbox, settings, world)
    rt = runtimes_by_name(world)[f"devbox_vm_{name}"]
    assert rt["environmentVariables"]["DEVBOX_SSO_ROLE"] == "ClaudeCode-Standard"
    assert set(json.loads(rt["environmentVariables"]["DEVBOX_MODELS"])) == {"sonnet", "haiku"}


def test_second_deploy_changes_nothing(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    n = len(world.log)
    deploy(devbox, settings, world)
    assert devbox.Report.changes == 0, capsys.readouterr().out
    assert mutations(world.log[n:]) == []
    assert len(sandbox["builds"]) == 2, "unchanged build contexts aren't rebuilt"


def test_check_mode_changes_nothing(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world, check=True)
    out = capsys.readouterr().out.replace("  ", " ")
    assert mutations(world.log) == [] and sandbox["builds"] == [] and sandbox["prebuilds"] == []
    assert devbox.Report.changes > 20
    for bit in (
        "would create EFS file system devbox",
        "would create table devbox-boxes",
        "devbox-provisioner waits for",
        "would create security groups devbox-box and devbox-efs",
        "would create the S3 gateway endpoint devbox-s3",
    ):
        assert bit in out, bit
    assert not devbox.STATE_FILE.exists()


def test_check_after_deploy_is_clean(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    n = len(world.log)
    deploy(devbox, settings, world, check=True)
    assert devbox.Report.changes == 0 and mutations(world.log[n:]) == []
    assert "everything is already deployed" in capsys.readouterr().out


def test_deploy_leaves_the_old_instances_boxes_alone(devbox, settings, world, sandbox, capsys):
    old = seed_instances(devbox, world)
    old_runtimes = {r["agentRuntimeId"]: json.dumps(r, sort_keys=True) for r in world.runtimes.values()}
    old_cps = set(world.cps)
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    for _, op, p in world.log:
        assert not op.endswith(("CapacityProvider", "CapacityProviderSession")) or not op.startswith(MUTATING), op
        assert p.get("agentRuntimeId") not in old_runtimes or not op.startswith(MUTATING), op
    assert (
        set(world.cps) == old_cps
        and {k: json.dumps(world.runtimes[k], sort_keys=True) for k in old_runtimes} == old_runtimes
    )
    assert {OPERATOR, INSTANCE_ROLE, "devbox-exec"} <= set(world.roles), "their roles too"
    assert (
        "The old Instances boxes are still there (2 capacity provider(s) and 2 runtime(s): devbox_ada, devbox_grace)"
        in out
    )
    assert out.count("`uv run deploy/devbox.py retire-instances` deletes them") == 2, (
        "at step 7, and again after the summary"
    )
    assert not [r for r in world.runtimes.values() if r["agentRuntimeName"].startswith("devbox_vm_")], (
        "a new box comes with a visit"
    )
    assert world.tables["devbox-boxes"]["items"] == {}, "an Instances box isn't a microVM box: nothing to record"
    state = json.loads(devbox.STATE_FILE.read_text())
    assert state["instances"] == old, "kept for retire-instances: uid, generation, capacity provider"
    # and a second deploy still leaves them, and changes nothing
    n = len(world.log)
    deploy(devbox, settings, world)
    assert devbox.Report.changes == 0 and mutations(world.log[n:]) == []


def test_an_instances_runtime_under_the_microvm_name_is_never_updated(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    visit("ada")
    rt = runtimes_by_name(world)[f"devbox_vm_{box_name(devbox, 'ada')}"]
    rt["capacityProviderConfiguration"] = {
        "capacityProviderArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:capacity-provider/x"
    }
    devbox.Report.reset()
    n = len(world.log)
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world)) == 1
    assert "is an Instances runtime" in capsys.readouterr().out
    assert "UpdateAgentRuntime" not in [c[1] for c in world.log[n:]]


def test_the_provisioner_waits_for_the_okta_client_id(devbox, world, sandbox, visit, capsys):
    s = devbox.load_settings({**fake_env(devbox), "DEVBOX_OKTA_CLIENT_ID": ""})
    deploy(devbox, s, world)
    out = capsys.readouterr().out
    assert len(world.efs_mts) == 1 and world.fns == {} and world.runtimes == {} and world.apis == {}
    assert "waits for DEVBOX_OKTA_CLIENT_ID" in out and "copy its Client ID into devbox.env" in out
    deploy(devbox, devbox.load_settings(fake_env(devbox)), world)
    assert "devbox-provisioner" in world.fns and visit("ada")[0] == 200
    (wb,) = [
        d
        for d in world.dists.values()
        if d["config"]["Origins"]["Items"][0]["CustomHeaders"]["Items"][0]["HeaderValue"] == "workbench"
    ]
    assert [o["Id"] for o in wb["config"]["Origins"]["Items"]] == ["devbox-edge", "devbox-api"], (
        "added to the distribution later"
    )


def test_a_tier_group_missing_from_identity_center_is_only_a_warning(devbox, world, sandbox, capsys):
    world.idc_groups.discard("ai-claude-standard")
    s = devbox.load_settings({**fake_env(devbox), "DEVBOX_USERS": "ada:Power:ada.lovelace@example.com"})
    devbox.prerequisites(make_ctx(devbox, s, world), need_docker=True)
    out = capsys.readouterr().out
    assert "group ai-claude-standard (the Standard tier) isn't in Identity Center: push it from Okta" in out
    assert "group devbox-users (who gets a box) is in Identity Center" in out
    assert "aren't used any more" in out and devbox.Report.problems == 0


def test_a_demo_persona_profile_is_refused(devbox, settings, world, sandbox, capsys, monkeypatch):
    monkeypatch.setattr(
        fake_aws.Sts,
        "get_caller_identity",
        lambda self: {
            "Account": ACCOUNT,
            "UserId": "x",
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/AWSReservedSSO_ClaudeCode-Power_abc/ada",
        },
    )
    with pytest.raises(devbox.Stop):
        devbox.prerequisites(make_ctx(devbox, settings, world), need_docker=True)
    assert "demo person's role" in capsys.readouterr().out


def test_an_az_microvm_vpc_mode_doesnt_support_is_refused(devbox, world, sandbox, capsys):
    s = devbox.load_settings({**fake_env(devbox), "DEVBOX_AZ": "us-east-1d"})  # use1-az6
    with pytest.raises(devbox.Stop):
        devbox.prerequisites(make_ctx(devbox, s, world), need_docker=True)
    out = capsys.readouterr().out
    assert "us-east-1d is use1-az6" in out and "supports only use1-az1, use1-az2, use1-az4" in out
    assert mutations(world.log) == []


def test_instances_in_devbox_env_is_refused_before_any_call(devbox, world, sandbox, capsys):
    s = devbox.load_settings({**fake_env(devbox), "DEVBOX_COMPUTE": "instances"})
    with pytest.raises(devbox.Stop):
        devbox.prerequisites(make_ctx(devbox, s, world), need_docker=True)
    assert "retire-instances" in capsys.readouterr().out and world.log == []


def test_image_change_updates_runtime_and_lambda(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    visit("ada"), visit("grace")
    (devbox.IMAGES["box"][1] / "Dockerfile").write_text("FROM scratch\nLABEL v=2\n")
    n = len(world.log)
    deploy(devbox, settings, world)
    ops = [c[1] for c in world.log[n:]]
    assert ops.count("UpdateAgentRuntime") == 2 and "CreateAgentRuntime" not in ops and "UpdateFunctionCode" not in ops
    for r in world.runtimes.values():
        assert r["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] == sandbox["builds"][-1]
        assert r["metadataConfiguration"] == {"requireMMDSV2": True}, "an update keeps MMDSv2"
        assert (
            r["networkConfiguration"]["networkMode"] == "VPC" and r["filesystemConfigurations"][0]["efsAccessPoint"]
        ), "an update replaces the whole configuration, so it carries the VPC and the folder"
        assert r["agentRuntimeVersion"] == "3"
    plan = json.loads(world.fns["devbox-provisioner"]["Environment"]["Variables"]["DEVBOX_PLAN"])
    assert plan["image_uri"] == sandbox["builds"][-1], "and a new person's box starts on the new image"


def test_a_longer_idle_timeout_is_capped_at_eight_hours(devbox, world, sandbox, visit, capsys):
    deploy(devbox, devbox.load_settings({**fake_env(devbox), "DEVBOX_IDLE_SECONDS": "86400"}), world)
    visit("ada")
    assert {json.dumps(r["lifecycleConfiguration"]) for r in world.runtimes.values()} == {
        json.dumps({"idleRuntimeSessionTimeout": 28800, "maxLifetime": 28800})
    }


def test_mmdsv2_rejection_is_a_warning(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    real = fake_aws.AgentCoreControl.update_agent_runtime

    def reject(self, agentRuntimeId, **req):
        if "metadataConfiguration" in req:
            raise fake_aws.error("ValidationException", "UpdateAgentRuntime", "metadataConfiguration is not supported")
        return real(self, agentRuntimeId, **req)

    monkeypatch.setattr(fake_aws.AgentCoreControl, "update_agent_runtime", reject)
    deploy(devbox, settings, world)
    assert visit("ada")[0] == 200, "the box works without it"
    assert "mmdsv2Rejected" in devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert "MMDSv2 isn't required" in out and "spike item 7" in out and len(world.runtimes) == 1
    n = len(world.log)
    deploy(devbox, settings, world)  # asked once, not on every deploy
    assert devbox.Report.changes == 0 and mutations(world.log[n:]) == []


# ----------------------------------------------------------------------------- network
def cidr_routes(world):
    """Every route to the firewall endpoint or the NAT (not the local routes, not the S3 endpoint's prefix-list route)."""
    return [
        r
        for t in world.rts.values()
        for r in t["Routes"]
        if r.get("DestinationCidrBlock") and (r.get("GatewayId", "").startswith("vpce-") or r.get("NatGatewayId"))
    ]


def test_network_pause_and_resume(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    old_endpoint, old_nat = world.firewall["endpoint"], next(iter(world.nats))
    (s3,) = world.endpoints
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_network(make_ctx(devbox, settings, world), "pause") == 0
    out = capsys.readouterr().out
    assert "can't start" in out and "Bedrock, STS and SSO" in out and "10–15 minutes" in out
    assert "the S3 gateway endpoint and the EFS files stay" in out
    assert world.firewall is None and world.nats[old_nat]["State"] == "deleted"
    assert world.addresses == {}, "nothing pins the NAT's address: pause releases it (resume allocates a new one)"
    assert world.dns_assocs and world.qlc_assocs, "the DNS Firewall stays"
    assert list(world.endpoints) == [s3] and world.efs_mts and world.efs_fs, "free, and the files"
    assert sorted(r["State"] for r in cidr_routes(world)) == ["blackhole"], (
        "the NAT's route; the firewall's had to go first"
    )
    assert not [r for t in world.rts.values() for r in t["Routes"] if r.get("GatewayId") == old_endpoint], (
        "AWS won't delete a firewall that a route table still points at"
    )
    assert [r for t in world.rts.values() for r in t["Routes"] if r.get("DestinationPrefixListId")], (
        "the S3 endpoint's route stays"
    )
    vpc = next(iter(world.vpcs.values()))
    assert {"Key": "devbox-network-paused", "Value": "true"} in vpc["Tags"]

    n = len(world.log)
    deploy(devbox, settings, world)  # a deploy while paused leaves it paused
    assert "CreateFirewall" not in [c[1] for c in world.log[n:]] and "CreateNatGateway" not in [
        c[1] for c in world.log[n:]
    ]

    devbox.Report.reset()
    n = len(world.log)
    assert devbox.cmd_network(make_ctx(devbox, settings, world), "resume") == 0
    new_endpoint = world.firewall["endpoint"]
    new_nat = next(k for k, v in world.nats.items() if v["State"] == "available")
    assert new_endpoint != old_endpoint and new_nat != old_nat
    resumed = [c[1] for c in world.log[n:]]
    assert resumed.count("CreateRoute") == 2 and resumed.count("ReplaceRoute") == 1, (
        "the firewall's two routes, and the NAT's"
    )
    assert all(r["State"] == "active" for t in world.rts.values() for r in t["Routes"])
    assert [c["LogType"] for c in world.fw_logging] == ["ALERT", "FLOW"]
    assert not [t for t in next(iter(world.vpcs.values()))["Tags"] if t["Key"] == "devbox-network-paused"]
    assert len(world.addresses) == 1 and list(world.endpoints) == [s3]


def egress_ports(world):
    box_sg = group_id(world, "devbox-box")
    return sorted(
        r["FromPort"]
        for r in world.sg_rules.values()
        if r["GroupId"] == box_sg and r["IsEgress"] and r.get("CidrIpv4") == "0.0.0.0/0"
    )


def nfs_rules(world):
    box_sg, efs_sg = group_id(world, "devbox-box"), group_id(world, "devbox-efs")
    return [r for r in rules(world, box_sg, True) if r[1] == 2049], rules(world, efs_sg, False)


def dns_any_rule(world):
    any_id = next(i for i, d in world.dns_lists.items() if d["Name"] == "devbox-dns-any")
    return next(r for r in world.dns_rules.values() if r["FirewallDomainListId"] == any_id)


def test_network_allowlist_lists_what_was_blocked(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    fs_id = next(iter(world.efs_fs))
    pol = world.fw_policies["devbox-egress"]["FirewallPolicy"]
    allow = world.rule_groups["devbox-allowlist"]["RuleGroupResponse"]["RuleGroupArn"]
    # deploy sets up both firewalls with the allowlist: drop everything else, port 80 closed, NXDOMAIN for other names
    assert [r["ResourceArn"] for r in pol["StatefulRuleGroupReferences"]] == [allow]
    assert pol["StatefulDefaultActions"] == ["aws:drop_established", "aws:alert_established"]
    assert egress_ports(world) == [443] and nfs_rules(world)[0][0][1] == 2049
    assert (dns_any_rule(world)["Action"], dns_any_rule(world)["BlockResponse"]) == ("BLOCK", "NXDOMAIN")
    world.query_names = ["bedrock-runtime.us-east-1.amazonaws.com", "registry.npmjs.org", "github.com"]
    world.query_http_names = ["example.org"]
    world.dns_query_names = [
        "ip-10-40-1-5.ec2.internal",
        "registry.npmjs.org",
        "c2hlbgxv.exfil.example",
        f"use1-az1.{fs_id}.efs.us-east-1.amazonaws.com",
    ]
    devbox.Report.reset()
    capsys.readouterr()
    n = len(world.log)
    assert devbox.cmd_network(make_ctx(devbox, settings, world), "allowlist") == 0
    out = capsys.readouterr().out
    blocked = out.split("aren't on the allowlist")[1]
    assert (
        "registry.npmjs.org  (DNS, TLS)" in blocked
        and "github.com  (TLS)" in blocked
        and "example.org  (HTTP)" in blocked
    )
    assert "c2hlbgxv.exfil.example  (DNS)" in blocked and "ip-10-40-1-5.ec2.internal  (DNS)" in blocked
    assert "bedrock-runtime.us-east-1.amazonaws.com" not in blocked
    assert ".efs." not in blocked, "the mount target's name stays resolvable, so a new session still mounts EFS"
    assert mutations(world.log[n:]) == [], "nothing to change: the allowlist file is what deploy applied"
    # a later deploy is steady state too
    n = len(world.log)
    deploy(devbox, settings, world)
    assert mutations(world.log[n:]) == []


def test_network_allowlist_with_nothing_blocked(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_network(make_ctx(devbox, settings, world), "allowlist") == 0
    assert "nothing blocked in the last 24 h is missing from the allowlist" in capsys.readouterr().out


def test_allowlist_edit_updates_the_rule_group(devbox, settings, world, sandbox, monkeypatch, tmp_path):
    deploy(devbox, settings, world)
    tpl = tmp_path / "templates"
    tpl.mkdir()
    for f in devbox.TEMPLATES.rglob("*"):
        if f.is_file():
            (tpl / f.relative_to(devbox.TEMPLATES)).parent.mkdir(parents=True, exist_ok=True)
            (tpl / f.relative_to(devbox.TEMPLATES)).write_text(f.read_text())
    (tpl / "egress-allowlist.txt").write_text(
        (tpl / "egress-allowlist.txt").read_text() + "bedrock-agentcore.us-east-1.amazonaws.com\n"
    )
    monkeypatch.setattr(devbox, "TEMPLATES", tpl)
    deploy(devbox, settings, world)
    targets = world.rule_groups["devbox-allowlist"]["RuleGroup"]["RulesSource"]["RulesSourceList"]["Targets"]
    dns = next(d for d in world.dns_lists.values() if d["Name"] == "devbox-dns-allow")["Domains"]
    assert (
        "bedrock-agentcore.us-east-1.amazonaws.com" in targets and "bedrock-agentcore.us-east-1.amazonaws.com." in dns
    )
    assert devbox.Report.changes == 2, "the Network Firewall rule group and the DNS Firewall domain list"


# ----------------------------------------------------------------------------- reset-box, state
def test_reset_box_moves_to_a_new_session_and_keeps_the_files(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    visit("ada"), visit("grace")
    name = box_name(devbox, "ada")
    aps_before = json.dumps(world.efs_aps, sort_keys=True, default=str)
    devbox.Report.reset()
    n = len(world.log)
    assert devbox.cmd_reset_box(make_ctx(devbox, settings, world), name, yes=True) == 0
    ops = [c[1] for c in world.log[n:]]
    assert "DeleteCapacityProviderSession" not in ops and "StopRuntimeSession" not in ops, "nobody may stop a session"
    assert not [o for o in ops if o.startswith(MUTATING) and o not in ("UpdateAgentRuntime", "PutItem")]
    rt = runtimes_by_name(world)[f"devbox_vm_{name}"]
    assert rt["environmentVariables"]["DEVBOX_SESSION_ID"] == devbox.session_id(ADA, 2)
    assert rt["metadataConfiguration"] == {"requireMMDSV2": True} and rt["networkConfiguration"]["networkMode"] == "VPC"
    assert rt["filesystemConfigurations"][0]["efsAccessPoint"]["mountPath"] == "/mnt/workspace"
    assert json.dumps(world.efs_aps, sort_keys=True, default=str) == aps_before, "the same folder"
    assert devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])["generation"] == 2
    assert visit("ada")[1]["box"]["generation"] == 2, "the page gets it from /api/box"
    assert visit("grace")[1]["box"]["generation"] == 1
    assert json.loads(devbox.STATE_FILE.read_text())["boxes"][name]["generation"] == 2
    out = capsys.readouterr().out
    assert (
        f"If {name} has the dev box open, they must reload the page" in out
        and f"on EFS (/devbox/{name}) and stay" in out
    )
    # the next deploy keeps generation 2
    n = len(world.log)
    deploy(devbox, settings, world)
    assert devbox.Report.changes == 0 and mutations(world.log[n:]) == []


def test_reset_box_asks_for_the_box_name(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    deploy(devbox, settings, world)
    visit("ada")
    monkeypatch.setattr("builtins.input", lambda prompt: "grace")
    devbox.Report.reset()
    n = len(world.log)
    with pytest.raises(devbox.Stop):
        devbox.cmd_reset_box(make_ctx(devbox, settings, world), box_name(devbox, "ada"), yes=False)
    assert mutations(world.log[n:]) == [] and "Not confirmed" in capsys.readouterr().out
    devbox.Report.reset()
    with pytest.raises(devbox.Stop):
        devbox.cmd_reset_box(make_ctx(devbox, settings, world), "nobody", yes=True)
    assert "no box named nobody" in capsys.readouterr().out


def test_generation_is_recovered_without_state(devbox, settings, world, sandbox, visit):
    deploy(devbox, settings, world)
    visit("ada")
    name = box_name(devbox, "ada")
    devbox.Report.reset()
    devbox.cmd_reset_box(make_ctx(devbox, settings, world), name, yes=True)
    devbox.STATE_FILE.unlink()
    n = len(world.log)
    deploy(devbox, settings, world)
    assert not [c for c in world.log[n:] if c[1].startswith(MUTATING)], "the table remembers it"
    assert json.loads(devbox.STATE_FILE.read_text())["boxes"][name]["generation"] == 2


# ----------------------------------------------------------------------------- retire-instances
def test_retire_instances_deletes_only_the_old_boxes(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    old = seed_instances(devbox, world)
    deploy(devbox, settings, world)
    visit("ada")
    vm = {r["agentRuntimeId"] for r in world.runtimes.values() if r["agentRuntimeName"].startswith("devbox_vm_")}
    efs_before = json.dumps(
        [world.efs_fs, world.efs_aps, world.efs_mts, world.efs_policies], sort_keys=True, default=str
    )
    monkeypatch.setattr("builtins.input", lambda prompt: ACCOUNT)
    devbox.Report.reset()
    capsys.readouterr()
    n = len(world.log)
    assert devbox.cmd_retire_instances(make_ctx(devbox, settings, world)) == 0
    out = capsys.readouterr().out
    log = world.log[n:]
    ops = [c[1] for c in log]

    # listed first (nothing deleted before the typed account id), then deleted
    listed = out.split("Type the account ID")[0].replace("  ", " ")
    for bit in (
        "would delete runtime devbox_ada",
        "would delete runtime devbox_grace",
        "would delete capacity provider devbox_ada",
        "would delete grace's session generation 4",
        f"would delete role {OPERATOR}",
        "would delete role devbox-exec",
        f"would delete role {INSTANCE_ROLE} and its instance profile",
        "EBS volume",
    ):
        assert bit in listed, bit
    assert "devbox_vm_" not in listed.split("This deletes")[0].replace("microVM runtimes devbox_vm_<name> stay", "")

    def at(op, last=False):
        idx = [i for i, o in enumerate(ops) if o == op]
        assert idx, f"{op} never called"
        return idx[-1] if last else idx[0]

    assert at("DeleteAgentRuntime", True) < at("DeleteCapacityProviderSession") < at("DeleteCapacityProvider")
    assert at("DeleteCapacityProvider", True) < at("DeleteRole")
    deleted_runtimes = {p["agentRuntimeId"] for _, o, p in log if o == "DeleteAgentRuntime"}
    assert deleted_runtimes == {old["ada"]["runtimeId"], old["grace"]["runtimeId"]}
    assert {s for _, s in world.deleted_sessions} == {devbox.session_id(ADA, 1)} | {
        devbox.session_id(GRACE, g) for g in (1, 2, 3, 4)
    }, "every generation's session: each one is an EBS volume"
    assert world.cps == {} and set(world.runtimes) == vm
    assert (
        set(world.roles)
        == {
            f"devbox-exec-{box_name(devbox, 'ada')}",
            "devbox-edge-lambda",
            "devbox-tools-gateway",
            "devbox-provisioner",
        }
        and world.profiles == {}
    )
    assert (
        json.dumps([world.efs_fs, world.efs_aps, world.efs_mts, world.efs_policies], sort_keys=True, default=str)
        == efs_before
    )
    assert "instances" not in json.loads(devbox.STATE_FILE.read_text())
    assert "Retired." in out
    # nothing left: says so, and deploy has nothing to warn about
    devbox.Report.reset()
    assert (
        devbox.cmd_retire_instances(make_ctx(devbox, settings, world)) == 0
        and "Nothing to retire" in capsys.readouterr().out
    )
    deploy(devbox, settings, world)
    assert "old Instances boxes" not in capsys.readouterr().out


def test_retire_instances_asks_for_the_account_id(devbox, settings, world, sandbox, capsys, monkeypatch):
    seed_instances(devbox, world)
    deploy(devbox, settings, world)
    monkeypatch.setattr("builtins.input", lambda prompt: "999999999999")
    devbox.Report.reset()
    n = len(world.log)
    with pytest.raises(devbox.Stop):
        devbox.cmd_retire_instances(make_ctx(devbox, settings, world))
    assert mutations(world.log[n:]) == [], "nothing is deleted before the typed account id"
    assert "Not confirmed. Nothing was deleted." in capsys.readouterr().out
    assert len(world.cps) == 2


def test_retire_instances_keeps_a_role_something_else_still_uses(devbox, settings, world, sandbox, capsys, monkeypatch):
    seed_instances(devbox, world)
    world.runtimes["someone_else-aaaaaaaaaa"] = {
        "agentRuntimeId": "someone_else-aaaaaaaaaa",
        "agentRuntimeArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/someone_else-aaaaaaaaaa",
        "agentRuntimeName": "someone_else",
        "agentRuntimeVersion": "1",
        "status": "READY",
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/devbox-exec",
    }
    monkeypatch.setattr("builtins.input", lambda prompt: ACCOUNT)
    devbox.Report.reset()
    assert devbox.cmd_retire_instances(make_ctx(devbox, settings, world)) == 0
    out = capsys.readouterr().out
    assert "kept: role devbox-exec, still used by runtime someone_else" in out
    assert "devbox-exec" in world.roles and OPERATOR not in world.roles and "someone_else-aaaaaaaaaa" in world.runtimes


def test_retire_instances_leaves_a_devbox_runtime_that_isnt_on_a_capacity_provider(
    devbox, settings, world, sandbox, capsys, monkeypatch
):
    seed_instances(devbox, world, generations=(("ada", ADA, 1),))
    world.runtimes["devbox_notes-bbbbbbbbbb"] = {
        "agentRuntimeId": "devbox_notes-bbbbbbbbbb",
        "agentRuntimeArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_notes-bbbbbbbbbb",
        "agentRuntimeName": "devbox_notes",
        "agentRuntimeVersion": "1",
        "status": "READY",
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/other",
    }
    monkeypatch.setattr("builtins.input", lambda prompt: ACCOUNT)
    devbox.Report.reset()
    devbox.cmd_retire_instances(make_ctx(devbox, settings, world))
    assert "devbox_notes-bbbbbbbbbb" in world.runtimes and "not an Instances box, left alone" in capsys.readouterr().out


def test_retire_instances_without_state_still_deletes_the_capacity_providers(
    devbox, settings, world, sandbox, capsys, monkeypatch
):
    """No uid, no generation: the session ids are unknown, but deleting the capacity provider deletes its sessions anyway."""
    seed_instances(devbox, world)
    devbox.STATE_FILE.unlink()
    monkeypatch.setattr("builtins.input", lambda prompt: ACCOUNT)
    devbox.Report.reset()
    assert devbox.cmd_retire_instances(make_ctx(devbox, settings, world)) == 0
    assert world.cps == {} and world.deleted_sessions == []
    assert "uid is unknown" in capsys.readouterr().out


def test_retire_instances_asks_again_when_a_capacity_provider_is_delete_failed(
    devbox, settings, world, sandbox, capsys, monkeypatch
):
    seed_instances(devbox, world)
    world.cp_delete_failures = 1
    monkeypatch.setattr("builtins.input", lambda prompt: ACCOUNT)
    devbox.Report.reset()
    assert devbox.cmd_retire_instances(make_ctx(devbox, settings, world)) == 0
    assert [c[1] for c in world.log].count("DeleteCapacityProvider") == 3 and world.cps == {}


# ----------------------------------------------------------------------------- undeploy, status
def test_undeploy_keeps_everyones_files(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    visit("ada"), visit("grace")
    fs_id = next(iter(world.efs_fs))
    aps = {a["RootDirectory"]["Path"]: a["AccessPointArn"] for a in world.efs_aps.values()}
    n = len(world.log)
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=False, yes=True) == 0
    ops = [c[1] for c in world.log[n:]]
    assert not [
        o
        for o in ops
        if o in ("DeleteFileSystem", "DeleteAccessPoint", "DeleteMountTarget", "DeleteFileSystemPolicy", "DeleteTable")
    ]
    assert world.runtimes == {} and world.gateways == {} and world.dists == {} and world.fn is None
    assert world.fns == {} and world.apis == {} and world.managed_policies == {}, (
        "the provisioner, its API and the boundary"
    )
    assert (
        world.firewall is None and all(x["State"] == "deleted" for x in world.nats.values()) and world.addresses == {}
    )
    assert list(world.efs_fs) == [fs_id] and len(world.efs_aps) == 2 and len(world.efs_mts) == 1, (
        "everyone's files, and how they're reached"
    )
    assert len(world.tables["devbox-boxes"]["items"]) == 2, "who has which box name and folder"
    assert (
        world.vpcs
        and world.subnets
        and {g["GroupName"] for g in world.sgs.values()} == {"devbox-box", "devbox-efs"}
        and world.endpoints
    )
    assert world.dns_groups and world.dns_assocs and world.qlc_assocs, "the DNS Firewall stays with the VPC"
    assert world.roles == {} and world.repos == {}
    out = capsys.readouterr().out
    assert f"kept: EFS file system devbox ({fs_id})" in out and "kept: the DNS Firewall devbox-dns" in out
    assert "kept: table devbox-boxes" in out
    assert (
        "each new runtime mounts the same folder (spike item 13)" in out
        and "Okta's redirect URIs and Trusted Origin must change" in out
    )
    rec = devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    assert rec["name"] == box_name(devbox, "ada") and rec["generation"] == 1, (
        "her box name, folder and generation are remembered"
    )
    old_wb = json.loads(devbox.STATE_FILE.read_text())["lastWorkbenchDomain"]
    # and deploy brings everyone's box back, on the same file system and folders, and says what to change in Okta
    n = len(world.log)
    deploy(devbox, settings, world)
    assert list(world.efs_fs) == [fs_id] and "CreateAccessPoint" not in [c[1] for c in world.log[n:]]
    assert {
        r["filesystemConfigurations"][0]["efsAccessPoint"]["accessPointArn"] for r in world.runtimes.values()
    } == set(aps.values())
    assert all(r["boundary"] for n_, r in world.roles.items() if n_.startswith("devbox-exec-")), (
        "remade with the boundary"
    )
    new_wb = json.loads(devbox.STATE_FILE.read_text())["lastWorkbenchDomain"]
    out = capsys.readouterr().out
    assert new_wb != old_wb
    assert f"The workbench domain changed from {old_wb} to {new_wb}" in out
    assert f"https://{old_wb}/callback  →  https://{new_wb}/callback" in out and "Trusted Origin" in out
    assert out.count("The workbench domain changed") == 2, "at the edge step, and again right before the Okta steps"


def test_undeploy_with_volumes_in_dependency_order(devbox, settings, world, sandbox, visit, capsys):
    deploy(devbox, settings, world)
    visit("ada")
    n = len(world.log)
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=True, yes=True) == 0
    log = world.log[n:]
    ops = [c[1] for c in log]

    def at(op, last=False):
        idx = [i for i, o in enumerate(ops) if o == op]
        assert idx, f"{op} never called"
        return idx[-1] if last else idx[0]

    assert at("DeleteAgentRuntime", True) < at("DeleteAccessPoint") < at("DeleteMountTarget") < at("DeleteFileSystem")
    assert at("DeleteFileSystem") < at("UpdateTable") < at("DeleteTable"), "deletion protection off first"
    assert (
        at("DeleteFileSystem") < at("DeleteGateway") < at("DeleteDistribution") < at("DeleteApi") < at("DeleteFirewall")
    )
    assert at("DeleteFirewall") < at("DeleteNatGateway") < at("ReleaseAddress")
    assert at("DisassociateResolverQueryLogConfig") < at("DeleteResolverQueryLogConfig") < at("DeleteVpc")
    assert (
        at("DisassociateFirewallRuleGroup")
        < at("DeleteFirewallRule")
        < at("DeleteFirewallRuleGroup")
        < at("DeleteFirewallDomainList")
        < at("DeleteVpc")
    )
    iam_delete_policy = next(i for i, (svc, o, _) in enumerate(log) if svc == "iam" and o == "DeletePolicy")
    assert at("DeleteRole", True) < iam_delete_policy, "the boundary goes after the roles that carry it"
    assert (
        at("DeleteRole", True)
        < at("DeleteRepository")
        < at("DeleteVpcEndpoints")
        < at("DeleteSecurityGroup")
        < at("DeleteSubnet")
    )
    assert at("DeleteSubnet", True) < at("DeleteInternetGateway") < at("DeleteVpc"), (
        "the VPC last (AgentCore's interfaces linger)"
    )
    assert at("RevokeSecurityGroupEgress") < at("DeleteSecurityGroup"), "devbox-box's NFS rule names devbox-efs"
    disable = [
        i
        for i, (s, o, p) in enumerate(log)
        if o == "UpdateDistribution" and p["DistributionConfig"]["Enabled"] is False
    ]
    assert len(disable) == 2 and max(disable) < at("DeleteDistribution")
    assert (
        world.efs_fs == {}
        and world.efs_aps == {}
        and world.efs_mts == {}
        and world.efs_policies == {}
        and world.endpoints == {}
    )
    assert (
        world.vpcs == {} and world.sgs == {} and world.roles == {} and world.profiles == {} and world.rule_groups == {}
    )
    assert (
        world.fw_policies == {}
        and world.oacs == {}
        and world.log_groups == {}
        and world.engines == {}
        and world.policies == {}
    )
    assert world.dns_lists == {} and world.dns_groups == {} and world.dns_rules == {} and world.dns_assocs == {}
    assert world.qlcs == {} and world.qlc_assocs == {}
    assert world.tables == {} and world.apis == {} and world.fns == {} and world.managed_policies == {}
    assert not devbox.STATE_FILE.exists()


def test_undeploy_and_the_old_instances_boxes(devbox, settings, world, sandbox, capsys):
    """Plain undeploy deletes the old runtimes but keeps their capacity providers (disks) and roles; --delete-volumes
    deletes those too, every recorded session first."""
    seed_instances(devbox, world)
    deploy(devbox, settings, world)
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=False, yes=True) == 0
    out = capsys.readouterr().out
    assert world.runtimes == {} and len(world.cps) == 2 and world.deleted_sessions == []
    assert set(world.roles) == {OPERATOR, INSTANCE_ROLE}, "the capacity providers launch with them"
    assert "kept: capacity provider devbox_ada" in out and "retire-instances" in out
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=True, yes=True) == 0
    assert world.cps == {} and world.roles == {} and world.profiles == {} and world.efs_fs == {} and world.vpcs == {}
    assert {s for _, s in world.deleted_sessions} == {devbox.session_id(ADA, 1)} | {
        devbox.session_id(GRACE, g) for g in (1, 2, 3, 4)
    }


def test_undeploy_lists_first_and_asks(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    deploy(devbox, settings, world)
    visit("ada")
    n = len(world.log)
    monkeypatch.setattr("builtins.input", lambda prompt: "999999999999")
    devbox.Report.reset()
    with pytest.raises(devbox.Stop):
        devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=True, yes=False)
    assert mutations(world.log[n:]) == [], "nothing is removed before the typed account id"
    out = capsys.readouterr().out.replace("  ", " ")
    assert "would delete EFS file system devbox" in out and "every file on it" in out
    assert (
        f"would delete runtime devbox_vm_{box_name(devbox, 'ada')}" in out and "would delete table devbox-boxes" in out
    )
    assert "Not confirmed" in out


def test_status_is_read_only(devbox, settings, world, sandbox, visit, capsys):
    seed_instances(devbox, world)
    deploy(devbox, settings, world)
    visit("ada"), visit("grace")
    n = len(world.log)
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_status(make_ctx(devbox, settings, world, check=True)) == 0
    assert mutations(world.log[n:]) == []
    out = capsys.readouterr().out
    a, g = box_name(devbox, "ada"), box_name(devbox, "grace")
    for bit in (
        "compute type microvm",
        f"{a} (Power): runtime devbox_vm_{a}",
        "microVM, VPC network",
        "idle 3600 s / max 28800 s",
        f"EFS folder /devbox/{a}",
        f"access point /devbox/{g}",
        "runs as 1000:1000",
        "file system devbox fs-",
        "encrypted, elastic throughput",
        "mount target fsmt-",
        "use1-az1",
        "file system policy: TLS only, no root",
        "S3 gateway endpoint devbox-s3: vpce-",
        "egress allowlist on",
        "workbench: https://d",
        "DNS Firewall devbox-dns: NXDOMAIN for names off the allowlist, on the VPC's resolver",
        "Lambda devbox-provisioner: Active",
        "HTTP API devbox-api: https://",
        "Old Instances boxes",
        "capacity provider devbox_grace",
        "runtime devbox_ada",
    ):
        assert bit in out, bit


def test_someone_taken_out_of_the_group_gets_no_box(devbox, settings, world, sandbox, visit):
    deploy(devbox, settings, world)
    visit("ada")
    uid, login, _ = PEOPLE["ada"]
    status, body, _ = visit((uid, login, ["ai-claude-power"]))
    assert status == 403 and "devbox-users" in body["message"]
    rt = runtimes_by_name(world)[f"devbox_vm_{box_name(devbox, 'ada')}"]
    groups = next(
        c
        for c in rt["authorizerConfiguration"]["customJWTAuthorizer"]["customClaims"]
        if c["inboundTokenClaimName"] == "groups"
    )
    assert groups["authorizingClaimMatchValue"] == {
        "claimMatchValue": {"matchValueStringList": ["devbox-users"]},
        "claimMatchOperator": "CONTAINS_ANY",
    }, "her own runtime refuses her token too"
    assert len(world.runtimes) == 1 and len(world.efs_aps) == 1, "nothing is deleted: her box and folder wait for her"


# ----------------------------------------------------------------------------- failure paths
def test_an_access_point_in_the_error_state_fails_the_deploy(devbox, settings, world, sandbox, visit, capsys):
    """A ✗ during deploy must not end in a green 'Deployed.' and exit 0."""
    deploy(devbox, settings, world)
    visit("ada")
    next(iter(world.efs_aps.values()))["LifeCycleState"] = "error"
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world)) == 1
    out = capsys.readouterr().out
    assert (
        f"{box_name(devbox, 'ada')}'s box: your folder" in out
        and "is in the error state" in out
        and "Deployed with 1 problem(s)" in out
    )
    assert "Deployed.\x1b" not in out and "\nDeployed. " not in out
    assert "Okta, by hand" in out and "Spike checklist" in out, "the next steps are still printed"
    devbox.Report.reset()
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world, check=True)) == 1, "check reports the problem too"


def test_a_failed_runtime_says_why(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    real = fake_aws.AgentCoreControl.create_agent_runtime

    def create(self, agentRuntimeName, **req):
        out = real(self, agentRuntimeName, **req)
        self.w.runtimes[out["agentRuntimeId"]].update(
            status="CREATE_FAILED", failureReason="Subnet use1-az6 isn't supported"
        )
        return out

    deploy(devbox, settings, world)
    monkeypatch.setattr(fake_aws.AgentCoreControl, "create_agent_runtime", create)
    status, body, _ = visit("ada")
    assert status == 500 and body["message"] == "your box's runtime is CREATE_FAILED: Subnet use1-az6 isn't supported"
    devbox.Report.reset()
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world)) == 1
    out = capsys.readouterr().out
    assert "CREATE_FAILED: Subnet use1-az6 isn't supported" in out
    assert "environmentVariables" not in out, "the status and the reason, not the whole runtime"


def test_a_refused_provisioner_fails_instead_of_waiting_for_the_role(
    devbox, settings, world, sandbox, visit, monkeypatch
):
    """Seen live: the provisioner's own AccessDenied names its role ("assumed-role/devbox-provisioner/…"), which the
    new-role pattern matched, so the page said "waiting for the new role" for ever and the log said nothing."""
    deploy(devbox, settings, world)

    def create(self, agentRuntimeName, **req):
        raise fake_aws.error(
            "AccessDeniedException",
            "CreateAgentRuntime",
            "User: arn:aws:sts::111122223333:assumed-role/devbox-provisioner/devbox-provisioner is not "
            "authorized to perform: bedrock-agentcore:CreateAgentRuntime because no identity-based policy allows it",
        )

    monkeypatch.setattr(fake_aws.AgentCoreControl, "create_agent_runtime", create)
    status, body, calls = visit("ada")
    assert status == 500 and "AccessDeniedException" in body["message"] and calls < 10, (status, body, calls)


def test_deploy_leaves_making_a_box_to_the_provisioner(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    """Seen live: a first visit failed, then deploy made the box as admin, so the provisioner's failure stayed hidden.
    Deploy only updates boxes that have a runtime; the next visit makes the rest."""
    deploy(devbox, settings, world)
    real = fake_aws.AgentCoreControl.create_agent_runtime

    def refused(self, agentRuntimeName, **req):
        raise fake_aws.error(
            "AccessDeniedException",
            "CreateAgentRuntime",
            "User: arn:aws:sts::111122223333:assumed-role/"
            "devbox-provisioner/devbox-provisioner is not authorized to perform: bedrock-agentcore:TagResource",
        )

    monkeypatch.setattr(fake_aws.AgentCoreControl, "create_agent_runtime", refused)
    assert visit("ada")[0] == 500
    monkeypatch.setattr(fake_aws.AgentCoreControl, "create_agent_runtime", real)
    capsys.readouterr()
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert world.runtimes == {} or not any("ada" in r.get("agentRuntimeName", "") for r in world.runtimes.values()), (
        "deploy made no box"
    )
    assert "not made yet" in out and "The provisioner makes it on" in out
    status, body, _ = visit("ada")
    assert status == 200 and body["ready"], "the next visit makes it"


def test_a_new_role_is_waited_for_only_so_long(devbox, settings, world, sandbox, visit, monkeypatch):
    """AgentCore refusing a role IAM is still making is retried while the role is new, then reported."""
    deploy(devbox, settings, world)

    def create(self, agentRuntimeName, **req):
        raise fake_aws.error(
            "ValidationException", "CreateAgentRuntime", "Role validation failed: unable to assume the role"
        )

    monkeypatch.setattr(fake_aws.AgentCoreControl, "create_agent_runtime", create)
    status, body, calls = visit("ada", limit=5)
    assert (status, body["step"], calls) == (202, "role", 5), "a new role: keep asking"
    monkeypatch.setattr(devbox, "ROLE_PROPAGATION_S", 0)
    status, body, _ = visit("ada")
    assert status == 500 and "ValidationException" in body["message"], "past the window: the real error"


def test_the_file_system_policy_waits_for_a_new_role(devbox, settings, world, sandbox, capsys, monkeypatch):
    """EFS refuses a principal IAM hasn't finished making yet ("invalid Principal"): retried like any new-role error."""
    real = fake_aws.Efs.put_file_system_policy
    left = {"n": 2}

    def put(self, FileSystemId, Policy, **kw):
        if left["n"]:
            left["n"] -= 1
            raise fake_aws.error(
                "InvalidPolicyException", "PutFileSystemPolicy", "Policy contains invalid Principal block"
            )
        return real(self, FileSystemId, Policy, **kw)

    monkeypatch.setattr(fake_aws.Efs, "put_file_system_policy", put)
    deploy(devbox, settings, world)
    assert len([c for c in world.log if c[1] == "PutFileSystemPolicy"]) == 3 and world.efs_policies


def test_a_mount_target_in_another_vpc_is_a_problem(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    (mt,) = world.efs_mts.values()
    mt["VpcId"] = "vpc-0somewhereelse0"
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world)) == 1
    assert "has a mount target in another VPC (vpc-0somewhereelse0)" in capsys.readouterr().out


def test_a_mount_target_behind_the_wrong_group_is_put_right(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    (mid,) = world.efs_mts
    world.efs_mt_sgs[mid] = [group_id(world, "devbox-box")]
    deploy(devbox, settings, world)
    assert world.efs_mt_sgs[mid] == [group_id(world, "devbox-efs")]
    assert "put it behind devbox-efs only" in capsys.readouterr().out


def test_an_unencrypted_file_system_is_reported_not_replaced(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    (fs,) = world.efs_fs.values()
    fs.update(Encrypted=False, ThroughputMode="bursting")
    n = len(world.log)
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert (
        "NOT encrypted" in out
        and "isn't encrypted at rest, and that can't be changed" in out
        and "bursting throughput" in out
    )
    assert "CreateFileSystem" not in [c[1] for c in world.log[n:]] and "DeleteFileSystem" not in [
        c[1] for c in world.log[n:]
    ]


def test_resume_doesnt_trust_a_stale_paused_tag(devbox, settings, world, sandbox, capsys, monkeypatch):
    deploy(devbox, settings, world)
    devbox.Report.reset()
    devbox.cmd_network(make_ctx(devbox, settings, world), "pause")
    real = fake_aws.Ec2.describe_vpcs
    stale = {"n": 0}

    def describe_vpcs(self, **kw):
        out = real(self, **kw)
        if stale["n"] < 3:  # DeleteTags isn't visible to the next reads yet
            stale["n"] += 1
            for v in out["Vpcs"]:
                v["Tags"] = [t for t in v["Tags"] if t["Key"] != "devbox-network-paused"] + [
                    {"Key": "devbox-network-paused", "Value": "true"}
                ]
        return out

    monkeypatch.setattr(fake_aws.Ec2, "describe_vpcs", describe_vpcs)
    devbox.Report.reset()
    capsys.readouterr()
    assert devbox.cmd_network(make_ctx(devbox, settings, world), "resume") == 0
    out = capsys.readouterr().out
    assert "stays a blackhole" not in out and "Running." in out
    assert world.firewall and [x for x in world.nats.values() if x["State"] == "available"]
    assert json.loads(devbox.STATE_FILE.read_text())["network"]["paused"] is False


def test_resume_fails_if_the_way_out_isnt_back(devbox, settings, world, sandbox, capsys, monkeypatch):
    deploy(devbox, settings, world)
    devbox.Report.reset()
    devbox.cmd_network(make_ctx(devbox, settings, world), "pause")
    monkeypatch.setattr(devbox, "ensure_network", lambda ctx, **kw: setattr(ctx, "net", {"vpc": "vpc-1"}))
    devbox.Report.reset()
    with pytest.raises(devbox.Stop):
        devbox.cmd_network(make_ctx(devbox, settings, world), "resume")
    assert "resume didn't bring back the NAT gateway" in capsys.readouterr().out


def test_an_unsuccessful_gateway_update_fails_fast_with_its_reasons(
    devbox, settings, world, sandbox, capsys, monkeypatch
):
    real = fake_aws.AgentCoreControl.update_gateway

    def update(self, gatewayIdentifier, **kw):
        out = real(self, gatewayIdentifier, **kw)
        self.w.gateways[gatewayIdentifier].update(
            status="UPDATE_UNSUCCESSFUL", _reads=5, statusReasons=["The gateway role can't read the policy engine"]
        )
        return out

    monkeypatch.setattr(fake_aws.AgentCoreControl, "update_gateway", update)
    devbox.Report.reset()
    with pytest.raises(devbox.Stop):
        devbox.cmd_deploy(make_ctx(devbox, settings, world))
    out = capsys.readouterr().out
    assert "UPDATE_UNSUCCESSFUL (The gateway role can't read the policy engine)" in out
    polls = [c for c in world.log if c[1] == "GetGateway"]
    assert len(polls) < 10, "a failure state, not 15 minutes of polling"


def test_a_failed_gateway_is_not_reused(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    (gw,) = world.gateways.values()
    gw.update(status="FAILED", statusReasons=["KMS key not usable"])
    devbox.Report.reset()
    capsys.readouterr()
    with pytest.raises(devbox.Stop):
        devbox.cmd_deploy(make_ctx(devbox, settings, world))
    out = capsys.readouterr().out
    assert "is FAILED: KMS key not usable" in out and "delete-gateway --gateway-identifier" in out
    assert "✓ gateway devbox-tools" not in out.replace("\x1b[32m", "").replace("\x1b[0m", "")


def test_a_failed_target_is_made_again(devbox, settings, world, sandbox, capsys):
    deploy(devbox, settings, world)
    (tgt,) = world.targets.values()
    tgt["status"] = "FAILED"
    old = tgt["targetId"]
    deploy(devbox, settings, world)
    assert [t["status"] for t in world.targets.values()] == ["READY"] and old not in world.targets
    assert "left FAILED by an earlier run" in capsys.readouterr().out


def test_an_mmdsv2_conflict_is_retried_not_recorded(devbox, settings, world, sandbox, visit, capsys, monkeypatch):
    real = fake_aws.AgentCoreControl.update_agent_runtime
    conflicts = {"left": 1}

    def update(self, agentRuntimeId, **req):
        if "metadataConfiguration" in req and conflicts["left"]:
            conflicts["left"] -= 1
            raise fake_aws.error("ConflictException", "UpdateAgentRuntime", "The runtime is being updated")
        return real(self, agentRuntimeId, **req)

    monkeypatch.setattr(fake_aws.AgentCoreControl, "update_agent_runtime", update)
    deploy(devbox, settings, world)
    assert visit("ada")[0] == 200
    assert "mmdsv2Rejected" not in devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    assert all(r["metadataConfiguration"] == {"requireMMDSV2": True} for r in world.runtimes.values())


def test_a_persistent_mmdsv2_conflict_is_asked_again_next_time(
    devbox, settings, world, sandbox, visit, capsys, monkeypatch
):
    real = fake_aws.AgentCoreControl.update_agent_runtime

    def update(self, agentRuntimeId, **req):
        if "metadataConfiguration" in req:
            raise fake_aws.error("ConflictException", "UpdateAgentRuntime", "The runtime is being updated")
        return real(self, agentRuntimeId, **req)

    monkeypatch.setattr(fake_aws.AgentCoreControl, "update_agent_runtime", update)
    deploy(devbox, settings, world)
    assert visit("ada", limit=6)[0] == 202, "the page keeps waiting"
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert "carries on" in out
    assert "mmdsv2Rejected" not in devbox.ddb_rec(world.tables["devbox-boxes"]["items"][devbox.uid_key(ADA)])
    monkeypatch.setattr(fake_aws.AgentCoreControl, "update_agent_runtime", real)
    deploy(devbox, settings, world)
    assert all(r["metadataConfiguration"] == {"requireMMDSV2": True} for r in world.runtimes.values())


def test_a_new_security_group_never_keeps_allow_all(devbox, settings, world, sandbox, monkeypatch):
    """The read straight after CreateSecurityGroup can miss the default rule; deploy mustn't depend on it."""
    real = fake_aws.Ec2.describe_security_group_rules
    first_read = {"read": True}

    def describe(self, Filters):
        if first_read["read"]:
            first_read["read"] = False
            return {"SecurityGroupRules": []}
        return real(self, Filters)

    monkeypatch.setattr(fake_aws.Ec2, "describe_security_group_rules", describe)
    deploy(devbox, settings, world)
    egress = sorted((r["IpProtocol"], r["FromPort"]) for r in world.sg_rules.values() if r["IsEgress"])
    assert ("-1", -1) not in egress and egress == [
        ("tcp", 53),
        ("tcp", 443),
        ("tcp", 2049),
        ("udp", 53),
    ], "devbox-box's rules only: devbox-efs has no outbound at all"


def test_a_stale_read_of_the_security_group_rules_is_harmless(devbox, settings, world, sandbox, monkeypatch):
    """The read still shows the default rule we just revoked: revoking it again by id isn't an error."""
    real_create = fake_aws.Ec2.create_security_group
    ghost = {}

    def create(self, **kw):
        out = real_create(self, **kw)
        ghost.update(next(r for r in self.w.sg_rules.values() if r["GroupId"] == out["GroupId"]))
        return out

    real_describe = fake_aws.Ec2.describe_security_group_rules

    def describe(self, Filters):
        out = real_describe(self, Filters)
        if ghost and ghost["SecurityGroupRuleId"] not in self.w.sg_rules:
            out["SecurityGroupRules"].append(dict(ghost))
            ghost.clear()
        return out

    monkeypatch.setattr(fake_aws.Ec2, "create_security_group", create)
    monkeypatch.setattr(fake_aws.Ec2, "describe_security_group_rules", describe)
    deploy(devbox, settings, world)
    assert ("-1", -1) not in [(r["IpProtocol"], r["FromPort"]) for r in world.sg_rules.values()]


def test_a_waiter_failure_says_why_and_a_failed_lambda_is_applied_again(
    devbox, settings, world, sandbox, capsys, monkeypatch
):
    real_wait = fake_aws.FakeWaiter.wait

    def wait(self, **kw):
        if self.name == "function_active_v2" and kw.get("FunctionName") == "devbox-edge":
            self.world.fn.update(State="Failed", StateReason="The image manifest is not supported")
            raise WaiterError(name=self.name, reason="Waiter encountered a terminal failure state", last_response={})
        return real_wait(self, **kw)

    monkeypatch.setattr(fake_aws.FakeWaiter, "wait", wait)
    devbox.Report.reset()
    with pytest.raises(devbox.Stop):
        devbox.cmd_deploy(make_ctx(devbox, settings, world))
    out = capsys.readouterr().out
    assert "Lambda devbox-edge: State Failed: The image manifest is not supported" in out
    # next deploy: not "✓ Lambda devbox-edge", but the image applied again
    monkeypatch.setattr(fake_aws.FakeWaiter, "wait", real_wait)
    n = len(world.log)
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert "is Failed" in out and "UpdateFunctionCode" in [c[1] for c in world.log[n:]]


def test_main_turns_a_botocore_error_into_one_line(devbox, monkeypatch, capsys, tmp_path):
    env = tmp_path / "devbox.env"
    env.write_text("".join(f'{k}="{v}"\n' for k, v in fake_env(devbox).items()))

    def boom(ctx):
        raise WaiterError(name="nat_gateway_available", reason="Max attempts exceeded", last_response={})

    monkeypatch.setattr(devbox, "cmd_deploy", boom)
    assert devbox.main(["--env", str(env), "deploy"]) == 1
    assert "WaiterError" in capsys.readouterr().out


def test_main_lists_retire_instances_and_it_has_no_yes_flag(devbox, capsys):
    with pytest.raises(SystemExit) as e:
        devbox.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "retire-instances" in out and "microVM" in out
    with pytest.raises(SystemExit) as e:
        devbox.main(["retire-instances", "--yes"])  # deleting disks always asks for the typed account id
    assert e.value.code == 2


def test_dns_query_logging_that_cant_write_is_a_problem(devbox, settings, world, sandbox, capsys):
    world.qlc_assoc_outcome = "ACTION_NEEDED"
    devbox.Report.reset()
    assert devbox.cmd_deploy(make_ctx(devbox, settings, world)) == 1
    out = capsys.readouterr().out
    assert "DNS query logging is ACTION_NEEDED: ACCESS_DENIED" in out and "Deployed with 1 problem(s)" in out


def test_box_image_size_is_reported_even_when_it_isnt_rebuilt(devbox, settings, world, sandbox, capsys, monkeypatch):
    deploy(devbox, settings, world)
    capsys.readouterr()
    monkeypatch.setattr(devbox, "local_image_size", lambda uri: 2_300_000_000)
    deploy(devbox, settings, world)
    out = capsys.readouterr().out
    assert "2300 MB on disk (AgentCore's image limit is 2 GB)" in out
    box = next(u for u in sandbox["builds"] if "/devbox-box:" in u)
    assert f"docker image ls {box}" in out


def test_a_box_that_is_gone_leaves_the_state(devbox, settings, world, sandbox, visit):
    """An undeploy that stopped half-way leaves .state.json listing boxes that no longer exist: the next deploy lists only
    what the table has (and doesn't move a dead box into it)."""
    devbox.STATE_FILE.write_text(
        json.dumps(
            {
                "account": ACCOUNT,
                "boxes": {
                    "ada": {
                        "name": "ada",
                        "uid": ADA,
                        "generation": 1,
                        "compute": "microvm",
                        "runtimeId": "devbox_vm_ada-gone000000",
                        "runtimeArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/devbox_vm_ada-gone000000",
                    }
                },
            }
        )
    )
    deploy(devbox, settings, world)
    assert json.loads(devbox.STATE_FILE.read_text())["boxes"] == {} and world.tables["devbox-boxes"]["items"] == {}
    visit("grace")
    deploy(devbox, settings, world)
    assert list(json.loads(devbox.STATE_FILE.read_text())["boxes"]) == [box_name(devbox, "grace")]


def test_undeploy_leaves_only_what_agentcores_network_interface_holds(devbox, settings, world, sandbox, visit, capsys):
    """AgentCore's network interface outlives the runtimes by up to 8 hours, and nothing can remove it sooner. undeploy
    doesn't wait: it deletes everything else, leaves the VPC, the box subnet and devbox-box, and says so. A deploy before
    AWS removes the interface reuses them; an undeploy after it deletes them."""
    deploy(devbox, settings, world)
    visit("ada")
    vpc_id = next(iter(world.vpcs))
    box_subnet = next(s for s in world.subnets.values() if name_of(s) == "devbox-box")["SubnetId"]
    world.enis["eni-0agentcore"] = {
        "NetworkInterfaceId": "eni-0agentcore",
        "InterfaceType": "agentic_ai",
        "VpcId": vpc_id,
        "SubnetId": box_subnet,
        "Groups": [{"GroupId": group_id(world, "devbox-box"), "GroupName": "devbox-box"}],
        "Status": "in-use",
    }
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=True, yes=True) == 0
    out = capsys.readouterr().out
    assert "Left for AWS to finish: VPC" in out and "eni-0agentcore" in out
    assert list(world.vpcs) == [vpc_id] and [s["SubnetId"] for s in world.subnets.values()] == [box_subnet]
    assert {g["GroupName"] for g in world.sgs.values()} == {"devbox-box"}
    assert world.igws == {} and world.efs_fs == {} and world.runtimes == {} and world.tables == {} and world.roles == {}
    state = json.loads(devbox.STATE_FILE.read_text())
    assert set(state) == {"account", "lastWorkbenchDomain"}, "only what the next deploy's Okta note needs"
    # a deploy now reuses the leftovers
    n = len(world.log)
    deploy(devbox, settings, world)
    assert "CreateVpc" not in [c[1] for c in world.log[n:]] and devbox.Report.problems == 0
    assert "The workbench domain changed from" in capsys.readouterr().out
    # once AWS has removed the interface, undeploy deletes the rest
    world.enis.clear()
    devbox.Report.reset()
    assert devbox.cmd_undeploy(make_ctx(devbox, settings, world), delete_volumes=True, yes=True) == 0
    assert world.vpcs == {} and world.subnets == {} and world.sgs == {} and not devbox.STATE_FILE.exists()
