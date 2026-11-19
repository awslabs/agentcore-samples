#!/usr/bin/env python
"""Removes everything deploy.py created, in the order the service requires.

Order matters: the CapacityProvider refuses deletion while any runtime version
still references it.

    python cleanup.py                 # asks for confirmation
    python cleanup.py --yes           # no confirmation
    python cleanup.py --keep-network  # preserves subnet/SG/VPC endpoints
    python cleanup.py --keep-iam      # preserves the roles
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".deploy-state.json"
PREFIX = "cpsample"


def log(m: str = "") -> None:
    print(m, flush=True)


def step(m: str) -> None:
    log(f"\n\033[1m── {m}\033[0m")


def ok(m: str) -> None:
    log(f"   ✓ {m}")


def skip(m: str) -> None:
    log(f"   · {m}")


def err(m: str) -> None:
    log(f"   ! {m}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--keep-network", action="store_true")
    ap.add_argument("--keep-iam", action="store_true")
    args = ap.parse_args()

    try:
        st = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        sys.exit("state not found (.deploy-state.json) — nothing to clean up")

    # No fallback region on purpose: guessing one would delete — or fail to find —
    # resources in a region you never deployed to. deploy.py always writes it.
    region = st.get("region")
    if not region:
        sys.exit(f"no region in {STATE_FILE.name} — add the region you deployed to, or delete the file")
    log(f"\033[1mCleanup\033[0m  account {st.get('account')}  region {region}")
    for k in ("cp_id", "runtime_id", "gateway_id", "subnet", "sg_agent"):
        if st.get(k):
            log(f"  {k:12s} {st[k]}")
    if not args.yes:
        if input("\nremove all of this? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("cancelled")

    sess = boto3.Session(region_name=region)
    agentcore = sess.client("bedrock-agentcore-control")
    ec2 = sess.client("ec2")

    # 1) Gateway: target before gateway.
    # Deleting a target is ASYNCHRONOUS — it sits in DELETING for a few seconds
    # and delete_gateway fails with "has targets associated with it" if you do
    # not wait for the list to actually empty out.
    step("1. Gateway")
    if st.get("gateway_id"):
        gid = st["gateway_id"]
        try:
            for t in agentcore.list_gateway_targets(
                    gatewayIdentifier=gid).get("items", []):
                agentcore.delete_gateway_target(gatewayIdentifier=gid,
                                                targetId=t["targetId"])
                ok(f"target removed: {t['targetId']}")
            # wait for the list to actually empty
            for attempt in range(12):
                remaining = agentcore.list_gateway_targets(
                    gatewayIdentifier=gid).get("items", [])
                if not remaining:
                    break
                if attempt == 0:
                    log(f"   waiting for {len(remaining)} target(s) to leave the list…")
                time.sleep(5)
            for attempt in range(6):
                try:
                    agentcore.delete_gateway(gatewayIdentifier=gid)
                    ok(f"gateway removed: {gid}")
                    break
                except ClientError as e:
                    if "targets associated" in e.response["Error"].get("Message", "") \
                            and attempt < 5:
                        time.sleep(10)
                        continue
                    err(f"{e.response['Error']['Code']}: "
                        f"{e.response['Error']['Message'][:120]}")
                    break
        except ClientError as e:
            err(f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:120]}")
    else:
        skip("no gateway in the state")

    # 2) Runtime (omitting ?version cascades to every version)
    step("2. AgentRuntime")
    if st.get("runtime_id"):
        try:
            agentcore.delete_agent_runtime(agentRuntimeId=st["runtime_id"])
            ok(f"runtime removed: {st['runtime_id']}")
            log("   waiting for the CP to release its references…")
            time.sleep(25)
        except ClientError as e:
            err(f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:120]}")
    else:
        skip("no runtime in the state")

    # 3) CapacityProvider — deleting the CP stops and deletes sessions + volumes
    step("3. CapacityProvider")
    if st.get("cp_id"):
        for attempt in range(6):
            try:
                agentcore.delete_capacity_provider(capacityProviderId=st["cp_id"])
                ok(f"CP removed: {st['cp_id']} (stops sessions and deletes volumes)")
                break
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("ValidationException", "ConflictException") and attempt < 5:
                    log(f"   still referenced, waiting… ({attempt+1}/6)")
                    time.sleep(20)
                    continue
                err(f"{code}: {e.response['Error']['Message'][:150]}")
                break
    else:
        skip("no CP in the state")

    # 4) Leftover EC2 (report only — the service is what terminates them)
    step("4. Managed EC2 (report only)")
    try:
        # IncludeManagedResources needs a recent botocore; on an old one it raises
        # ParamValidationError, which is NOT a ClientError and would crash the
        # cleanup. These instances are hidden without it, so skip the check rather
        # than fail. (See deploy.py — the CP's EC2 are "managed resources".)
        kw = {}
        if "IncludeManagedResources" in ec2.meta.service_model.operation_model(
                "DescribeInstances").input_shape.members:
            kw["IncludeManagedResources"] = True
        r = ec2.describe_instances(
            Filters=[{"Name": "tag-key",
                      "Values": ["bedrock-agentcore:capacity-provider-id"]}], **kw)
        alive = [i for res in r["Reservations"] for i in res["Instances"]
                 if i["State"]["Name"] in ("running", "pending")]
        if alive:
            err(f"{len(alive)} instance(s) still up — the service terminates them "
                f"after the idle timeout:")
            for i in alive[:8]:
                log(f"      {i['InstanceId']} {i['InstanceType']} {i['State']['Name']}")
        else:
            ok("no managed instances running")
    except ClientError as e:
        err(e.response["Error"]["Code"])

    # 5) ECR + the build artifacts (CodeBuild project, its bucket)
    step("5. ECR + build")
    try:
        sess.client("ecr").delete_repository(repositoryName=f"{PREFIX}-agent", force=True)
        ok(f"repo removed: {PREFIX}-agent")
    except ClientError as e:
        skip(f"{e.response['Error']['Code']}")
    try:
        sess.client("codebuild").delete_project(name=f"{PREFIX}-image-build")
        ok(f"CodeBuild project removed: {PREFIX}-image-build")
    except ClientError as e:
        skip(f"codebuild: {e.response['Error']['Code']}")
    bucket = f"{PREFIX}-build-{st.get('account')}-{region}"
    try:
        s3 = sess.client("s3")
        objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objs:
            s3.delete_objects(Bucket=bucket,
                              Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
        s3.delete_bucket(Bucket=bucket)
        ok(f"build bucket removed: {bucket}")
    except ClientError as e:
        skip(f"bucket: {e.response['Error']['Code']}")

    # 6) Networking
    if args.keep_network:
        step("6. Networking")
        skip("--keep-network: preserved")
    else:
        step("6. Networking (VPC endpoints bill hourly — removing them is what stops the cost)")
        # Deleting an interface endpoint is ASYNCHRONOUS: it sits in `deleting`
        # and only then releases its ENI. While the ENI exists the SG is "in use"
        # (DependencyViolation) and the subnet cannot be removed. So we wait for
        # the endpoints to actually disappear before moving on.
        try:
            eps = [e["VpcEndpointId"] for e in ec2.describe_vpc_endpoints(
                Filters=[{"Name": "tag:project", "Values": ["cp-sample"]}])["VpcEndpoints"]]
            if eps:
                ec2.delete_vpc_endpoints(VpcEndpointIds=eps)
                ok(f"{len(eps)} endpoint(s) marked for removal")
                log("   waiting for the ENIs to release (can take ~2 min)…")
                for attempt in range(24):
                    remaining = ec2.describe_vpc_endpoints(
                        Filters=[{"Name": "tag:project", "Values": ["cp-sample"]}]
                    )["VpcEndpoints"]
                    alive_eps = [e for e in remaining
                                 if e["State"] not in ("deleted", "deleting")]
                    if not remaining:
                        ok("endpoints removed")
                        break
                    if not alive_eps and attempt % 4 == 3:
                        log(f"   {len(remaining)} still in 'deleting'…")
                    time.sleep(10)
                else:
                    err("endpoints still in 'deleting' — SG/subnet may fail")
            else:
                skip("no endpoint carrying the sample's tag")
        except ClientError as e:
            err(e.response["Error"]["Code"])

        # SGs: the vpce one depends on the agent's (ingress rule), so it goes first.
        # A DependencyViolation here is almost always an endpoint ENI still releasing.
        for name in (f"{PREFIX}-vpce", f"{PREFIX}-noingress"):
            g = ec2.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [name]}])["SecurityGroups"]
            for x in g:
                for attempt in range(8):
                    try:
                        ec2.delete_security_group(GroupId=x["GroupId"])
                        ok(f"SG removed: {name}")
                        break
                    except ClientError as e:
                        code = e.response["Error"]["Code"]
                        if code == "DependencyViolation" and attempt < 7:
                            if attempt == 0:
                                log(f"   {name}: still in use, waiting…")
                            time.sleep(15)
                            continue
                        err(f"{name}: {code}")
                        break

        if st.get("subnet"):
            for attempt in range(10):
                try:
                    ec2.delete_subnet(SubnetId=st["subnet"])
                    ok(f"subnet removed: {st['subnet']}")
                    break
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    if code == "DependencyViolation" and attempt < 9:
                        if attempt == 0:
                            enis = ec2.describe_network_interfaces(Filters=[
                                {"Name": "subnet-id", "Values": [st["subnet"]]}
                            ])["NetworkInterfaces"]
                            log(f"   subnet still has {len(enis)} ENI(s), waiting…")
                        time.sleep(15)
                        continue
                    if code == "InvalidSubnetID.NotFound":
                        ok("subnet no longer exists")
                        break
                    err(f"subnet: {code}")
                    break

    # 7) IAM
    if args.keep_iam:
        step("7. IAM")
        skip("--keep-iam: preserved")
    else:
        step("7. IAM")
        iam = sess.client("iam")

        # Only the three roles deploy.py created. There is no shared instance role
        # to reason about — the capacity provider omits instanceProfileArn and uses
        # the service default, so nothing here is account-global or cross-region.
        for role in (f"{PREFIX}OperatorRole", f"{PREFIX}RuntimeRole",
                     f"{PREFIX}GatewayRole", f"{PREFIX}CodeBuildRole"):
            try:
                for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                    iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
                for p in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                    iam.delete_role_policy(RoleName=role, PolicyName=p)
                iam.delete_role(RoleName=role)
                ok(f"role removed: {role}")
            except ClientError as e:
                skip(f"{role}: {e.response['Error']['Code']}")

    try:
        STATE_FILE.unlink()
        ok(f"\nlocal state removed ({STATE_FILE.name})")
    except OSError:
        pass

    log("\nWhat this script does NOT undo:")
    log("  · Transaction Search / X-Ray sampling (ACCOUNT-level config)")
    log("    to revert: aws xray update-indexing-rule --name Default \\")
    log("                --rule '{\"Probabilistic\":{\"DesiredSamplingPercentage\":0}}'")
    log("  · the /aws/bedrock-agentcore/runtimes/* and aws/spans log groups (they retain data)")
    log("  · the CloudWatch dashboard, if you created one\n")


if __name__ == "__main__":
    main()
