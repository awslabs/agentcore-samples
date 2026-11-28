#!/usr/bin/env python3
"""Seed DynamoDB tables with sample data."""

import argparse

from cloud_checks import context


def seed_policies():
    state, session, _ = context()
    table_name = f"{state['project']}-{state['target']}-Policies"
    print(f"  Table: {table_name}")
    table = session.resource("dynamodb").Table(table_name)
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
        table.put_item(Item=p, ConditionExpression="attribute_not_exists(policy_number)")
        print(f"  ✓ {p['policy_number']} ({p['holder_name']})")
    print(f"✅ Seeded {len(policies)} policies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error("Explicit --allow-cloud required")
    seed_policies()
