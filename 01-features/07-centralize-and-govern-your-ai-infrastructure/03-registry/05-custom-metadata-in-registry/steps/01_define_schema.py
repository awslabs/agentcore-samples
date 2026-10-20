"""Blog Step 1, admin: create the registry with the custom metadata schema every record must follow."""

import json

from common import REGISTRY_NAME, client, save_state, wait_for_registry

default_schema = {
    "type": "object",
    "properties": {
        "environment": {"type": "string", "enum": ["prod", "staging", "dev"]},
        "businessUnit": {"type": "string"},
        "ownerTeam": {"type": "string"},
        "cmdbId": {"type": "string"},
        "dataClassification": {"type": "string", "enum": ["public", "internal", "confidential", "restricted"]},
        "securityScanStatus": {"type": "string", "enum": ["PENDING", "PASSED", "FAILED"]},
        "securityScanReportUrl": {"type": "string", "format": "uri"},
    },
    "required": ["environment", "businessUnit", "securityScanStatus"],
}
# Record-type overrides add to the default schema; MCP records also accept networkEgress.
mcp_schema = {
    "type": "object",
    "properties": {"networkEgress": {"type": "string", "enum": ["none", "vpc-only", "allowlisted-internet"]}},
}

cp = client("admin", "agent-registry-control")
existing = [r for r in cp.list_registries()["registries"] if r["name"] == REGISTRY_NAME]
if existing:
    registry_arn = existing[0]["registryArn"]
    print(f"reusing registry {REGISTRY_NAME}")
else:
    registry_arn = cp.create_registry(
        name=REGISTRY_NAME,
        description="AnyCompany registry: every record carries governed custom metadata",
        approvalConfiguration={"autoApprovalRules": []},
        customMetadataSchemaConfiguration={
            "defaultSchema": json.dumps(default_schema),
            "recordTypeSchemaOverrides": [{"recordType": "MCP", "schema": json.dumps(mcp_schema)}],
        },
    )["registryArn"]
    print(f"creating registry {REGISTRY_NAME}")
registry_id = registry_arn.rsplit("/", 1)[1]
registry = wait_for_registry(cp, registry_id, "READY")
save_state(registry_id=registry_id, registry_arn=registry_arn)

print(f"\napprovalConfiguration: {json.dumps(registry['approvalConfiguration'])}")
print("custom metadata schema (* = required):")
for name, spec in default_schema["properties"].items():
    mark = "*" if name in default_schema["required"] else " "
    print(f"  {mark} {name:22} {spec.get('enum') or spec.get('format') or 'text'}")
print(f"    {'networkEgress':22} {mcp_schema['properties']['networkEgress']['enum']}  (MCP records only)")
