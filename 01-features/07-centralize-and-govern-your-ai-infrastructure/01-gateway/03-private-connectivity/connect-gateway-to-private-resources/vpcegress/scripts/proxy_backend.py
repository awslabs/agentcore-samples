"""SDK-owned internal HTTPS proxy from the original private-CA/self-signed labs.

This replaces obsolete PublicCertProxy CDK commands, not the backend architecture.
Keep the ownership record after failures; --delete resumes recorded partial work.
"""

import argparse
import ipaddress
import json
import re
import time

import boto3
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


def delete_owned(ec2, elb, record, path):
    require_reconciled(record)
    for target_path in STATE_DIR.glob("target-*.json"):
        target = load(target_path)
        if record.get("dnsName") in (None, ""):
            continue
        if (
            record["dnsName"] in json.dumps(target.get("request", {}))
            and target.get("status") != "DELETED"
        ):
            raise RuntimeError(f"Delete target first: {target_path}")
    if record.get("loadBalancerArn"):
        arn = record["loadBalancerArn"]
        try:
            elb.delete_load_balancer(LoadBalancerArn=arn)
        except ClientError as error:
            if error.response["Error"]["Code"] != "LoadBalancerNotFound":
                raise
        elb.get_waiter("load_balancers_deleted").wait(
            LoadBalancerArns=[arn], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
        )
        del record["loadBalancerArn"]
        save(path, record)
    if record.get("targetGroupArn"):
        try:
            elb.delete_target_group(TargetGroupArn=record["targetGroupArn"])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TargetGroupNotFound":
                raise
        del record["targetGroupArn"]
        save(path, record)
    if record.get("securityGroupId"):
        for attempt in range(30):
            try:
                ec2.delete_security_group(GroupId=record["securityGroupId"])
                break
            except ClientError as error:
                code = error.response["Error"]["Code"]
                if code == "InvalidGroup.NotFound":
                    break
                if code != "DependencyViolation" or attempt == 29:
                    raise
                time.sleep(10)
        del record["securityGroupId"]
    record["status"] = "DELETED"
    save(path, record)


def create_owned(ec2, elb, record, path, args):
    cidr = ec2.describe_vpcs(VpcIds=[args.vpc_id])["Vpcs"][0]["CidrBlock"]
    if ipaddress.ip_address(args.backend_ip) not in ipaddress.ip_network(cidr):
        raise ValueError("Backend IP must be inside the selected lab VPC")
    begin(record, path, "create_security_group")
    sg = ec2.create_security_group(
        GroupName=f"{args.name}-sg",
        Description="Lab internal HTTPS proxy",
        VpcId=args.vpc_id,
    )
    complete(record, path, securityGroupId=sg["GroupId"])
    ec2.authorize_security_group_ingress(
        GroupId=record["securityGroupId"],
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": cidr}],
            }
        ],
    )
    begin(record, path, "create_load_balancer")
    alb = elb.create_load_balancer(
        Name=args.name,
        Subnets=args.subnet_ids.split(","),
        SecurityGroups=[record["securityGroupId"]],
        Scheme="internal",
        Type="application",
        IpAddressType="ipv4",
    )["LoadBalancers"][0]
    complete(
        record, path, loadBalancerArn=alb["LoadBalancerArn"], dnsName=alb["DNSName"]
    )
    elb.get_waiter("load_balancer_available").wait(
        LoadBalancerArns=[record["loadBalancerArn"]],
        WaiterConfig={"Delay": 10, "MaxAttempts": 30},
    )
    begin(record, path, "create_target_group")
    group = elb.create_target_group(
        Name=f"{args.name}-tg",
        Protocol="HTTPS",
        Port=443,
        VpcId=args.vpc_id,
        TargetType="ip",
        HealthCheckProtocol="HTTPS",
        HealthCheckPort="443",
        HealthCheckPath="/health",
    )["TargetGroups"][0]
    complete(record, path, targetGroupArn=group["TargetGroupArn"])
    elb.register_targets(
        TargetGroupArn=record["targetGroupArn"],
        Targets=[{"Id": args.backend_ip, "Port": 443}],
    )
    actions = [{"Type": "forward", "TargetGroupArn": record["targetGroupArn"]}]
    listener = elb.create_listener(
        LoadBalancerArn=record["loadBalancerArn"],
        Protocol="HTTPS",
        Port=443,
        Certificates=[{"CertificateArn": args.certificate_arn}],
        DefaultActions=actions,
    )["Listeners"][0]
    record["listenerArn"] = listener["ListenerArn"]
    save(path, record)
    rule = elb.create_rule(
        ListenerArn=record["listenerArn"],
        Priority=1,
        Conditions=[{"Field": "path-pattern", "PathPatternConfig": {"Values": ["/*"]}}],
        Actions=actions,
        Transforms=[
            {
                "Type": "host-header-rewrite",
                "HostHeaderRewriteConfig": {
                    "Rewrites": [{"Regex": ".*", "Replace": args.backend_domain}]
                },
            }
        ],
    )["Rules"][0]
    record.update(ruleArn=rule["RuleArn"], status="CREATED")
    save(path, record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name", required=True, help="Unique ALB name, at most 29 characters"
    )
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-ids")
    parser.add_argument("--backend-ip")
    parser.add_argument("--backend-domain")
    parser.add_argument("--certificate-arn")
    args = parser.parse_args(argv)
    path = record_path("proxy", args.name)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,28}", args.name):
        parser.error("Invalid ALB name")
    if args.delete:
        record = load(path)
        session = verified_session(record)
    else:
        if (
            not re.fullmatch(r"vpc-[0-9a-f]+", args.vpc_id or "")
            or not re.fullmatch(
                r"subnet-[0-9a-f]+(?:,subnet-[0-9a-f]+)+", args.subnet_ids or ""
            )
            or not re.fullmatch(r"[A-Za-z0-9.-]+", args.backend_domain or "")
            or not re.fullmatch(
                r"arn:[^:]+:acm:[^:]+:\d{12}:certificate/[0-9a-f-]+",
                args.certificate_arn or "",
            )
        ):
            parser.error(
                "Provide VPC, at least two private subnets, backend domain and public certificate ARN"
            )
        try:
            if not ipaddress.ip_address(args.backend_ip).is_private:
                raise ValueError("Backend must be private")
        except ValueError as error:
            parser.error(str(error))
        if path.exists():
            parser.error(
                f"Record exists: {path}; reconcile or delete before reusing name"
            )
        session = boto3.Session()
        record = {
            "name": args.name,
            "region": session.region_name,
            "accountId": account_for(session),
            "status": "CREATE_PENDING",
        }
        reserve(path, record)
    ec2, elb = session.client("ec2"), session.client("elbv2")
    if args.delete:
        delete_owned(ec2, elb, record, path)
    else:
        create_owned(ec2, elb, record, path, args)
    print(json.dumps(record, indent=2))
    print(f"Ownership record: {path}")


if __name__ == "__main__":
    main()
