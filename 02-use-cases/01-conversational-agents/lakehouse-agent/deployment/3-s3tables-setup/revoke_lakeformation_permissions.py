#!/usr/bin/env python3
"""
Revoke Lake Formation Permissions (teardown counterpart to setup_lakeformation_permissions.py)

Revokes the Lake Formation grants held by the deployment's principals so the IAM roles are
not deleted out from under live grants. Deleting a role that still holds Lake Formation
permissions leaves the grant keyed to a principal ARN that no longer resolves.

ORDERING IS LOAD-BEARING — run this BEFORE both:
  1. ``cleanup_s3tables.py``  — it deletes the ``claims``/``users`` tables and the SSM keys
     ``table-bucket-name`` / ``namespace``. Those identifiers are REQUIRED to build the
     Resource filter this script queries with (see the Resource-qualification note below),
     so once that script has run the grants can no longer be located by resource.
  2. ``cleanup_iam_roles.py`` — it deletes the three tenant roles AND
     ``LakeFormationS3TablesDataAccessRole``, which are the grant principals themselves.

Notebook ``09-optional-cleanup.ipynb`` runs this as Step 5, immediately before both.

WHY THIS SHIPS: ``setup_lakeformation_permissions.py`` issues grants for every tenant role
(a DESCRIBE on the database, plus Table / TableWithColumns SELECT grants), and until this
script existed nothing in the deployment tree revoked them -- ``cleanup_s3tables.py`` calls
only ``deregister_resource``, which does not revoke principal grants.

Usage:
    python revoke_lakeformation_permissions.py              # revoke (default)
    python revoke_lakeformation_permissions.py --dry-run    # enumerate and print, change nothing

Exit codes:
    0  revoke completed, or a CONFIRMED-empty enumeration (queries ran and found nothing)
    1  identifiers unresolved, or no query succeeded -- state is UNKNOWN, nothing was revoked
    2  at least one revoke call failed
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session

# Every principal cleanup_iam_roles.py deletes. The three tenant roles are the grant holders
# created by setup_iam_roles.py; LakeFormationS3TablesDataAccessRole is deleted by the same
# script (delete_lakeformation_role) and is therefore queried too -- it normally holds no
# principal grants (it is the registered-resource role), but "normally" is not a reason to
# skip the query. If it ever does hold one, deleting it silently orphans that grant.
TENANT_ROLE_SSM_KEYS = {
    "policyholders": "roles/lakehouse-policyholders-role",
    "adjusters": "roles/lakehouse-adjusters-role",
    "administrators": "roles/lakehouse-administrators-role",
}
LAKEFORMATION_ROLE_NAME = "LakeFormationS3TablesDataAccessRole"

# Tables created by setup_s3tables.py. Grants are held per-table, so each is queried.
TABLES = ["claims", "users"]

# Principals that must never be revoked even if a query returns them. The data-lake
# administrators and any shared/pre-existing service role are outside this deployment's
# ownership; revoking them would break unrelated workloads in the same account.
NEVER_REVOKE_SUBSTRINGS = (
    "AmazonSageMakerS3TablesRoleForLakeFormation",
    "DataLakeAdmins",
    "AmazonSageMakerAdminIAMExecutionRole",
    ":root",
)


class LakeFormationRevoke:
    def __init__(self, dry_run: bool = False):
        _session, self.region, self.account_id = get_aws_session()
        self.lakeformation = boto3.client("lakeformation", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.dry_run = dry_run

        # Counters reported at the end. A revoke that cannot state these is indistinguishable
        # from one that did nothing, which is the failure mode this script exists to avoid.
        self.found = 0
        self.revoked = 0
        self.refused = 0
        self.failed = 0
        # False until at least one list_permissions call actually answers. A zero with this
        # still False is an UNKNOWN, not an empty set.
        self.enumerable = False
        self.read_errors: list[str] = []

    def _get_ssm_param(self, name: str):
        """Return the parameter value, or None if it is absent/unreadable."""
        try:
            return self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")["Parameter"]["Value"]
        except Exception:
            return None

    def resolve_identifiers(self) -> bool:
        """Resolve the catalog/database identifiers the Resource filter needs.

        Returns False if they cannot be resolved -- which means cleanup_s3tables.py has
        already run and this script is too late to locate the grants by resource.
        """
        self.namespace = self._get_ssm_param("namespace")
        self.table_bucket_name = self._get_ssm_param("table-bucket-name")

        if not self.namespace or not self.table_bucket_name:
            print("\n❌ Cannot resolve the Lake Formation resource identifiers.")
            print("   Missing SSM parameter(s) under /app/lakehouse-agent/:")
            if not self.table_bucket_name:
                print("     - table-bucket-name")
            if not self.namespace:
                print("     - namespace")
            print("\n   This is the ordering trap: cleanup_s3tables.py DELETES both keys, so")
            print("   this script must run BEFORE it. Grants may still be live and are now")
            print("   much harder to locate -- they must be found by principal in the Lake")
            print("   Formation console instead. State is UNKNOWN; nothing was revoked.")
            return False

        # Resource CatalogId for S3 Tables uses the federated sub-catalog form, exactly as
        # setup_lakeformation_permissions.py builds it. The top-level CatalogId stays the
        # bare account id. Mismatching these two is the classic S3-Tables-in-LF error.
        self.catalog_id = f"{self.account_id}:s3tablescatalog/{self.table_bucket_name}"
        return True

    def collect_principals(self) -> list[str]:
        """Build the principal ARN list: the three tenant roles plus the LF data-access role."""
        principals = []
        for label, ssm_key in TENANT_ROLE_SSM_KEYS.items():
            arn = self._get_ssm_param(ssm_key)
            if arn:
                principals.append(arn)
            else:
                # Fall back to the documented name. The SSM key may already be gone while
                # the role -- and its grants -- still exist.
                fallback = f"arn:aws:iam::{self.account_id}:role/lakehouse-{label}-role"
                print(f"   ℹ️  SSM {ssm_key} absent; using derived ARN for {label}")
                principals.append(fallback)

        principals.append(f"arn:aws:iam::{self.account_id}:role/{LAKEFORMATION_ROLE_NAME}")
        return principals

    def _resource_filters(self) -> list[dict]:
        """The Resource filters to query per principal.

        list_permissions REJECTS a Principal-only filter with InvalidInputException
        ("Resource is mandatory if Principal is set in the input"). A caller that swallows
        that error reads it as "this principal holds no grants" -- a false, reassuring zero
        that would let the roles be deleted with their grants still live. So every query
        below is Resource-qualified, and a read error is recorded as UNRESOLVED rather than
        treated as absence.

        Querying by Table also returns TableWithColumns and ColumnWildcard grants in their
        real shape, which is what revoke_permissions must be handed back -- a plain Table
        revoke does not match a TableWithColumns grant.
        """
        filters = [{"Database": {"CatalogId": self.catalog_id, "Name": self.namespace}}]
        filters += [
            {"Table": {"CatalogId": self.catalog_id, "DatabaseName": self.namespace, "Name": table}} for table in TABLES
        ]
        return filters

    def enumerate_grants(self, principals: list[str]) -> list[dict]:
        """Return every in-scope grant held by the given principals."""
        print("\n🔍 Enumerating Lake Formation grants (Resource-qualified)...")
        print(f"   top-level CatalogId : {self.account_id}")
        print(f"   resource CatalogId  : {self.catalog_id}")
        print(f"   database            : {self.namespace}")
        print(f"   principals queried  : {len(principals)}")

        grants = []
        for arn in principals:
            role_label = arn.split("/")[-1]
            for resource in self._resource_filters():
                resource_kind = next(iter(resource))
                try:
                    response = self.lakeformation.list_permissions(
                        CatalogId=self.account_id,
                        Principal={"DataLakePrincipalIdentifier": arn},
                        Resource=resource,
                    )
                    self.enumerable = True
                except ClientError as exc:
                    code = exc.response["Error"].get("Code", "Unknown")
                    if code == "EntityNotFoundException":
                        # The database or table is already gone; nothing can be granted on
                        # it. This is a genuine absence, so it does not count as an error.
                        self.enumerable = True
                        continue
                    detail = f"{role_label}/{resource_kind}: {code}"
                    self.read_errors.append(detail)
                    print(f"   ⚠️  UNRESOLVED — list_permissions failed for {detail}")
                    continue

                for grant in response.get("PrincipalResourcePermissions", []):
                    principal = grant.get("Principal", {}).get("DataLakePrincipalIdentifier", "")
                    if principal != arn:
                        continue
                    if any(s in principal for s in NEVER_REVOKE_SUBSTRINGS):
                        print(f"   🛡️  Protected principal, refusing to revoke: {principal}")
                        self.refused += 1
                        continue
                    if not self._in_scope(grant):
                        self.refused += 1
                        continue
                    grants.append(grant)

        self.found = len(grants)
        return grants

    def _in_scope(self, grant: dict) -> bool:
        """Confirm the grant belongs to this deployment's catalog and database."""
        resource = grant.get("Resource", {})
        if not resource:
            return False
        body = next(iter(resource.values()), {})
        if body.get("CatalogId") not in (self.catalog_id, self.account_id):
            print(f"   ⏭️  Out of scope (CatalogId={body.get('CatalogId')}) — left alone")
            return False
        database = body.get("Name") if "Database" in resource else body.get("DatabaseName")
        if database != self.namespace:
            print(f"   ⏭️  Out of scope (database={database}) — left alone")
            return False
        return True

    def revoke_grants(self, grants: list[dict]):
        """Revoke each grant using the exact Resource/Permissions shape Lake Formation returned."""
        print(f"\n🔓 Revoking {len(grants)} grant(s)...")
        for grant in grants:
            resource_kind = next(iter(grant["Resource"]))
            who = grant["Principal"]["DataLakePrincipalIdentifier"].split("/")[-1]
            permissions = ", ".join(grant.get("Permissions", []))
            if self.dry_run:
                print(f"   [dry-run] would revoke {resource_kind} ({permissions}) from {who}")
                continue
            try:
                self.lakeformation.revoke_permissions(
                    CatalogId=self.account_id,
                    Principal=grant["Principal"],
                    Resource=grant["Resource"],
                    Permissions=grant.get("Permissions", []),
                    PermissionsWithGrantOption=grant.get("PermissionsWithGrantOption", []),
                )
                self.revoked += 1
                print(f"   ✅ Revoked {resource_kind} ({permissions}) from {who}")
            except ClientError as exc:
                code = exc.response["Error"].get("Code", "Unknown")
                if code in ("EntityNotFoundException", "InvalidInputException"):
                    print(f"   ⏭️  Already gone ({code}): {resource_kind} for {who}")
                    continue
                self.failed += 1
                print(f"   ❌ Revoke failed ({code}): {resource_kind} for {who}: {exc}")

    def recount_remaining(self, principals: list[str]) -> int | None:
        """Re-read after revoking and return the surviving grant count.

        Returns None if the re-read itself could not be completed -- an unverifiable
        result must not print as zero.
        """
        remaining = 0
        verified = False
        for arn in principals:
            for resource in self._resource_filters():
                try:
                    response = self.lakeformation.list_permissions(
                        CatalogId=self.account_id,
                        Principal={"DataLakePrincipalIdentifier": arn},
                        Resource=resource,
                    )
                    verified = True
                except ClientError as exc:
                    if exc.response["Error"].get("Code") == "EntityNotFoundException":
                        verified = True
                        continue
                    continue
                remaining += len(
                    [
                        g
                        for g in response.get("PrincipalResourcePermissions", [])
                        if g.get("Principal", {}).get("DataLakePrincipalIdentifier") == arn
                    ]
                )
        return remaining if verified else None

    def run(self) -> int:
        print("\n🔐 Lake Formation Permissions Revoke")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        print(f"   Mode: {'DRY RUN' if self.dry_run else 'APPLY'}")

        if not self.resolve_identifiers():
            return 1

        principals = self.collect_principals()
        grants = self.enumerate_grants(principals)

        if not self.enumerable:
            print("\n❌ No list_permissions call succeeded — grant state is UNKNOWN.")
            print("   Refusing to report success. A silent no-op here lets cleanup_iam_roles.py")
            print("   delete the tenant roles while their grants are still live.")
            print(f"   Read errors: {len(self.read_errors)} — {'; '.join(self.read_errors)}")
            return 1

        if not grants:
            print("\n⏭️  Enumeration succeeded and found 0 grants in scope.")
            print("   This is a CONFIRMED empty (queries ran and answered), not a failed read.")
            self._report(remaining=0, principals_queried=len(principals))
            return 1 if self.read_errors else 0

        self.revoke_grants(grants)

        remaining = None if self.dry_run else self.recount_remaining(principals)
        self._report(remaining=remaining, principals_queried=len(principals))

        if self.failed:
            return 2
        if self.read_errors:
            # Some grants may exist behind the failed reads, so success cannot be claimed.
            return 1
        return 0

    def _report(self, remaining, principals_queried: int):
        """Print the denominator. Never a bare count, never a bare zero."""
        print("\n📊 Lake Formation grant revoke summary")
        print(f"   principals queried : {principals_queried} (3 tenant roles + {LAKEFORMATION_ROLE_NAME})")
        print(
            f"   resource filters   : {len(self._resource_filters())} per principal (1 database + {len(TABLES)} tables)"
        )
        print(f"   grants found       : {self.found}")
        print(f"   grants revoked     : {self.revoked}{'  (dry run — nothing changed)' if self.dry_run else ''}")
        print(f"   refused (protected/out-of-scope) : {self.refused}")
        print(f"   revoke failures    : {self.failed}")
        if remaining is None:
            print("   remaining (re-read): UNKNOWN — the verification re-read did not complete")
        else:
            print(f"   remaining (re-read): {remaining}")
        if self.read_errors:
            print(f"   ⚠️  read errors    : {len(self.read_errors)} — {'; '.join(self.read_errors)}")
            print("       Grants may exist behind these failed reads. Treated as UNRESOLVED,")
            print("       never as absent, so this run does not report success.")
        else:
            print("   read errors        : 0 (every query answered)")
        print("\n   🔒 No put_data_lake_settings call — Lake Formation administrators are untouched.")


def main():
    parser = argparse.ArgumentParser(description="Revoke Lake Formation permissions before role deletion")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate and print the grants without revoking anything",
    )
    args = parser.parse_args()

    sys.exit(LakeFormationRevoke(dry_run=args.dry_run).run())


if __name__ == "__main__":
    main()
