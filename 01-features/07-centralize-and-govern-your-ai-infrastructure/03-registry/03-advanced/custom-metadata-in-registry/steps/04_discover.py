"""Step 4 (consumer): find approved records with a search query plus custom metadata filters.

Search only returns APPROVED records, so refunds-mcp and customer-pii-mcp, which the approval
function rejected, never appear. The script runs the same query twice: without filters, to show
every approved match, and with filters on environment, businessUnit and securityScanStatus. The
filters exclude any match outside prod, payments and a passed scan, such as
payments-ledger-sandbox-mcp (environment=dev), and the script prints each exclusion with the field
that caused it. (marketing-analytics-mcp is approved too, but it doesn't match "ledger lookup".)
It fails unless the filtered result is exactly payments-ledger-mcp.
"""

import time

from common import RECORD_NAME, client, need

QUERY = "ledger lookup"
REQUIRED = {"environment": "prod", "businessUnit": "payments", "securityScanStatus": "PASSED"}
FILTERS = {"$and": [{f"customMetadata.{field}": {"$eq": value}} for field, value in REQUIRED.items()]}

registry_id = need("registry_id", "01_define_schema")
dp = client("consumer", "agent-registry")


def search(**extra):
    return dp.search_discoverable_registry_records(
        registryIds=[registry_id], searchQuery=QUERY, maxResults=20, **extra
    )["registryRecords"]


def describe(record):
    m = record.get("customMetadata", {})
    return f"{record['name']}: " + ", ".join(f"{k}={m.get(k)}" for k in REQUIRED)


# A newly approved record can take from seconds to a few minutes to reach the search index,
# so retry for up to 3 minutes.
for attempt in range(19):
    filtered = search(filters=FILTERS)
    if any(r["name"] == RECORD_NAME for r in filtered):
        break
    print(f"   {RECORD_NAME} not in search results yet; retrying in 10 s")
    time.sleep(10)
else:
    raise SystemExit(f"{RECORD_NAME} did not appear in search within 3 minutes (is it APPROVED?)")
unfiltered = search()

print(f"search '{QUERY}', no filters (approved records only):")
for r in unfiltered:
    print(f"  {describe(r)}")

print(f"\nsearch '{QUERY}' where " + " AND ".join(f"{k}={v}" for k, v in REQUIRED.items()) + ":")
for r in filtered:
    print(f"  {describe(r)}")

filtered_names = {r["name"] for r in filtered}
for r in unfiltered:
    if r["name"] not in filtered_names:
        m = r.get("customMetadata", {})
        why = ", ".join(f"{k}={m.get(k)}" for k, v in REQUIRED.items() if m.get(k) != v)
        print(f"excluded by the filter: {r['name']} ({why})")

if filtered_names != {RECORD_NAME}:
    raise SystemExit(f"expected only {RECORD_NAME} in the filtered result, got {sorted(filtered_names)}")
