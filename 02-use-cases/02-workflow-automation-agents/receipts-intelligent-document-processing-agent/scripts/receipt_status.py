#!/usr/bin/env python3
"""Look up what happened to a receipt — the one-command replacement for the
cross-three-log-groups forensic dig.

  # one receipt's fate (a single GetItem on receiptId = hash(s3_uri)):
  python3 scripts/receipt_status.py --s3-uri s3://receipts-inbox-<acct>-us-west-2/receipts/sroie/012.jpg

  # every error today (a single GSI query on status):
  python3 scripts/receipt_status.py --status error

  # everything that went to review:
  python3 scripts/receipt_status.py --status needs_review

Backed by the ProcessingRuns ledger table (fed via EventBridge). O(1) per receipt,
O(matches) per status — never a log scan.
"""

import argparse
import hashlib
import json

import boto3


def receipt_id(s3_uri: str) -> str:
    """Must match app/receiptsagent/parsing.py:receipt_id."""
    return "rcpt-" + hashlib.sha256((s3_uri or "").encode()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--table", default="ReceiptsAgent-ProcessingRuns")
    ap.add_argument("--s3-uri", help="look up one receipt by its S3 URI")
    ap.add_argument("--status", help="list all runs with this status (processed/needs_review/deferred/error)")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    if args.s3_uri:
        rid = receipt_id(args.s3_uri)
        item = table.get_item(Key={"receiptId": rid}).get("Item")
        if not item:
            print(
                f"no ledger row for {args.s3_uri} (receiptId={rid}). "
                "Not processed yet, or processed before the ledger existed."
            )
            return
        print(json.dumps(item, indent=2, default=str))
        return

    if args.status:
        resp = table.query(
            IndexName="status-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("status").eq(args.status),
            ScanIndexForward=False,  # newest first
            Limit=args.limit,
        )
        items = resp.get("Items", [])
        print(f"{len(items)} run(s) with status={args.status}:\n")
        for it in items:
            print(
                f"  {it.get('processedAt', '?')}  {it.get('s3Uri', '?')}  "
                f"rung={it.get('rung', '?')}  merchant={it.get('merchant', '')}  "
                f"{('ERROR: ' + it['error']) if it.get('error') else it.get('validatorConcerns', '')}"
            )
        return

    ap.error("pass --s3-uri or --status")


if __name__ == "__main__":
    main()
