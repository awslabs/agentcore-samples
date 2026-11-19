#!/usr/bin/env python3
"""Seed the Users table with the sample user, user-001, whose profile the chat
assistant's get_user_profile tool reads. Rerunning overwrites the same row.

Usage: python3 scripts/seed_dynamodb.py --region us-west-2
"""

import argparse

import boto3

SAMPLE_USERS = [
    {
        "userId": "user-001",
        "displayName": "Alex Rivera",
        "costCenter": "ENG-1234",
        "defaultCategory": "Meals & Entertainment",
        "currency": "USD",
        "reimbursementPolicy": "Meals under $75/day reimbursable; alcohol not reimbursable.",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--table", default="ReceiptsAgent-Users")
    args = parser.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    for user in SAMPLE_USERS:
        table.put_item(Item=user)
        print(f"seeded user {user['userId']} ({user['displayName']})")
    print("done.")


if __name__ == "__main__":
    main()
