#!/usr/bin/env python3
"""
Cleanup IAM Tenant Roles

Deletes:
- lakehouse-policyholders-role
- lakehouse-adjusters-role
- lakehouse-administrators-role
- LakeFormationS3TablesDataAccessRole
- SSM parameters for role ARNs

⚠️ RUN THE LAKE FORMATION REVOKE FIRST. All four roles above are Lake Formation grant
principals (see 3-s3tables-setup/setup_lakeformation_permissions.py). Deleting a role that
still holds grants leaves each grant keyed to a principal ARN that no longer resolves, and
this script cannot detect or report that — it deletes roles, not permissions.

    cd ../3-s3tables-setup
    python revoke_lakeformation_permissions.py

That script must also run before cleanup_s3tables.py, which deletes the SSM identifiers the
revoke needs to locate grants by resource. Notebook 09-optional-cleanup.ipynb runs the three
in the correct order (Step 5 revoke → Step 6 s3tables → Step 7 roles).

Usage:
    python cleanup_iam_roles.py [--keep-ssm]
"""

import argparse
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class IAMRolesCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.iam = boto3.client("iam")
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm

    def _delete_role(self, role_name):
        try:
            self.iam.get_role(RoleName=role_name)
        except self.iam.exceptions.NoSuchEntityException:
            print(f"   ⏭️  Role not found: {role_name}")
            return

        try:
            # Delete inline policies
            for p in self.iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)

            # Detach managed policies
            for p in self.iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                self.iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])

            # Remove from instance profiles
            for ip in self.iam.list_instance_profiles_for_role(RoleName=role_name)["InstanceProfiles"]:
                self.iam.remove_role_from_instance_profile(
                    InstanceProfileName=ip["InstanceProfileName"], RoleName=role_name
                )

            self.iam.delete_role(RoleName=role_name)
            print(f"   ✅ Deleted role: {role_name}")
        except Exception as e:
            print(f"   ❌ Error deleting {role_name}: {e}")

    def delete_tenant_roles(self):
        print("\n🗑️  Deleting tenant IAM roles...")
        roles = [
            "lakehouse-policyholders-role",
            "lakehouse-adjusters-role",
            "lakehouse-administrators-role",
        ]
        for role in roles:
            self._delete_role(role)

    def delete_lakeformation_role(self):
        print("\n🗑️  Deleting Lake Formation data access role...")
        self._delete_role("LakeFormationS3TablesDataAccessRole")

    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        params = [
            "roles/lakehouse-policyholders-role",
            "roles/lakehouse-adjusters-role",
            "roles/lakehouse-administrators-role",
            "lakeformation-role-arn",
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
        print("\n🧹 IAM Roles Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        print("\n⚠️  All four roles below are Lake Formation grant principals.")
        print("   If you have not run 3-s3tables-setup/revoke_lakeformation_permissions.py,")
        print("   stop now: deleting these roles orphans their grants, and this script")
        print("   deletes roles only — it does not revoke permissions and cannot detect them.")
        self.delete_tenant_roles()
        self.delete_lakeformation_role()
        self.delete_ssm_parameters()
        print("\n✨ IAM roles cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup IAM tenant roles")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    IAMRolesCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
