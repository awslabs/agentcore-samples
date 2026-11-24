"""Own a Resource Gateway + SINGLE Resource Configuration via SDK, not CDK.

Run with the resource owner's AWS profile (Account B for the cross-account lab).
Delete targets and RAM shares first. All waits are bounded and IDs are durable.
"""
import argparse
import re
import time

import boto3
from botocore.exceptions import ClientError

from lifecycle import account_for, begin, complete, load, record_path, require_reconciled, reserve, save, verified_session


def wait(client, kind, identifier, *, deleted=False):
    get = getattr(client, f"get_{kind}")
    key = "resourceGatewayIdentifier" if kind == "resource_gateway" else "resourceConfigurationIdentifier"
    for attempt in range(30):
        try:
            status = get(**{key: identifier})["status"]
        except ClientError as error:
            if deleted and error.response["Error"]["Code"] == "ResourceNotFoundException":
                return
            raise
        if not deleted and status == "ACTIVE":
            return
        if "FAILED" in status:
            raise RuntimeError(f"{kind} {identifier}: {status}")
        if attempt < 29:
            time.sleep(10)
    raise TimeoutError(f"{kind} {identifier}: wait timed out")


def delete_resource(client, kind, identifier):
    key = "resourceGatewayIdentifier" if kind == "resource_gateway" else "resourceConfigurationIdentifier"
    for attempt in range(30):
        try:
            getattr(client, f"delete_{kind}")(**{key: identifier})
            break
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                break
            if code != "ConflictException" or attempt == 29:
                raise
            time.sleep(10)  # AgentCore may still be removing its association.
    wait(client, kind, identifier, deleted=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-ids")
    parser.add_argument("--security-group-ids")
    parser.add_argument("--dns-name")
    args = parser.parse_args(argv)
    path = record_path("lattice", args.name)
    if args.delete:
        record = load(path)
        require_reconciled(record)
        session = verified_session(record)
    else:
        if not re.fullmatch(r"vpc-[0-9a-f]+", args.vpc_id or ""):
            parser.error("Provide --vpc-id")
        if not re.fullmatch(r"subnet-[0-9a-f]+(?:,subnet-[0-9a-f]+)*", args.subnet_ids or ""):
            parser.error("Provide nonempty subnet IDs")
        if not re.fullmatch(r"sg-[0-9a-f]+(?:,sg-[0-9a-f]+)*", args.security_group_ids or ""):
            parser.error("Provide nonempty security group IDs")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", args.dns_name or ""):
            parser.error("Provide --dns-name, not a URL")
        if path.exists():
            parser.error(f"Record exists: {path}; reconcile it rather than adopting resources")
        session = boto3.Session()
        record = {"name": args.name, "region": session.region_name, "accountId": account_for(session), "status": "CREATE_PENDING"}
        reserve(path, record)
    client = session.client("vpc-lattice")
    if args.delete:
        if record.get("resourceConfigurationArn"):
            delete_resource(client, "resource_configuration", record["resourceConfigurationArn"])
            record["configurationDeleted"] = True
            save(path, record)
        if record.get("resourceGatewayId"):
            delete_resource(client, "resource_gateway", record["resourceGatewayId"])
        record["status"] = "DELETED"
        save(path, record)
    else:
        begin(record, path, 'create_resource_gateway')
        gateway = client.create_resource_gateway(name=f"{args.name}-rg", vpcIdentifier=args.vpc_id,
            subnetIds=args.subnet_ids.split(","), securityGroupIds=args.security_group_ids.split(","), ipAddressType="IPV4")
        complete(record, path, resourceGatewayId=gateway["id"])
        wait(client, "resource_gateway", gateway["id"])
        begin(record, path, 'create_resource_configuration')
        config = client.create_resource_configuration(name=f"{args.name}-rc", type="SINGLE",
            resourceGatewayIdentifier=gateway["id"], portRanges=["443"],
            resourceConfigurationDefinition={"dnsResource": {"domainName": args.dns_name, "ipAddressType": "IPV4"}})
        complete(record, path, resourceConfigurationArn=config["arn"])
        wait(client, "resource_configuration", config["arn"])
        record["status"] = "ACTIVE"
        save(path, record)
        print(f"Resource Configuration ARN: {config['arn']}")
    print(f"Keep ownership record: {path}")


if __name__ == "__main__":
    main()
