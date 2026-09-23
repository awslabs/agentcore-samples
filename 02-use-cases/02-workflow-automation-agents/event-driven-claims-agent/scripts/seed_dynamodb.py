#!/usr/bin/env python3
"""Seed DynamoDB tables with sample data."""

import argparse

import boto3


def get_table_name(region, stack_name="AgentCore-ClaimsAgent-dev", logical_suffix="Policies"):
    """Discover the actual DynamoDB table name from the deployed stack.

    Falls back to the hardcoded convention if the stack isn't found.
    """
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.list_stack_resources(StackName=stack_name)
        for r in resp.get("StackResourceSummaries", []):
            if r["ResourceType"] == "AWS::DynamoDB::Table" and logical_suffix in r["LogicalResourceId"]:
                return r["PhysicalResourceId"]
    except Exception:
        pass
    # Fallback: try common naming patterns
    dynamodb = boto3.client("dynamodb", region_name=region)
    try:
        tables = dynamodb.list_tables()["TableNames"]
        for t in tables:
            if "Policies" in t and "Claims" in t.lower():
                return t
    except Exception:
        pass
    return "ClaimsAgent-Policies"


def seed_policies(region):
    table_name = get_table_name(region)
    print(f"  Table: {table_name}")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    policies = [
        {
            "policy_number": "POL-12345",
            "holder_name": "John Smith",
            "email": "john.smith@example.com",
            "policy_type": "auto",
            "coverage_amount": 50000,
            "deductible": 500,
            "status": "active",
            "vehicle": {"make": "Toyota", "model": "Camry", "year": 2023},
        },
        {
            "policy_number": "POL-67890",
            "holder_name": "Jane Doe",
            "email": "jane.doe@example.com",
            "policy_type": "home",
            "coverage_amount": 250000,
            "deductible": 1000,
            "status": "active",
            "property": {"address": "123 Main St, Springfield, IL", "type": "single_family"},
        },
        {
            "policy_number": "POL-11111",
            "holder_name": "Bob Johnson",
            "email": "bob.j@example.com",
            "policy_type": "auto",
            "coverage_amount": 75000,
            "deductible": 250,
            "status": "active",
            "vehicle": {"make": "Honda", "model": "Civic", "year": 2024},
        },
        {
            "policy_number": "POL-99999",
            "holder_name": "Alice Williams",
            "email": "alice.w@example.com",
            "policy_type": "auto",
            "coverage_amount": 100000,
            "deductible": 1000,
            "status": "expired",
            "vehicle": {"make": "BMW", "model": "X5", "year": 2022},
        },
    ]
    for p in policies:
        table.put_item(Item=p)
        print(f"  ✓ {p['policy_number']} ({p['holder_name']})")
    print(f"✅ Seeded {len(policies)} policies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    print(f"🌱 Seeding DynamoDB in {args.region}...")
    seed_policies(args.region)
