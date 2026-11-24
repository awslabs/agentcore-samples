#!/usr/bin/env python3
"""
Cleanup IAM Tenant Roles

Deletes:
- lakehouse-policyholders-role
- lakehouse-adjusters-role
- lakehouse-administrators-role
- LakeFormationS3TablesDataAccessRole
- SSM parameters for role ARNs

A role is deleted only when its ownership record shows this sample created it and it
carries no policies or instance profiles this sample did not add. A same-name role with
no ownership record is left in place, and LakeFormationS3TablesDataAccessRole is not
deleted while Lake Formation still uses it for the S3 Tables registration (run
3-s3tables-setup/cleanup_s3tables.py first).

Exit code: 0 when every role was deleted or already gone, 1 on any error, 3 when the
only problem is a role left in place.

Run the Lake Formation revoke first. All four roles above are Lake Formation grant
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
from utils.iam_roles import (
    FAILED,
    LEFT,
    OK,
    OURS,
    RoleOwnershipError,
    combine,
    delete_owned_role,
    lf_registration_owner,
)

# The managed policy setup_iam_roles.py attaches to every tenant role.
ATHENA_FULL_ACCESS_ARN = "arn:aws:iam::aws:policy/AmazonAthenaFullAccess"
LF_ROLE_NAME = "LakeFormationS3TablesDataAccessRole"


class IAMRolesCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.iam = boto3.client("iam")
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.lakeformation = boto3.client("lakeformation", region_name=self.region)
        self.keep_ssm = keep_ssm
        # Failed SSM deletes are collected here; run() prints ✨ only when this stays empty
        # and every role result is OK. "Not found" outcomes are not failures.
        self.errors = []
        # (role name, OK / FAILED / LEFT) for every role this script handled.
        self.role_results = []

    def delete_tenant_roles(self):
        print("\n🗑️  Deleting tenant IAM roles...")
        roles = [
            "lakehouse-policyholders-role",
            "lakehouse-adjusters-role",
            "lakehouse-administrators-role",
        ]
        for role in roles:
            # The inline policy names setup_iam_roles.py puts on each role.
            if "administrators" in role:
                expected_inline = {f"{role}-admin-access"}
            else:
                expected_inline = {f"{role}-athena-s3-access"}
            code = delete_owned_role(self.iam, self.ssm, role, expected_inline, {ATHENA_FULL_ACCESS_ARN})
            self.role_results.append((role, code))

    def delete_lakeformation_role(self):
        print("\n🗑️  Deleting Lake Formation data access role...")
        resource_arn = f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"
        try:
            owner, _ = lf_registration_owner(self.lakeformation, self.iam, self.ssm, resource_arn, LF_ROLE_NAME)
        except RoleOwnershipError as e:
            print(f"   {e}")
            self.role_results.append((LF_ROLE_NAME, FAILED))
            return
        if owner == OURS:
            print(
                f"   ❌ IAM role {LF_ROLE_NAME} is still registered with Lake Formation for S3 Tables. "
                "Run cleanup_s3tables.py first. Nothing was deleted."
            )
            self.role_results.append((LF_ROLE_NAME, FAILED))
            return
        code = delete_owned_role(self.iam, self.ssm, LF_ROLE_NAME, {"S3TablesDataAccess"}, set())
        self.role_results.append((LF_ROLE_NAME, code))

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
                self.errors.append(f"SSM /app/lakehouse-agent/{p}: {e}")

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
        # Exit 1 on any error; exit 3 when the only problem is a role left in place.
        failed = [f"role {name}: see above" for name, code in self.role_results if code == FAILED] + self.errors
        left = [name for name, code in self.role_results if code == LEFT]
        rc = combine([code for _, code in self.role_results] + [FAILED] * len(self.errors))
        if failed:
            print(f"\n❌ cleanup_iam_roles.py finished with {len(failed)} error(s):")
            for err in failed:
                print(f"   • {err}")
        if left:
            print("\n⚠  cleanup_iam_roles.py left these roles in place: " + ", ".join(left))
        if rc != OK:
            sys.exit(rc)
        print("\n✨ IAM roles cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup IAM tenant roles")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    IAMRolesCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
