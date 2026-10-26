# ADR-0012: GSI Over Scan for Status and Claim-ID Queries

## Status

Accepted

## Context

The `list_pending_claims` Lambda used DynamoDB `scan` with a `FilterExpression` to find claims with `status=pending_review`. The `resolve_claim` Lambda used `scan` to find reviews by `claim_id`. Both patterns read the entire table on every invocation — O(table_size) regardless of result count.

For a demo with 10 items this is invisible, but as a production-grade sample it sets a bad example. Customers copying this pattern into real systems would hit throttling at scale.

## Decision

Add two Global Secondary Indexes:
- **`status-index`** on Claims table (partition: `status`, sort: `created_at`) — enables efficient query by claim status
- **`claim-id-index`** on Reviews table (partition: `claim_id`) — enables efficient lookup of reviews for a specific claim

Lambda handlers updated to use `table.query(IndexName=...)` instead of `table.scan(FilterExpression=...)`.

## Alternatives Rejected

- **Keep scan, add pagination** — Still O(table) over multiple pages; doesn't solve the fundamental access pattern mismatch.
- **Overloaded single-table design** — More efficient but harder to understand for a sample project. GSIs are idiomatic DynamoDB and self-documenting.

## Consequences

- Two additional GSIs per table (no extra cost on PAY_PER_REQUEST, just storage for projected attributes)
- `list_pending_claims` is now O(pending_claims) not O(all_claims)
- `resolve_claim` review lookup is O(reviews_for_claim) not O(all_reviews)
- Table names are also parameterized by stage suffix to enable multi-environment deployment
