---
name: "AWS Agent Registry Kiro Power"
displayName: "AWS Agent Registry for Publisher workflow"
description: "Publish and discover agents and tools in AWS Agent Registry — create MCP and A2A records, submit for approval"
keywords: ["agentcore", "bedrock", "registry", "boto3", "python", "agent", "mcp", "a2a"]
author: "anantmu"
---

# AWS Agent Registry

## Overview

AWS Agent Registry is a service that provides a central catalog to govern and control access to AI agents and tools across an AWS organization.

This power covers **publisher persona operations** — creating and managing registry records (MCP and A2A protocols), submitting them for approval, and searching the catalog. Admin operations (create/delete registry, approve records) are out of scope here.

Record status flow: `DRAFT` → `PENDING_APPROVAL` → `APPROVED`


## Files in This Power

```
aws-agent-registry/
├── POWER.md
└── steering/
    └── publisher-workflow.md   ← step-by-step publisher workflow
```

IAM setup and boto3 venv configuration are handled as prerequisites separately.

## Available Steering Files

- **publisher-workflow** — end-to-end publisher workflow (assumes registry and boto3 venvs already set up)

## AWS Agent Registry — Publisher APIs

| API | Description |
|-----|-------------|
| `ListRegistries` | List registries visible to the publisher |
| `GetRegistry` | Get details of a specific registry |
| `CreateRegistryRecord` | Publish an A2A or MCP record to a registry |
| `GetRegistryRecord` | Get details of a specific record |
| `ListRegistryRecords` | List all records in a registry |
| `UpdateRegistryRecord` | Update an existing record (resets status to DRAFT) |
| `DeleteRegistryRecord` | Delete a record you own |
| `SubmitRegistryRecordForApproval` | Move a DRAFT record to PENDING_APPROVAL |


## Notes

- Publisher **cannot** `CreateRegistry`, `DeleteRegistry`, `UpdateRegistry`, or `UpdateRegistryRecordStatus` (approve/reject) — those are admin-only.
- Record status flow: `DRAFT` → `PENDING_APPROVAL` → `APPROVED`
- Updating a record resets its status back to `DRAFT` — must re-submit for approval.

## Troubleshooting

**`NoCredentialsError`** — run `aws configure` or set `AWS_PROFILE`.

**`ResourceNotFoundException`** — registry or record ID is wrong; use `list_registries()` or `list_registry_records()` to get valid IDs.

**`ServiceQuotaExceededException`** — account has hit the 5-registry limit; ask an admin to delete unused registries.

**Search returns nothing** — records must be `APPROVED` and the OpenSearch index needs ~30s after approval to update.

**Name validation error** — record names must match `[a-zA-Z][a-zA-Z0-9_]{0,63}` — no hyphens.

**`update_registry_record` resets status** — any field update moves the record back to `DRAFT`; re-submit for approval after updating.
