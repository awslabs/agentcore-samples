#!/usr/bin/env python3
"""
Cleanup of the notes gateway (GW2) and its substrate, for both IdPs.

Run by 09-optional-cleanup.ipynb, Step 2 (``python 06_cleanup_obo_gateway.py``
from this directory). Same shape as deployment/5a-gateway-setup/cleanup_gateway.py.
Tears down what 01_deploy_opensearch_collection.py, 03_create_oauth_provider.py
and 04_create_obo_gateway.py created, and only that: every resource is
addressed by its fixed name or by the id stored in SSM, so the interceptor
gateway (GW1) and other workloads in the account are not touched.

What is deleted, in run() order:
  1. Every target on the notes gateway (lakehouse-obo-target), then a poll of
     up to ~60 s until the target list is empty
  2. The notes gateway (lakehouse-notes-gateway); its id comes from SSM
     notes-gateway-id, else a paginated by-name search
  3. The gateway's IAM role (agentcore-lakehouse-notes-gateway-role): inline
     policies deleted and managed policies detached first, which IAM requires
  4. OAuth2 credential providers: Okta OBO (lakehouse-obo-okta-provider) and
     Cognito M2M (lakehouse-notes-cognito-oauth-provider); each is skipped if
     absent, so only the active IdP's provider is actually deleted
  5. AOSS data-access policy   (lakehouse-claim-notes-data)
  6. AOSS collection           (lakehouse-claim-notes), polled up to ~10 min
  7. AOSS network policy       (lakehouse-claim-notes-network)
  8. AOSS encryption policy    (lakehouse-claim-notes-encryption)
  9. SSM keys under /app/lakehouse-agent/ created by this directory:
     notes-gateway-{id,arn,url,name}, obo-credential-provider-arn,
     opensearch-collection-{arn,endpoint} (skipped with --keep-ssm)

Note: there is no agent-IAM revert step — the agent deliberately holds NO OBO
grant (the GW2 gateway role performs the RFC 8693 exchange), so
there is nothing to undo on the agent role.

Resources NOT cleaned here (owned by sibling cleanup scripts):
  - OpenSearch MCP runtime stack + opensearch-mcp-runtime-{arn,id}, then its execution role
    -> deployment/agentcore_cli_deploy.py opensearch-mcp --destroy --yes
       + setup_runtime_roles.py delete --role opensearch-mcp
  - Agent runtime stack, then its execution role
    -> deployment/agentcore_cli_deploy.py lakehouse-agent --destroy --yes
       + setup_runtime_roles.py delete --role lakehouse-agent
  - Interceptor gateway and its substrate
    -> deployment/5a-gateway-setup/cleanup_gateway.py
  - Okta OBO exchange app (lakehouse-obo-exchange-client) + okta-obo-client-* SSM keys
    -> deployment/1-okta-setup/cleanup_okta.py  (Okta-side resource; owned there)

Cleanup ordering (load-bearing): targets BEFORE gateway (the gateway delete waits
up to ~60 s for its targets to go), gateway BEFORE its role (so the gateway never
exists without the role it runs as), data-access policy BEFORE collection (AOSS rejects
collection delete while a data-access policy references it). Network and
encryption policies go last, after the collection delete has been polled to
completion. SSM keys go last of all, so a partial run can be re-run and still
find the gateway id.

Idempotent: re-running after a successful first run logs
all ⏭️ and exits 0.

Usage:
    python 06_cleanup_obo_gateway.py [--keep-ssm]
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session
from utils.notebook_init import pause, wait_for

# Names match the create-side scripts in this directory (01, 03, 04)
COLLECTION_NAME = "lakehouse-claim-notes"
# Default GW2 name. Used at create time and as the last resort in the by-name
# fallback below — the persisted SSM `notes-gateway-name` is preferred so a
# non-default name still resolves. Mirrors GATEWAY_NAME in
# 5a-gateway-setup/cleanup_gateway.py.
GATEWAY_NAME = "lakehouse-notes-gateway"
GATEWAY_ROLE_NAME = f"agentcore-{GATEWAY_NAME}-role"
PROVIDER_NAME = "lakehouse-obo-okta-provider"
# Cognito M2M gateway->runtime credential provider created by 04's Cognito
# branch (COGNITO_M2M_PROVIDER_NAME). Deleted explicitly here (symmetric with
# the Okta OBO provider above) rather than relying on cleanup_gateway.py's
# incidental "lakehouse" substring match. Safe-if-absent -> no-op on Okta.
COGNITO_M2M_PROVIDER_NAME = "lakehouse-notes-cognito-oauth-provider"
ENCRYPTION_POLICY_NAME = f"{COLLECTION_NAME}-encryption"
NETWORK_POLICY_NAME = f"{COLLECTION_NAME}-network"
DATA_ACCESS_POLICY_NAME = f"{COLLECTION_NAME}-data"

SSM_PREFIX = "/app/lakehouse-agent/"


class OBOCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.bedrock = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.iam = boto3.client("iam")
        self.aoss = boto3.client("opensearchserverless", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm

    def _get_ssm_param(self, name, default=None):
        try:
            return self.ssm.get_parameter(Name=f"{SSM_PREFIX}{name}")["Parameter"]["Value"]
        except Exception:
            return default

    def _find_gateway_id_by_name(self):
        """Resolve GW2's id by name when SSM `notes-gateway-id` is missing or stale.

        Covers the missing-or-stale case: a partially failed create, a manually
        deleted parameter, or a teardown re-run after the SSM sweep already went
        through. Without it, teardown reports "no OBO gateway found" and leaves a
        live gateway (plus its targets and role) behind, which reads as a clean
        teardown and is not one.

        Mirrors the equivalent fallback in
        5a-gateway-setup/cleanup_gateway.py::_find_gateway_id_by_name.

        Prefers the persisted `notes-gateway-name` (04_create_obo_gateway.py stores
        it, so a non-default name still resolves) and falls back to the default
        literal. Paginated: an unpaginated list_gateways can miss the target in an
        account with many gateways, which would be the same false "not found" this
        fallback exists to prevent.
        """
        gateway_name = self._get_ssm_param("notes-gateway-name", GATEWAY_NAME)
        print(f"   🔍 SSM notes-gateway-id missing; searching by name: {gateway_name}")
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

    # ─── 1+2: OBO gateway target + gateway ────────────────────────────
    def delete_gateway(self):
        print(f"\n🗑️  Deleting OBO_Gateway: {GATEWAY_NAME}")

        # SSM key may be missing if 04_create_obo_gateway.py partially failed; fall back to a by-name
        # lookup rather than reporting a clean teardown over a live gateway.
        gateway_id = self._get_ssm_param("notes-gateway-id")
        if not gateway_id:
            gateway_id = self._find_gateway_id_by_name()

        if not gateway_id:
            print("   ⏭️  No OBO gateway found (SSM notes-gateway-id absent and no name match)")
            return

        # Targets first
        try:
            targets = self.bedrock.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            for target in targets:
                tid = target["targetId"]
                try:
                    self.bedrock.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=tid)
                    print(f"   ✅ Deleted target: {target.get('name', tid)}")
                except Exception as e:
                    print(f"   ⚠️  Error deleting target {tid}: {e}")

            # Target deletion completes asynchronously; poll (12 x 5 s) until the
            # list is empty so the gateway delete below does not race it.
            if targets:

                def _targets_gone():
                    remaining = self.bedrock.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
                    return not remaining, f"{len(remaining)} target(s) still listed"

                # No raise on timeout: the gateway delete below still runs and prints its own error
                # if it fails, and the rest of the teardown continues.
                wait_for("Gateway targets delete", _targets_gone, interval=5, timeout=60, raise_on_timeout=False)
        except Exception as e:
            print(f"   ⚠️  Error listing/deleting targets: {e}")

        # Gateway
        try:
            self.bedrock.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"   ✅ Deleted gateway: {gateway_id}")
            pause("Gateway delete settling", 5)
        except self.bedrock.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Gateway not found: {gateway_id}")
        except Exception as e:
            print(f"   ❌ Error deleting gateway: {e}")

    # ─── 3: OBO gateway IAM role ──────────────────────────────────────
    def delete_gateway_role(self):
        print(f"\n🗑️  Deleting OBO_Gateway IAM role: {GATEWAY_ROLE_NAME}")
        try:
            self.iam.get_role(RoleName=GATEWAY_ROLE_NAME)
        except self.iam.exceptions.NoSuchEntityException:
            print(f"   ⏭️  Role not found: {GATEWAY_ROLE_NAME}")
            return
        try:
            for p in self.iam.list_role_policies(RoleName=GATEWAY_ROLE_NAME)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyName=p)
            for p in self.iam.list_attached_role_policies(RoleName=GATEWAY_ROLE_NAME)["AttachedPolicies"]:
                self.iam.detach_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyArn=p["PolicyArn"])
            self.iam.delete_role(RoleName=GATEWAY_ROLE_NAME)
            print(f"   ✅ Deleted role: {GATEWAY_ROLE_NAME}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    # ─── 4: OAuth2 credential providers (Okta OBO + Cognito M2M) ───────
    def _oauth_provider_names(self):
        """Exact names of every OAuth2 credential provider in the account (paginated).

        Raises on a failed read, so a failed check is never mistaken for "absent".
        """
        names = set()
        for page in self.bedrock.get_paginator("list_oauth2_credential_providers").paginate():
            names.update(p.get("name", "") for p in page.get("credentialProviders", []))
        return names

    def delete_oauth_provider(self, provider_name=PROVIDER_NAME):
        print(f"\n🗑️  Deleting OAuth2 credential provider: {provider_name}")
        # Check existence first, by exact name. A delete call that returns
        # without an error is not, on its own, proof that a provider existed.
        try:
            present = provider_name in self._oauth_provider_names()
        except Exception as e:
            print(f"   ❌ Error listing OAuth2 credential providers, nothing deleted: {e}")
            return
        if not present:
            print(f"   ⏭️  Not present, skipped: {provider_name}")
            return
        try:
            self.bedrock.delete_oauth2_credential_provider(name=provider_name)
            print(f"   ✅ Deleted: {provider_name}")
        except self.bedrock.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Provider not found: {provider_name}")
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "resourcenotfound" in msg:
                print(f"   ⏭️  Provider not found: {provider_name}")
            else:
                print(f"   ❌ Error: {e}")

    # ─── 5+6+7+8: AOSS data-access policy / network / encryption / collection ─
    def delete_aoss_collection(self):
        print(f"\n🗑️  Deleting AOSS data-access policy: {DATA_ACCESS_POLICY_NAME}")
        # Data-access policy MUST go first — AOSS rejects collection delete
        # while a data-access policy still references the collection.
        try:
            self.aoss.delete_access_policy(name=DATA_ACCESS_POLICY_NAME, type="data")
            print(f"   ✅ Deleted: {DATA_ACCESS_POLICY_NAME}")
        except self.aoss.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Policy not found: {DATA_ACCESS_POLICY_NAME}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        print(f"\n🗑️  Deleting AOSS collection: {COLLECTION_NAME}")
        try:
            response = self.aoss.list_collections(collectionFilters={"name": COLLECTION_NAME})
            summaries = response.get("collectionSummaries", [])
            if not summaries:
                print(f"   ⏭️  Collection not found: {COLLECTION_NAME}")
            else:
                collection_id = summaries[0]["id"]
                try:
                    self.aoss.delete_collection(id=collection_id)
                    print(f"   ✅ Delete initiated: {COLLECTION_NAME} (id={collection_id})")

                    # delete_collection only starts the delete; poll until the
                    # collection disappears so the network and encryption
                    # policies below are removed after it, not alongside it.
                    class _UnexpectedStatus(Exception):
                        pass

                    def _collection_gone():
                        items = self.aoss.batch_get_collection(ids=[collection_id]).get("collectionDetails", [])
                        if not items:
                            return True, "deleted"
                        status = items[0].get("status")
                        if status not in ("DELETING", "ACTIVE", "CREATING"):
                            raise _UnexpectedStatus(status)  # any other status stops the wait at once
                        return False, status

                    # Up to 10 min (15 s x 40). A timeout warns, lists the collection under
                    # "Not confirmed deleted" at the end, and the teardown continues.
                    try:
                        gone = wait_for(
                            "AOSS collection delete", _collection_gone, interval=15, timeout=600, raise_on_timeout=False
                        )
                    except _UnexpectedStatus as e:
                        print(f"   ⚠️  Unexpected status during delete: {e}")
                        gone = False
                    if not gone:
                        print("   ⚠️  Collection delete not confirmed; teardown continues")
                        self.not_confirmed.append(f"AOSS collection {COLLECTION_NAME} (id={collection_id})")
                except self.aoss.exceptions.ResourceNotFoundException:
                    print("   ⏭️  Collection already gone")
                except Exception as e:
                    print(f"   ❌ Error deleting collection: {e}")
        except Exception as e:
            print(f"   ❌ Error listing collections: {e}")

        print(f"\n🗑️  Deleting AOSS network policy: {NETWORK_POLICY_NAME}")
        try:
            self.aoss.delete_security_policy(name=NETWORK_POLICY_NAME, type="network")
            print(f"   ✅ Deleted: {NETWORK_POLICY_NAME}")
        except self.aoss.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Policy not found: {NETWORK_POLICY_NAME}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        print(f"\n🗑️  Deleting AOSS encryption policy: {ENCRYPTION_POLICY_NAME}")
        try:
            self.aoss.delete_security_policy(name=ENCRYPTION_POLICY_NAME, type="encryption")
            print(f"   ✅ Deleted: {ENCRYPTION_POLICY_NAME}")
        except self.aoss.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Policy not found: {ENCRYPTION_POLICY_NAME}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    # ─── 9: SSM keys ──────────────────────────────────────────────────
    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        # All net-new keys this 5b/ directory owns
        params = [
            # GW2 notes gateway (04_create_obo_gateway.py)
            "notes-gateway-id",
            "notes-gateway-arn",
            "notes-gateway-url",
            "notes-gateway-name",
            # OBO credential provider (03_create_oauth_provider.py)
            "obo-credential-provider-arn",
            # AOSS collection (01_deploy_opensearch_collection.py)
            "opensearch-collection-arn",
            "opensearch-collection-endpoint",
        ]
        # opensearch-mcp-runtime-{arn,id} are intentionally NOT deleted here —
        # they're owned by deployment/agentcore_cli_deploy.py opensearch-mcp --destroy
        # and removing them here would create a coverage overlap that would
        # produce ⚠️ on second-run cleanup (defeats the ⏭️-only second-run guarantee).
        for p in params:
            try:
                self.ssm.delete_parameter(Name=f"{SSM_PREFIX}{p}")
                print(f"   ✅ Deleted: {SSM_PREFIX}{p}")
            except self.ssm.exceptions.ParameterNotFound:
                pass
            except Exception as e:
                print(f"   ❌ Error: {e}")

    def run(self):
        self.not_confirmed = []  # deletes that were started but not confirmed; listed at the end
        print("\n🧹 OBO_Gateway Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        # Order matters: gateway-side AWS resources first (target -> gateway ->
        # role -> provider), then AOSS, then SSM.
        self.delete_gateway()
        self.delete_gateway_role()
        self.delete_oauth_provider()  # Okta OBO provider (no-op on Cognito)
        self.delete_oauth_provider(COGNITO_M2M_PROVIDER_NAME)  # Cognito M2M provider (no-op on Okta)
        self.delete_aoss_collection()
        self.delete_ssm_parameters()
        if self.not_confirmed:
            print("\n⚠️  Not confirmed deleted:")
            for item in self.not_confirmed:
                print(f"   - {item}")
        else:
            print("\nNot confirmed deleted: none")
        print("\n✨ OBO_Gateway cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup OBO_Gateway substrate")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    OBOCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
