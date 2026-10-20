"""Step 5 (consumer): find approved records with a search query plus custom metadata filters.

Only APPROVED records are returned, so the record appears only after step 4 approves it.
"""

import time

from common import RECORD_NAME, client, need

registry_id = need("registry_id", "01_define_schema")
dp = client("consumer", "agent-registry")

# A newly approved record can take from seconds to a few minutes to reach the search index,
# so retry for up to 3 minutes.
for attempt in range(19):
    matches = dp.search_discoverable_registry_records(
        registryIds=[registry_id],
        searchQuery="ledger lookup",
        filters={
            "$and": [
                {"customMetadata.environment": {"$eq": "prod"}},
                {"customMetadata.businessUnit": {"$eq": "payments"}},
                {"customMetadata.securityScanStatus": {"$eq": "PASSED"}},
            ]
        },
    )
    records = matches["registryRecords"]
    if any(r["name"] == RECORD_NAME for r in records):
        break
    print(f"   {RECORD_NAME} not in search results yet; retrying in 10 s")
    time.sleep(10)
else:
    raise SystemExit(f"{RECORD_NAME} did not appear in search within 3 minutes (is it APPROVED?)")

print("search: 'ledger lookup' where environment=prod AND businessUnit=payments AND securityScanStatus=PASSED")
for r in records:
    m = r.get("customMetadata", {})
    print(
        f"  {r['name']} ({r['recordType']}): "
        + ", ".join(
            f"{k}={m.get(k)}"
            for k in ("environment", "businessUnit", "ownerTeam", "dataClassification", "securityScanStatus")
        )
    )
