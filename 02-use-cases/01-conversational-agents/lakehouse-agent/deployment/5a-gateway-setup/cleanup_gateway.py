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

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session
from utils.idp_config import get_idp_provider

# GW1's name, matching create_gateway.py's `gateway_name` default. Used only as the
# last resort in the by-name fallback below — the persisted SSM `gateway-name` is
# preferred so a non-default name still resolves. Mirrors GATEWAY_NAME in
# 5b-obo-gateway-setup/06_cleanup_obo_gateway.py.
GATEWAY_NAME = "lakehouse-gateway"


class GatewayCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.bedrock = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.lambda_client = boto3.client("lambda", region_name=self.region)
        self.iam = boto3.client("iam")
        self.dynamodb = boto3.client("dynamodb", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        # Active IdP (R4/M4) — selects the exact GW1 provider name(s) to delete.
        self.idp_provider = get_idp_provider(self.ssm)
        self.keep_ssm = keep_ssm

    def _get_ssm_param(self, name, default=None):
        try:
            return self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")["Parameter"]["Value"]
        except Exception:
            return default

    def _find_gateway_id_by_name(self):
        """Resolve the GW1 gateway id by NAME, as a fallback for a missing SSM key.

        The SSM `gateway-id` read is the normal path and works on a healthy stack.
        This exists for the case where that key is absent or stale — a partially
        failed create, a manually deleted parameter, or a teardown re-run after the
        SSM sweep already went through. Without it, teardown reports "no gateway
        found" and leaves a live gateway (plus its targets and role) behind, which
        reads as a clean teardown and is not one.

        Mirrors the equivalent fallback in
        5b-obo-gateway-setup/06_cleanup_obo_gateway.py::delete_gateway.

        Prefers the persisted `gateway-name` (create_gateway.py stores it, so a
        non-default name still resolves) and falls back to the default literal.
        Paginated: an unpaginated list_gateways can miss the target in an account
        with many gateways, which would be the same false "not found" this fallback
        exists to prevent.
        """
        gateway_name = self._get_ssm_param("gateway-name", GATEWAY_NAME)
        print(f"   🔍 SSM gateway-id missing; searching by name: {gateway_name}")
        try:
            scanned = 0
            next_token = None
            while True:
                kwargs = {"nextToken": next_token} if next_token else {}
                response = self.bedrock.list_gateways(**kwargs)
                items = response.get("items", [])
                scanned += len(items)
                for gw in items:
                    if gw.get("name") == gateway_name:
                        found = gw["gatewayId"]
                        print(f"   ✅ Matched by name after scanning {scanned}: {found}")
                        return found
                next_token = response.get("nextToken")
                if not next_token:
                    break
            print(f"   ⏭️  No gateway named {gateway_name} among {scanned} scanned")
        except Exception as e:
            # Report rather than swallow: a failed lookup is not the same as
            # "absent", and treating it as absent is how a live gateway survives
            # a teardown that claims success.
            print(f"   ⚠️  list_gateways failed: {e}")
        return None

    def delete_gateway(self):
        print("\n🗑️  Deleting AgentCore Gateway...")
        gateway_id = self._get_ssm_param("gateway-id")
        if not gateway_id:
            gateway_id = self._find_gateway_id_by_name()
        if not gateway_id:
            print("   ⏭️  No gateway found (SSM gateway-id absent and no name match)")
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
        print("\n🗑️  Deleting GW1 OAuth2 credential providers...")
        # EXACT-name deletes for the ACTIVE IdP's GW1 provider(s) only (R4/M4),
        # safe-if-absent. The prior lakehouse-prefix substring match collaterally
        # deleted the GW2 notes/OBO providers — those are owned by
        # 06_cleanup_obo_gateway.py and are intentionally NOT deleted here.
        if self.idp_provider == "cognito":
            # DR-17: create_gateway.py now REQUIRES the dedicated M2M client and no
            # longer creates the hybrid "lakehouse-mcp-oauth-provider". We KEEP the
            # hybrid name in the delete list anyway (safe-if-absent) so teardown still
            # cleans up any provider left behind by a pre-hardening deploy.
            provider_names = ["lakehouse-mcp-m2m-oauth-provider", "lakehouse-mcp-oauth-provider"]
        else:  # okta
            provider_names = ["lakehouse-mcp-okta-oauth-provider"]

        deleted = 0
        for name in provider_names:
            try:
                self.bedrock.delete_oauth2_credential_provider(name=name)
                print(f"   ✅ Deleted: {name}")  # codeql[py/clear-text-logging-sensitive-data]
                deleted += 1
            except self.bedrock.exceptions.ResourceNotFoundException:
                print(f"   ⏭️  Not found: {name}")
            except Exception as e:
                msg = str(e).lower()
                if "not found" in msg or "resourcenotfound" in msg:
                    print(f"   ⏭️  Not found: {name}")
                else:
                    print(f"   ⚠️  Error deleting {name}: {e}")  # codeql[py/clear-text-logging-sensitive-data]
        if deleted == 0:
            print(f"   ⏭️  No GW1 OAuth providers found for IdP={self.idp_provider}")

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
