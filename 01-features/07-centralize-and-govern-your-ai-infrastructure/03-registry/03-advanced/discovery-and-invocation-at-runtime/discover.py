"""Inspect discoverable MCP/A2A records. Never connect to a descriptor-supplied URL."""

import argparse
import json
import re
from contextlib import closing

import boto3


def discover(client, registry, query, limit=10):
    if not re.fullmatch(r"arn:aws:agent-registry:[a-z0-9-]+:[0-9]{12}:registry/[A-Za-z0-9]{12,16}", registry):
        raise ValueError("Use the full approved Registry ARN (commercial AWS)")
    if not query.strip() or not 1 <= limit <= 50:
        raise ValueError("Supply a nonempty query and a limit from 1 to 50")
    params = {"registryIds": [registry], "searchQuery": query, "maxResults": limit}
    targets = []
    seen = set()
    for _ in range(10):
        page = client.search_discoverable_registry_records(**params)
        for item in page["registryRecords"]:
            arn = item["recordArn"]
            if item["registryArn"] != registry or arn != f"{registry}/record/{item['recordId']}":
                raise ValueError("Record from unexpected registry")
            if item["status"] != "APPROVED" or arn in seen:
                continue
            seen.add(arn)
            key = {"MCP": "mcpServer", "AGENT": "a2aAgentCard"}.get(item["recordType"])
            if key not in item["descriptors"]:
                continue
            raw = item["descriptors"][key].get("data")
            if not raw:
                continue
            metadata = json.loads(raw)
            if not isinstance(metadata, dict):
                raise TypeError("Expected a descriptor JSON object")
            # Display data only. Even an approved record cannot instruct this client to send credentials.
            targets.append(
                {
                    "recordArn": arn,
                    "name": item["name"],
                    "descriptor": key,
                    "version": item.get("recordVersion"),
                    "metadataKeys": sorted(metadata),
                }
            )
            if len(targets) == limit:
                return targets
        if not page.get("nextToken"):
            return targets
        params["nextToken"] = page["nextToken"]
    raise RuntimeError("Search page limit reached; narrow the query")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-arn", required=True)
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    parts = args.registry_arn.split(":")
    if len(parts) != 6:
        parser.error("Use the Registry ARN")
    session = boto3.Session(region_name=parts[3])
    with closing(session.client("agent-registry")) as registry_client:
        print(json.dumps(discover(registry_client, args.registry_arn, args.query), indent=2))


if __name__ == "__main__":
    main()
