"""Identify the existing VPC NAT EIP for one private subnet; creates nothing."""

import argparse
import json
import re

import boto3


def identify(ec2, subnet_id):
    tables = ec2.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )["RouteTables"]
    nats = {
        route["NatGatewayId"]
        for table in tables
        for route in table["Routes"]
        if route.get("DestinationCidrBlock") == "0.0.0.0/0"
        and route.get("NatGatewayId")
        and route.get("State") == "active"
    }
    if len(nats) != 1:
        raise ValueError(
            "Expected exactly one active NAT default route for this lab subnet"
        )
    nat_id = nats.pop()
    nat = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]
    ips = [
        address["PublicIp"]
        for address in nat["NatGatewayAddresses"]
        if address.get("PublicIp")
    ]
    if nat["State"] != "available" or len(ips) != 1:
        raise ValueError("Expected an available NAT with exactly one public IP")
    return {"subnetId": subnet_id, "natGatewayId": nat_id, "elasticIp": ips[0]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subnet-id", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"subnet-[0-9a-f]+", args.subnet_id):
        parser.error("Invalid subnet ID")
    print(json.dumps(identify(boto3.Session().client("ec2"), args.subnet_id), indent=2))


if __name__ == "__main__":
    main()
