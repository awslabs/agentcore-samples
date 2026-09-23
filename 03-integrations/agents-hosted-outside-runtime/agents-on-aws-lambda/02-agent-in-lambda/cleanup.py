"""
Delete all AWS resources created by deploy.py.

Usage:
    python cleanup.py
"""

import json
import sys

import boto3

# ── Load config ────────────────────────────────────────────────────────────────

try:
    with open("lambda_config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    print("ERROR: lambda_config.json not found. Nothing to clean up.")
    sys.exit(0)

FUNCTION_NAME = config["function_name"]
ROLE_NAME = config["role_name"]
REGION = config["region"]

lam = boto3.client("lambda", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)


def delete_lambda():
    try:
        lam.delete_function(FunctionName=FUNCTION_NAME)
        print(f"  Deleted Lambda function: {FUNCTION_NAME}")
    except lam.exceptions.ResourceNotFoundException:
        print(f"  Lambda function not found (already deleted?): {FUNCTION_NAME}")
    except Exception as e:
        print(f"  Could not delete Lambda: {e}")


def delete_iam_role():
    # Detach managed policies first
    try:
        attached = iam.list_attached_role_policies(RoleName=ROLE_NAME)["AttachedPolicies"]
        for p in attached:
            iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn=p["PolicyArn"])
            print(f"  Detached: {p['PolicyName']}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM role not found (already deleted?): {ROLE_NAME}")
        return
    except Exception as e:
        print(f"  Error detaching policies: {e}")

    # Delete inline policies
    try:
        inline = iam.list_role_policies(RoleName=ROLE_NAME)["PolicyNames"]
        for name in inline:
            iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=name)
            print(f"  Deleted inline policy: {name}")
    except Exception as e:
        print(f"  Error deleting inline policies: {e}")

    try:
        iam.delete_role(RoleName=ROLE_NAME)
        print(f"  Deleted IAM role: {ROLE_NAME}")
    except Exception as e:
        print(f"  Could not delete IAM role: {e}")


def main():
    print("=" * 60)
    print(f"Cleaning up resources for: {FUNCTION_NAME}")
    print("=" * 60)

    delete_lambda()
    delete_iam_role()

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
