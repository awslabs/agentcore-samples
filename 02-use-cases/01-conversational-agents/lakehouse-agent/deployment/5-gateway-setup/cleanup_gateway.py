#!/usr/bin/env python3
"""
Cleanup Gateway Resources

Deletes:
- AgentCore Gateway (targets first, then gateway)
- OAuth2 credential providers (lakehouse-related)
- Request interceptor Lambda function
- Response interceptor Lambda function
- Lambda execution role (InsuranceClaimsGatewayInterceptorRole)
- DynamoDB tenant role mapping table
- SSM parameters

Usage:
    python cleanup_gateway.py [--keep-ssm]
"""

import boto3
import sys
import os
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class GatewayCleanup:
    def __init__(self, keep_ssm=False):
        session, self.region, self.account_id = get_aws_session()
        self.bedrock = boto3.client("bedrock-agentcore-control", region_name=self.region)
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

    def delete_gateway(self):
        print("\n🗑️  Deleting AgentCore Gateway...")
        gateway_id = self._get_ssm_param("gateway-id")
        if not gateway_id:
            print("   ⏭️  No gateway ID found in SSM")
            return

        try:
            # Delete targets first
            try:
                targets = self.bedrock.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
                for target in targets:
                    tid = target["targetId"]
                    self.bedrock.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=tid)
                    print(f"   ✅ Deleted target: {target.get('name', tid)}")

                if targets:
                    print("   ⏳ Waiting for targets to delete...")
                    for _ in range(12):
                        remaining = self.bedrock.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
                        if not remaining:
                            break
                        time.sleep(5)
            except Exception as e:
                print(f"   ⚠️  Error deleting targets: {e}")

            # Delete gateway
            self.bedrock.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"   ✅ Deleted gateway: {gateway_id}")
            time.sleep(5)
        except self.bedrock.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Gateway not found: {gateway_id}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    def delete_oauth_providers(self):
        print("\n🗑️  Deleting OAuth2 credential providers...")
        try:
            response = self.bedrock.list_oauth2_credential_providers()
            providers = response.get(
                "oauth2CredentialProviders",
                response.get("credentialProviders", response.get("items", [])),
            )
            deleted = 0
            for provider in providers:
                name = provider.get("name", "")
                if "lakehouse" in name.lower():
                    try:
                        self.bedrock.delete_oauth2_credential_provider(name=name)
                        print(f"   ✅ Deleted: {name}")  # codeql[py/clear-text-logging-sensitive-data]
                        deleted += 1
                    except Exception as e:
                        print(f"   ⚠️  Error deleting {name}: {e}")  # codeql[py/clear-text-logging-sensitive-data]
            if deleted == 0:
                print("   ⏭️  No lakehouse OAuth providers found")
        except Exception as e:
            print(f"   ⚠️  Error listing providers: {e}")

    def delete_lambda_functions(self):
        print("\n🗑️  Deleting Lambda functions...")
        functions = [
            "lakehouse-gateway-interceptor",
            "lakehouse-gateway-response-interceptor",
        ]
        for func_name in functions:
            try:
                self.lambda_client.delete_function(FunctionName=func_name)
                print(f"   ✅ Deleted: {func_name}")
            except self.lambda_client.exceptions.ResourceNotFoundException:
                print(f"   ⏭️  Not found: {func_name}")
            except Exception as e:
                print(f"   ❌ Error deleting {func_name}: {e}")

    def delete_lambda_role(self):
        print("\n🗑️  Deleting Lambda execution role...")
        role_name = "InsuranceClaimsGatewayInterceptorRole"
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
            print(f"   ❌ Error: {e}")

    def delete_gateway_role(self):
        print("\n🗑️  Deleting Gateway IAM role...")
        role_name = "agentcore-lakehouse-gateway-role"
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
            print(f"   ❌ Error: {e}")

    def delete_dynamodb_table(self):
        print("\n🗑️  Deleting DynamoDB tenant role mapping table...")
        try:
            self.dynamodb.delete_table(TableName="lakehouse_tenant_role_map")
            print("   ✅ Deleted table: lakehouse_tenant_role_map")
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
            "gateway-arn",
            "gateway-id",
            "gateway-url",
            "gateway-name",
            "interceptor-lambda-arn",
            "interceptor-lambda-role-arn",
            "response-interceptor-lambda-arn",
            "tenant-role-mapping-table",
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
        print("\n🧹 Gateway Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        self.delete_gateway()
        self.delete_oauth_providers()
        self.delete_lambda_functions()
        self.delete_lambda_role()
        self.delete_gateway_role()
        self.delete_dynamodb_table()
        self.delete_ssm_parameters()
        print("\n✨ Gateway cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Gateway resources")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    GatewayCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
