#!/usr/bin/env python3
"""
Cleanup Cognito Resources

Deletes:
- Cognito User Pool domain
- Cognito User Pool (and all users/clients)
- Post-authentication Lambda function
- Post-authentication Lambda IAM role, only when this sample's ownership
  record matches it and it carries only the sample's inline policy
- DynamoDB login audit table
- SSM parameters

Usage:
    python cleanup_cognito.py [--keep-ssm]

Exit code: 0 on success; 1 on any failure; 3 when the only problem was the
post-auth role left in place because this sample cannot prove it created it.
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session
from utils.iam_roles import FAILED, LEFT, OK, combine, delete_owned_role
from utils.notebook_init import pause

POST_AUTH_ROLE_NAME = "lakehouse-cognito-post-auth-role"
# What deploy_post_auth_lambda.sh puts on the role. Delete refuses a role carrying anything else.
POST_AUTH_ROLE_INLINE = {f"{POST_AUTH_ROLE_NAME}-policy"}
POST_AUTH_ROLE_MANAGED: set[str] = set()


class CognitoCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.cognito = boto3.client("cognito-idp", region_name=self.region)
        self.lambda_client = boto3.client("lambda", region_name=self.region)
        self.iam = boto3.client("iam")
        self.dynamodb = boto3.client("dynamodb", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm
        # Failed deletes are collected here; run() prints ✨ only when this stays empty.
        # "Not found" outcomes are not failures.
        self.errors = []
        # OK, FAILED or LEFT from the post-auth role delete.
        self.role_result = OK

    def _get_ssm_param(self, name, default=None):
        # Only an absent parameter means "nothing to delete"; any other read error is raised.
        try:
            return self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            return default

    def delete_user_pool(self):
        print("\n🗑️  Deleting Cognito User Pool...")
        try:
            pool_id = self._get_ssm_param("cognito-user-pool-id")
        except Exception as e:
            print(f"   ❌ Error reading the User Pool id from SSM: {e}")
            self.errors.append(f"User Pool id not read from SSM: {e}")
            return
        if not pool_id:
            print("   ⏭️  No User Pool found in SSM")
            return

        try:
            # Delete domain first
            pool_info = self.cognito.describe_user_pool(UserPoolId=pool_id)
            domain = pool_info["UserPool"].get("Domain")
            if domain:
                self.cognito.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
                print(f"   ✅ Deleted domain: {domain}")
                pause("Cognito domain delete settling", 3)

            self.cognito.delete_user_pool(UserPoolId=pool_id)
            print(f"   ✅ Deleted User Pool: {pool_id}")
        except self.cognito.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  User Pool not found: {pool_id}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            self.errors.append(f"User Pool {pool_id}: {e}")

    def delete_post_auth_lambda(self):
        print("\n🗑️  Deleting post-auth Lambda...")
        func_name = "lakehouse-cognito-post-auth"
        try:
            self.lambda_client.delete_function(FunctionName=func_name)
            print(f"   ✅ Deleted Lambda: {func_name}")
        except self.lambda_client.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Lambda not found: {func_name}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            self.errors.append(f"Lambda {func_name}: {e}")

    def delete_post_auth_role(self):
        print("\n🗑️  Deleting post-auth Lambda role...")
        self.role_result = delete_owned_role(
            self.iam, self.ssm, POST_AUTH_ROLE_NAME, POST_AUTH_ROLE_INLINE, POST_AUTH_ROLE_MANAGED
        )
        if self.role_result == FAILED:
            self.errors.append(f"role {POST_AUTH_ROLE_NAME}: not deleted (see above)")

    def delete_audit_table(self):
        print("\n🗑️  Deleting login audit DynamoDB table...")
        try:
            self.dynamodb.delete_table(TableName="lakehouse_user_login_audit")
            print("   ✅ Deleted table: lakehouse_user_login_audit")
        except self.dynamodb.exceptions.ResourceNotFoundException:
            print("   ⏭️  Table not found")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            self.errors.append(f"table lakehouse_user_login_audit: {e}")

    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        params = [
            "cognito-user-pool-id",
            "cognito-user-pool-arn",
            "cognito-app-client-id",
            "cognito-app-client-secret",
            "cognito-m2m-client-id",
            "cognito-m2m-client-secret",
            "cognito-domain",
            "cognito-resource-server-id",
            "cognito-region",
        ]
        for p in params:
            try:
                self.ssm.delete_parameter(Name=f"/app/lakehouse-agent/{p}")
                print(f"   ✅ Deleted: /app/lakehouse-agent/{p}")
            except self.ssm.exceptions.ParameterNotFound:
                pass
            except Exception as e:
                print(f"   ❌ Error: {e}")
                self.errors.append(f"SSM /app/lakehouse-agent/{p}: {e}")

    def run(self):
        print("\n🧹 Cognito Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        self.delete_user_pool()
        self.delete_post_auth_lambda()
        self.delete_post_auth_role()
        self.delete_audit_table()
        self.delete_ssm_parameters()
        rc = combine([FAILED if self.errors else OK, self.role_result])
        if self.errors:
            print(f"\n❌ cleanup_cognito.py finished with {len(self.errors)} error(s):")
            for err in self.errors:
                print(f"   • {err}")
            sys.exit(rc)
        if rc == LEFT:
            print(f"\n⚠  Cognito cleanup finished, but IAM role {POST_AUTH_ROLE_NAME} was left in place (see above).")
            sys.exit(rc)
        print("\n✨ Cognito cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Cognito resources")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    CognitoCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
