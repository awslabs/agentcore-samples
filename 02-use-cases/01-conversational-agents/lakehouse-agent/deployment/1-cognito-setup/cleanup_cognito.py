#!/usr/bin/env python3
"""
Cleanup Cognito Resources

Deletes:
- Cognito User Pool domain
- Cognito User Pool (and all users/clients)
- Post-authentication Lambda function
- Post-authentication Lambda IAM role
- DynamoDB login audit table
- SSM parameters

Usage:
    python cleanup_cognito.py [--keep-ssm]
"""

import boto3
import sys
import os
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class CognitoCleanup:
    def __init__(self, keep_ssm=False):
        session, self.region, self.account_id = get_aws_session()
        self.cognito = boto3.client("cognito-idp", region_name=self.region)
        self.lambda_client = boto3.client("lambda", region_name=self.region)
        self.iam = boto3.client("iam")
        self.dynamodb = boto3.client("dynamodb", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm

    def _get_ssm_param(self, name, default=None):
        try:
            return self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")["Parameter"]["Value"]
        except Exception:
            return default

    def delete_user_pool(self):
        print("\n🗑️  Deleting Cognito User Pool...")
        pool_id = self._get_ssm_param("cognito-user-pool-id")
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
                time.sleep(3)

            self.cognito.delete_user_pool(UserPoolId=pool_id)
            print(f"   ✅ Deleted User Pool: {pool_id}")
        except self.cognito.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  User Pool not found: {pool_id}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

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

    def delete_post_auth_role(self):
        print("\n🗑️  Deleting post-auth Lambda role...")
        role_name = "lakehouse-cognito-post-auth-role"
        self._delete_iam_role(role_name)

    def _delete_iam_role(self, role_name):
        try:
            self.iam.get_role(RoleName=role_name)
        except self.iam.exceptions.NoSuchEntityException:
            print(f"   ⏭️  Role not found: {role_name}")
            return

        try:
            for p in self.iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            for p in self.iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                self.iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
            self.iam.delete_role(RoleName=role_name)
            print(f"   ✅ Deleted role: {role_name}")
        except Exception as e:
            print(f"   ❌ Error deleting role {role_name}: {e}")

    def delete_audit_table(self):
        print("\n🗑️  Deleting login audit DynamoDB table...")
        try:
            self.dynamodb.delete_table(TableName="lakehouse_user_login_audit")
            print("   ✅ Deleted table: lakehouse_user_login_audit")
        except self.dynamodb.exceptions.ResourceNotFoundException:
            print("   ⏭️  Table not found")
        except Exception as e:
            print(f"   ❌ Error: {e}")

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

    def run(self):
        print("\n🧹 Cognito Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        self.delete_user_pool()
        self.delete_post_auth_lambda()
        self.delete_post_auth_role()
        self.delete_audit_table()
        self.delete_ssm_parameters()
        print("\n✨ Cognito cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Cognito resources")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    CognitoCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
