#!/usr/bin/env python3
"""
Load Sample Claim-Note Documents into OpenSearch Serverless

This script:
1. Reads the AOSS collection endpoint from SSM.
2. Idempotent-creates the `claim-notes` index with the spec mapping
   (owner_user_sub keyword, note_text text, claim_id/note_type keyword,
   created_at date).
3. Discovers test-user subs from SSM (<idp>-user-*-sub keys: okta-user-* written
   by setup_okta.py in notebook 01, cognito-user-* by seed_cognito_user_subs.py)
   and bulk-loads DISJOINT free-text claim-note documents per user.

Data distinctness (per design §4):
- Notes are FREE-TEXT (adjuster narratives, damage descriptions, call
  summaries), qualitatively different from the structured claims records
  served by the Claims_MCP_Server (which carry claim_id, claim_status,
  claim_amount, etc.). The agent's tool choice should be obvious from the
  question shape: note-search => notes/* (this server); structured
  claims/amounts/status => claims/* (Claims_MCP_Server).
- note_type ∈ {adjuster_note, call_summary, damage_assessment}.
- Per-user content is disjoint: each user gets its own 3-document set keyed
  by the user's LABEL (NOTES_BY_LABEL[label]), and every claim_id is
  label-prefixed ("CLM-<label>-<suffix>"). User A's claim_ids cannot appear
  under user B's owner_user_sub filter, so the notebook 07 isolation
  invariant is testable by simple set-difference.

Usage:
    python load_sample_opensearch_data.py
"""

import json
import os
import sys
from datetime import datetime, timezone

import boto3
from aws_requests_auth.aws_auth import AWSRequestsAuth
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers

# Make the repo's utils/ importable (idp_config lives there).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import get_idp_provider

CLAIM_NOTES_INDEX = "claim-notes"
SSM_PREFIX = "/app/lakehouse-agent/"


# Index mapping. Field types chosen to support the
# query shape in opensearch_tools.search_claim_notes:
#   - owner_user_sub: keyword (exact-match term filter for RLS)
#   - note_text:      text    (full-text match query)
#   - claim_id:       keyword (exact-match identifier; not searched as text)
#   - note_type:      keyword (exact-match facet)
#   - created_at:     date    (range queries / sorting)
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "claim_id": {"type": "keyword"},
            "owner_user_sub": {"type": "keyword"},
            "note_text": {"type": "text"},
            "note_type": {"type": "keyword"},
            "created_at": {"type": "date"},
        }
    }
}


# Canonical LABEL-KEYED note fixture. Each of the 5 test-user labels maps to
# its own list of 3 disjoint free-text notes; each note is a 3-tuple of
# (note_type, claim_id_suffix, note_text). Assignment is by LABEL (not by the
# user's position in the sorted sub list), so a user always receives the same
# notes regardless of how many other users are seeded — this prevents the
# earlier off-by-one (12 templates / 4 slices vs 5 seeded users) and the
# positional-slice coupling. claim_id is "CLM-<label>-<suffix>", so both
# claim_ids and narrative content are disjoint across users by construction.
#
# All notes describe insurance-claim *narratives* with no structured amount/
# status fields in the payload; the agent should pick this tool when the
# user asks about damage descriptions, adjuster commentary, call history,
# etc., and pick the claims/* tools when the user asks about totals,
# pending counts, or specific claim_id lookups.
NOTES_BY_LABEL: dict[str, list[tuple]] = {
    # policyholder001 — storm / water-intrusion narratives
    "policyholder001": [
        (
            "adjuster_note",
            "AN-PH1-001",
            "Field adjuster visited the property on the morning after the storm. "
            "Observed water damage to the kitchen ceiling and ongoing seepage from a "
            "displaced roof shingle. Recommended emergency tarp and follow-up "
            "structural inspection. Homeowner cooperative; documentation thorough.",
        ),
        (
            "call_summary",
            "CS-PH1-001",
            "Inbound call from policyholder reporting that the contractor had not yet "
            "scheduled the roof repair. Confirmed the claim is in adjuster review and "
            "noted that the policyholder should expect a callback within two "
            "business days. Tone: anxious but constructive.",
        ),
        (
            "damage_assessment",
            "DA-PH1-001",
            "Initial damage assessment: kitchen ceiling drywall saturated across "
            "approximately twelve square feet, cabinetry warping along the upper "
            "left run, hardwood flooring shows cupping under the affected zone. "
            "Mold mitigation flagged as elevated risk; air-quality test recommended.",
        ),
    ],
    # policyholder002 — rear-end auto-collision narratives
    "policyholder002": [
        (
            "adjuster_note",
            "AN-PH2-001",
            "Inspected the vehicle at the body-shop after the rear-end collision. "
            "Bumper cover cracked, tailgate misaligned by a noticeable margin, "
            "rear quarter panel showing crease damage. Frame appeared straight on "
            "visual check; pre-repair scan recommended before any disassembly.",
        ),
        (
            "call_summary",
            "CS-PH2-001",
            "Policyholder called requesting status on the rental-vehicle "
            "extension. Confirmed coverage is in place through the end of the "
            "month and walked them through the body-shop pickup process. They "
            "asked about pursuing the at-fault party's carrier; explained "
            "subrogation timing.",
        ),
        (
            "damage_assessment",
            "DA-PH2-001",
            "Photographic survey shows clearly observable damage concentrated at "
            "the rear of the vehicle. Tail-lamp housings intact but cracked. No "
            "evidence of airbag deployment. Mileage logged and matches the "
            "policyholder's stated odometer reading at the time of loss.",
        ),
    ],
    # adjuster001 — basement-intrusion narratives
    "adjuster001": [
        (
            "adjuster_note",
            "AN-ADJ1-001",
            "Walked the property line with the homeowner to identify the source "
            "of basement intrusion. Drainage from the neighboring lot appears to "
            "feed through a low spot near the egress window. Recommend grading "
            "review and a moisture log over the next two weeks before final "
            "settlement discussion.",
        ),
        (
            "call_summary",
            "CS-ADJ1-001",
            "Policyholder followed up regarding their personal-property inventory "
            "spreadsheet. Confirmed receipt and noted three items still need "
            "supporting receipts. Discussed the depreciation schedule and what "
            "categories are subject to actual-cash-value versus replacement-cost "
            "treatment.",
        ),
        (
            "damage_assessment",
            "DA-ADJ1-001",
            "Basement assessment shows water staining along the north wall up to "
            "approximately eight inches above slab grade. Carpet padding is "
            "saturated and not salvageable. HVAC return duct in the affected room "
            "shows rust at the base; recommend professional duct cleaning prior "
            "to drywall close-up.",
        ),
    ],
    # adjuster002 — front-end auto narratives
    "adjuster002": [
        (
            "adjuster_note",
            "AN-ADJ2-001",
            "Reviewed the auto-loss photos remotely and requested additional "
            "shots of the underside of the front bumper and the radiator support. "
            "Initial impression suggests the impact was lower than the photos "
            "first showed; underlying components likely affected.",
        ),
        (
            "call_summary",
            "CS-ADJ2-001",
            "Policyholder asked whether the deductible would be waived given the "
            "other driver's admission of fault on the police report. Walked "
            "through the carrier's recovery process and explained that the "
            "deductible is reimbursed on successful subrogation, not waived "
            "up-front.",
        ),
        (
            "damage_assessment",
            "DA-ADJ2-001",
            "Front-end damage shows hood crumple, headlight assembly compromised "
            "on the driver side, and visible coolant residue under the front "
            "cross-member. Recommend a full tear-down inspection at an authorized "
            "facility before parts ordering.",
        ),
    ],
    # admin — commercial hail / roof narratives
    "admin": [
        (
            "adjuster_note",
            "AN-ADM-001",
            "Inspected the flat commercial roof after the hail event. Membrane "
            "shows scattered impact bruising and two punctures near the rooftop "
            "HVAC curbs. Recommend an infrared moisture survey to scope any "
            "sub-membrane saturation before repair versus replacement is decided.",
        ),
        (
            "call_summary",
            "CS-ADM-001",
            "Property manager called to coordinate access for the engineering "
            "consultant. Confirmed the tenant list and after-hours entry "
            "procedure, and explained that business-interruption items are "
            "handled under a separate coverage part from the physical roof "
            "repair.",
        ),
        (
            "damage_assessment",
            "DA-ADM-001",
            "Assessment of the interior shows ceiling-tile staining in three "
            "second-floor offices directly beneath the punctured membrane "
            "sections. No structural deck deformation observed. Recommend "
            "temporary interior protection until the roof is made watertight.",
        ),
    ],
}


def get_aoss_client(region: str, collection_endpoint: str) -> OpenSearch:
    """Build a SigV4-signed OpenSearch client against the AOSS collection."""
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available for OpenSearch SigV4 signing")

    endpoint_host = collection_endpoint.replace("https://", "").replace("http://", "")

    auth = AWSRequestsAuth(
        aws_access_key=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        aws_token=credentials.token,
        aws_host=endpoint_host,
        aws_region=region,
        aws_service="aoss",
    )

    return OpenSearch(
        hosts=[{"host": endpoint_host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


def ensure_index(client: OpenSearch) -> None:
    """Idempotent: check-exists then create."""
    print(f"\n📚 Ensuring index '{CLAIM_NOTES_INDEX}' exists...")
    try:
        if client.indices.exists(index=CLAIM_NOTES_INDEX):
            print("   ⏭️  Index already exists; skipping create")
            # Note: We do not validate the existing mapping shape here. R8.5
            # / design §12 #3 ("idempotent IaC pattern") suggests fail-loud
            # on drift, but the original demo's data-load scripts do not
            # mapping-diff either. Tutorial-reader cleanup path: run
            # cleanup_runtime.py + re-deploy the AOSS collection (D.2 task
            # 9.6) to drop and recreate the index.
            return
    except Exception as e:
        print(f"   ⚠️  exists() probe failed: {e}; attempting create anyway")

    try:
        client.indices.create(index=CLAIM_NOTES_INDEX, body=INDEX_MAPPING)
        print(f"   ✅ Index created with mapping: {json.dumps(INDEX_MAPPING['mappings']['properties'])}")
    except Exception as e:
        print(f"   ❌ Failed to create index: {e}")
        raise


def discover_user_subs(ssm) -> list[dict[str, str]]:
    """
    Discover test-user sub identifiers from SSM.

    Reads /app/lakehouse-agent/<idp>-user-*-sub (DR-8/DR-9 IdP branch):
    `okta-user-*` on Okta (written by setup_okta.py in notebook 01),
    `cognito-user-*` on Cognito (each user's Cognito `sub` GUID, seeded by
    seed_cognito_user_subs.py).
    Returns a sorted list of {label, sub} where label is the middle segment
    (e.g., 'policyholder001'). The sort only makes the print/enumeration order
    deterministic — per-user note assignment is keyed by LABEL (NOTES_BY_LABEL),
    not by sort position. The seeded `sub` matches what the gateway forwards at
    query time by construction, so the owner_user_sub filter is non-vacuous.
    """
    idp = get_idp_provider(ssm)
    user_prefix = "cognito-user-" if idp == "cognito" else "okta-user-"
    print(f"\n🔎 Discovering test-user subs in SSM under '{SSM_PREFIX}{user_prefix}*-sub' (IdP: {idp})...")

    paginator = ssm.get_paginator("get_parameters_by_path")
    found = []
    for page in paginator.paginate(Path=SSM_PREFIX, Recursive=False):
        for p in page.get("Parameters", []):
            name = p["Name"]
            # Match /app/lakehouse-agent/<idp>-user-<label>-sub
            if not name.startswith(f"{SSM_PREFIX}{user_prefix}"):
                continue
            if not name.endswith("-sub"):
                continue
            label = name[len(f"{SSM_PREFIX}{user_prefix}") : -len("-sub")]
            found.append({"label": label, "sub": p["Value"]})

    found.sort(key=lambda x: x["label"])

    if not found:
        print(f"   ❌ No {user_prefix}*-sub parameters found in SSM")
        if idp == "cognito":
            print("      Run seed_cognito_user_subs.py first (writes cognito-user-*-sub from the pool).")
        else:
            print("      okta-user-*-sub keys are written by setup_okta.py (notebook 01). Run notebook 01 first.")
        return []

    print(f"   ✅ Discovered {len(found)} test user(s):")
    for u in found:
        print(f"      - {user_prefix}{u['label']}-sub = {u['sub']}")
    return found


def assert_label_coverage(users: list[dict[str, str]]) -> None:
    """
    Fail-fast if any discovered user LABEL has no NOTES_BY_LABEL entry.

    Assignment is label-keyed, so every seeded test user must have a fixture
    entry before we index anything. This replaces the old positional
    pool-exhaustion RuntimeError and keeps the loader correct even if the
    seeded-user set changes (add a NOTES_BY_LABEL entry for any new label).
    """
    missing = sorted(u["label"] for u in users if u["label"] not in NOTES_BY_LABEL)
    if missing:
        raise RuntimeError(
            "NOTES_BY_LABEL is missing an entry for these discovered test-user "
            f"label(s): {missing}. Known labels: {sorted(NOTES_BY_LABEL)}. Add a "
            "3-note fixture entry per missing label (keyed by the user's label)."
        )


def build_documents_for_user(user_sub: str, user_label: str) -> list[dict]:
    """
    Build the disjoint note documents for a user, keyed by the user's LABEL.

    Looks up NOTES_BY_LABEL[user_label] (validated present by
    assert_label_coverage). Each document carries a label-prefixed claim_id
    ("CLM-<label>-<suffix>") so both claim_id and narrative content are
    disjoint across users regardless of seed order or count.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    docs = []
    for note_type, claim_id_suffix, note_text in NOTES_BY_LABEL[user_label]:
        docs.append(
            {
                "claim_id": f"CLM-{user_label}-{claim_id_suffix}",
                "owner_user_sub": user_sub,
                "note_text": note_text,
                "note_type": note_type,
                "created_at": now_iso,
            }
        )
    return docs


def bulk_load(client: OpenSearch, docs: list[dict]) -> None:
    """Bulk-index documents into the claim-notes index."""
    if not docs:
        print("   ⏭️  No documents to load")
        return

    print(f"\n📥 Bulk-loading {len(docs)} document(s)...")

    # Deterministic _id = claim_id (globally unique: CLM-<user_label>-<suffix>)
    # turns the bulk "index" op into an idempotent UPSERT. Re-running the
    # seeder overwrites the same documents in place instead of appending
    # duplicates under fresh auto-generated ids — so notebook 07's exact
    # per-user note counts stay stable across re-seeds. Custom document _id is
    # supported on SEARCH-type AOSS collections (PUT <index>/_doc/<id> falls
    # under the aoss:WriteDocument grant the loader principal already has), so
    # this needs no additional data-access permission.
    actions = [
        {
            "_op_type": "index",
            "_index": CLAIM_NOTES_INDEX,
            "_id": doc["claim_id"],
            "_source": doc,
        }
        for doc in docs
    ]

    # AOSS does not support refresh on bulk; let it propagate naturally.
    try:
        success, errors = helpers.bulk(client, actions, raise_on_error=False, raise_on_exception=False)
        print(f"   ✅ Indexed {success} document(s)")
        if errors:
            print(f"   ⚠️  {len(errors)} error(s) reported by bulk helper:")
            for err in errors[:5]:
                print(f"      {err}")
    except Exception as e:
        print(f"   ❌ Bulk load failed: {e}")
        raise


def main():
    print("=" * 70)
    print("Load Sample Claim-Note Documents into OpenSearch Serverless")
    print("=" * 70)

    session = boto3.Session()
    region = session.region_name
    if not region:
        print("❌ Could not detect AWS region from boto3 session")
        sys.exit(1)

    ssm = boto3.client("ssm", region_name=region)

    print(f"\n📋 Region: {region}")

    # Read the AOSS collection endpoint from SSM
    print("\n🔍 Loading AOSS collection endpoint from SSM...")
    try:
        endpoint = ssm.get_parameter(Name=f"{SSM_PREFIX}opensearch-collection-endpoint")["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        print(f"❌ SSM parameter {SSM_PREFIX}opensearch-collection-endpoint not found")
        print("   Run deployment/5b-obo-gateway-setup/01_deploy_opensearch_collection.py first")
        sys.exit(1)

    print(f"   ✅ Collection endpoint: {endpoint}")

    # Build AOSS client
    client = get_aoss_client(region, endpoint)

    # Idempotent index create
    ensure_index(client)

    # Discover test-user subs
    users = discover_user_subs(ssm)
    if not users:
        sys.exit(1)

    # Coverage pre-check: every discovered label must have a fixture entry
    # (fail-fast before we index anything).
    try:
        assert_label_coverage(users)
    except RuntimeError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    # Build per-user disjoint document sets (label-keyed)
    all_docs = []
    for i, user in enumerate(users):
        docs = build_documents_for_user(user["sub"], user["label"])
        print(f"\n📄 User {i} ({user['label']}, sub={user['sub']}): {len(docs)} document(s)")
        for d in docs:
            print(f"   - claim_id={d['claim_id']}, note_type={d['note_type']}")
        all_docs.extend(docs)

    # Bulk load
    bulk_load(client, all_docs)

    print("\n" + "=" * 70)
    print("Sample-data load complete!")
    print("=" * 70)
    print(f"\n✅ Loaded {len(all_docs)} document(s) across {len(users)} user(s)")
    print(f"   Index: {CLAIM_NOTES_INDEX}")
    print("\n📋 Next Steps:")
    print("   1. Verify per-user RLS via search_claim_notes (notebook 07)")
    print("   2. Confirm disjointness: user A's claim_ids never appear under user B's filter")


if __name__ == "__main__":
    main()
