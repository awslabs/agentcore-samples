"""Wire a selected Kubernetes-owned NLB to the CDK-owned private DNS zone.

Use the exact NLB ARN belonging to your Service, never a first name-match.
Only the new DNS record and any new ingress rule are owned by this helper.
"""

import argparse
import json
import re
import uuid

import boto3
import resource_gateway_sg
from botocore.exceptions import ClientError
from lifecycle import (
    STATE_DIR,
    account_for,
    begin,
    complete,
    load,
    record_path,
    require_reconciled,
    reserve,
    save,
    verified_session,
)


def select_security_group(lb, requested=None):
    groups = lb.get("SecurityGroups", [])
    if requested:
        if requested not in groups:
            raise ValueError("Selected security group is not attached to this NLB")
        return requested
    if len(groups) > 1:
        raise ValueError(
            "Multiple NLB groups: supply --security-group-id for the owned frontend group, not a shared backend group"
        )
    return groups[0] if groups else None


def apply(ec2, elb, dns, args, record, path):
    if any(
        record.get(key)
        for key in (
            "resourceGatewayGroupName",
            "resourceGatewayGroupIntent",
            "resourceGatewayGroupBinding",
        )
    ):
        raise RuntimeError(
            "Endpoint child acquisition already started; identities cannot be overwritten"
        )
    lb = elb.describe_load_balancers(LoadBalancerArns=[args.nlb_arn])["LoadBalancers"][
        0
    ]
    if (
        lb["VpcId"] != args.vpc_id
        or lb["Type"] != "network"
        or lb["Scheme"] != "internal"
    ):
        raise ValueError("Expected the selected internal NLB in the lab VPC")
    sg = select_security_group(lb, getattr(args, "security_group_id", None))
    zone = dns.get_hosted_zone(Id=args.zone_id)
    if not zone["HostedZone"]["Config"]["PrivateZone"] or args.vpc_id not in [
        v["VPCId"] for v in zone["VPCs"]
    ]:
        raise ValueError("Private hosted zone must belong to the lab VPC")
    if record.get("vpcId", args.vpc_id) != args.vpc_id:
        raise RuntimeError("Endpoint VPC identity cannot change")
    record.update(
        vpcId=args.vpc_id,
        nlbArn=args.nlb_arn,
        securityGroupId=sg,
        zoneId=args.zone_id,
        recordSet={
            "Name": args.domain,
            "Type": "A",
            "AliasTarget": {
                "DNSName": lb["DNSName"],
                "HostedZoneId": lb["CanonicalHostedZoneId"],
                "EvaluateTargetHealth": False,
            },
        },
    )
    save(path, record)
    # CREATE fails rather than overwriting an existing record.
    begin(record, path, "create_dns_alias")
    change = dns.change_resource_record_sets(
        HostedZoneId=args.zone_id,
        ChangeBatch={
            "Changes": [{"Action": "CREATE", "ResourceRecordSet": record["recordSet"]}]
        },
    )
    complete(record, path, aliasCreated=True, changeId=change["ChangeInfo"]["Id"])
    dns.get_waiter("resource_record_sets_changed").wait(
        Id=record["changeId"], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
    )
    cidr = ec2.describe_vpcs(VpcIds=[args.vpc_id])["Vpcs"][0]["CidrBlock"]
    if sg is None:
        # NLB has no group, but Resource Gateway ENIs still require HTTPS
        # egress. The prerequisite default SG is intentionally restricted.
        group = create_bound_group(ec2, record, path, cidr)
        complete(
            record,
            path,
            securityGroupId=group["securityGroupId"],
            ruleIds=[],
            status="CREATED",
        )
        return
    begin(record, path, "authorize_security_group_ingress")
    try:
        result = ec2.authorize_security_group_ingress(
            GroupId=sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": cidr}],
                }
            ],
        )
        record["ruleIds"] = [
            rule["SecurityGroupRuleId"] for rule in result["SecurityGroupRules"]
        ]
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        record[
            "ruleIds"
        ] = []  # Existing rule belongs to someone else; never delete it.
    complete(record, path, status="CREATED")


def parent_identity(record):
    if not record.get("operationId") or not record.get("name"):
        raise ValueError(
            "Missing endpoint generation; reconcile rather than adopt a child by name"
        )
    return {
        "kind": "eks-endpoint",
        "name": record["name"],
        "operationId": record["operationId"],
    }


def bind_group_id(record, path, group_id):
    binding = record["resourceGatewayGroupBinding"]
    if binding.get("securityGroupId", group_id) != group_id or record.get(
        "securityGroupId"
    ) not in (None, group_id):
        raise RuntimeError("Endpoint child ID is immutable")
    binding["securityGroupId"] = group_id
    record["securityGroupId"] = group_id
    save(path, record)


def create_bound_group(ec2, record, path, cidr):
    parent = parent_identity(record)
    if any(
        record.get(key)
        for key in (
            "resourceGatewayGroupName",
            "resourceGatewayGroupIntent",
            "resourceGatewayGroupBinding",
        )
    ):
        raise RuntimeError(
            "Child acquisition already started; do not overwrite its generation"
        )
    intent = {
        "name": record["name"],
        "parentOperationId": parent["operationId"],
        "ownerToken": str(uuid.uuid4()),
        "vpcId": record["vpcId"],
        "accountId": record["accountId"],
        "region": record["region"],
        "destinationCidr": cidr,
    }
    # An intent is NOT ownership. It permits recovery only if a newly reserved
    # child has this exact token AND parent generation. Existing records fail.
    record["resourceGatewayGroupIntent"] = intent
    save(path, record)
    child, child_path = resource_gateway_sg.reserve_group(
        name=intent["name"],
        vpc_id=intent["vpcId"],
        destination_cidr=cidr,
        account_id=intent["accountId"],
        region=intent["region"],
        owner_token=intent["ownerToken"],
        parent_binding=parent,
    )
    record["resourceGatewayGroupBinding"] = dict(intent)
    record["resourceGatewayGroupName"] = intent["name"]
    save(path, record)  # Child reservation is durable before any EC2 creation.
    return resource_gateway_sg.provision_group(
        ec2,
        child,
        child_path,
        on_created=lambda group_id: bind_group_id(record, path, group_id),
    )


def bound_group_for_cleanup(ec2, record, path):
    parent = parent_identity(record)
    intent = record.get("resourceGatewayGroupIntent")
    if (
        not isinstance(intent, dict)
        or intent.get("parentOperationId") != parent["operationId"]
    ):
        raise RuntimeError(
            "Missing/stale child binding intent; no cleanup by child name"
        )
    if not intent.get("ownerToken") or intent.get("name") != parent["name"]:
        raise RuntimeError("Child binding identity mismatch")
    for key in ("vpcId", "accountId", "region"):
        if not record.get(key) or intent.get(key) != record[key]:
            raise RuntimeError("Child binding context mismatch")
    binding = record.get("resourceGatewayGroupBinding")
    if binding is not None and any(
        binding.get(key) != value for key, value in intent.items()
    ):
        raise RuntimeError("Child binding generation is immutable")
    if record.get("resourceGatewayGroupName", intent["name"]) != intent["name"]:
        raise RuntimeError("Child binding name mismatch")
    child_path = record_path("resource-gateway-sg", intent["name"])
    child = load(child_path)
    if child.get("parentBinding") != parent or any(
        child.get(key) != intent[key]
        for key in (
            "name",
            "ownerToken",
            "vpcId",
            "accountId",
            "region",
            "destinationCidr",
        )
    ):
        raise RuntimeError(
            "Child binding mismatch; existing/stale child is not owned by this endpoint"
        )
    group_id = child.get("securityGroupId")
    if not group_id:
        raise ValueError(
            "Bound child has no recorded EC2 ID; reconcile reserved/uncertain creation before cleanup"
        )
    if (binding or {}).get("securityGroupId", group_id) != group_id or record.get(
        "securityGroupId"
    ) not in (None, group_id):
        raise RuntimeError("Child binding ID mismatch")
    resource_gateway_sg.require_unused(child)
    # Before ANY DNS/SG cleanup verify the actual ID/tag/VPC (or its absence).
    try:
        resource_gateway_sg.owned_group(ec2, child)
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidGroup.NotFound":
            raise
    # Recovery after child reservation/ID journaling but interrupted parent save:
    # complete only the missing binding, never overwrite a differing generation/ID.
    if binding is None:
        record["resourceGatewayGroupBinding"] = dict(intent)
        record["resourceGatewayGroupName"] = intent["name"]
    if record["resourceGatewayGroupBinding"].get("securityGroupId") is None:
        bind_group_id(record, path, group_id)
    return child, child_path, parent


def alias_at_name(dns, record):
    result = dns.list_resource_record_sets(
        HostedZoneId=record["zoneId"],
        StartRecordName=record["recordSet"]["Name"],
        StartRecordType="A",
        MaxItems="1",
    )
    for value in result["ResourceRecordSets"]:
        if (
            value["Name"].rstrip(".").lower()
            == record["recordSet"]["Name"].rstrip(".").lower()
            and value["Type"] == "A"
        ):
            return value
    return None


def normalized_record(value):
    result = json.loads(json.dumps(value))
    result["Name"] = result["Name"].rstrip(".").lower()
    if "AliasTarget" in result:
        result["AliasTarget"]["DNSName"] = (
            result["AliasTarget"]["DNSName"].rstrip(".").lower()
        )
    return result


def remove_alias(dns, record, path):
    if record.get("dnsDeleteChangeId"):
        # Retry the exact accepted change, not another DELETE by reusable name.
        dns.get_waiter("resource_record_sets_changed").wait(
            Id=record["dnsDeleteChangeId"],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        )
        if alias_at_name(dns, record) is not None:
            raise RuntimeError(
                "Alias exists after recorded deletion; do not delete a possible replacement"
            )
    else:
        current = alias_at_name(dns, record)
        if current is not None:
            if record.get("dnsDeleteIntent"):
                raise RuntimeError(
                    "Uncertain DNS delete response with alias still present; reconcile the original change"
                )
            if normalized_record(current) != normalized_record(record["recordSet"]):
                raise RuntimeError(
                    "DNS record differs from owned alias; refusing replacement deletion"
                )
            record["dnsDeleteIntent"] = True
            save(path, record)
            change = dns.change_resource_record_sets(
                HostedZoneId=record["zoneId"],
                ChangeBatch={
                    "Changes": [
                        {"Action": "DELETE", "ResourceRecordSet": record["recordSet"]}
                    ]
                },
            )
            record["dnsDeleteChangeId"] = change["ChangeInfo"]["Id"]
            save(path, record)
            dns.get_waiter("resource_record_sets_changed").wait(
                Id=record["dnsDeleteChangeId"],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            )
            if alias_at_name(dns, record) is not None:
                raise RuntimeError(
                    "DNS alias absence not confirmed; retain the deletion journal"
                )
    record["aliasCreated"] = False
    save(path, record)


def remove(ec2, dns, record, path):
    if record.get("status") == "DELETED":
        return  # Never inspect/adopt a later child under a reused name.
    require_reconciled(record)
    group = None
    if any(
        record.get(key)
        for key in (
            "resourceGatewayGroupName",
            "resourceGatewayGroupIntent",
            "resourceGatewayGroupBinding",
        )
    ):
        group, group_path, parent = bound_group_for_cleanup(ec2, record, path)
    for target_path in STATE_DIR.glob("target-*.json"):
        target = load(target_path)
        if (
            record.get("recordSet", {}).get("Name", "\0")
            in json.dumps(target.get("request", {}))
            and target.get("status") != "DELETED"
        ):
            raise RuntimeError(f"Delete target first: {target_path}")
    if record.get("aliasCreated"):
        remove_alias(dns, record, path)
    if group is not None:
        resource_gateway_sg.delete_group(ec2, group, group_path, parent_binding=parent)
    if record.get("ruleIds"):
        ec2.revoke_security_group_ingress(
            GroupId=record["securityGroupId"], SecurityGroupRuleIds=record["ruleIds"]
        )
        record["ruleIds"] = []
    record["status"] = "DELETED"
    save(path, record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--nlb-arn")
    parser.add_argument("--zone-id")
    parser.add_argument("--domain")
    parser.add_argument("--vpc-id")
    parser.add_argument(
        "--security-group-id",
        help="Explicit frontend SG when the NLB has multiple attached groups",
    )
    args = parser.parse_args(argv)
    path = record_path("eks-endpoint", args.name)
    if args.delete:
        record = load(path)
        if record.get("status") == "DELETED":
            print(
                "This endpoint generation was already deleted; no child-name lookup performed."
            )
            return
        session = verified_session(record)
    else:
        if (
            not re.fullmatch(
                r"arn:[^:]+:elasticloadbalancing:[^:]+:\d{12}:loadbalancer/net/[^/*?]+/[a-z0-9]+",
                args.nlb_arn or "",
            )
            or not re.fullmatch(r"Z[A-Z0-9]+", args.zone_id or "")
            or not re.fullmatch(r"[A-Za-z0-9.-]+", args.domain or "")
            or not re.fullmatch(r"vpc-[0-9a-f]+", args.vpc_id or "")
        ):
            parser.error("Provide exact NLB ARN, private zone ID, domain and VPC ID")
        if path.exists():
            parser.error(f"Record exists: {path}; reconcile before retrying")
        session = boto3.Session()
        record = {
            "name": args.name,
            "operationId": str(uuid.uuid4()),
            "region": session.region_name,
            "accountId": account_for(session),
            "status": "CREATE_PENDING",
        }
        reserve(path, record)
    ec2, dns = session.client("ec2"), session.client("route53")
    if args.delete:
        remove(ec2, dns, record, path)
    else:
        apply(ec2, session.client("elbv2"), dns, args, record, path)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
