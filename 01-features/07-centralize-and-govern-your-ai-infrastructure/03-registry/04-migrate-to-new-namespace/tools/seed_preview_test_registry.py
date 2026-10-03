#!/usr/bin/env python3
"""Create a Preview Agent Registry and seed it with a migration test matrix.

Produces a small registry that exercises every descriptor variant and the awkward shapes the
transform has to handle -- nested supplementary descriptors, per-descriptor sync sources,
credential providers, versions, unicode, and large payloads -- so a migration run can be validated
end to end without touching a production registry. On top of that base matrix it also covers:

  * dedup-key gaps -- the target key is (name, recordVersion), and Preview enforces neither part,
  * lifecycle states beyond the DRAFT/PENDING_APPROVAL/APPROVED/REJECTED set (DEPRECATED),
  * boundary values at the limits the service actually enforces,
  * records whose own history matters (edited after approval, so updatedAt > createdAt).

EVERY fixture here is a shape the live service is known to accept. The aim is a registry the
migration can be run against, so a record that cannot be created contributes nothing: it tests
registry validation rather than the migration, and shows up as noise in the run output. A rejection
is therefore a genuine failure -- the run reports it and exits 2.

An earlier revision carried 11 deliberately-invalid fixtures to probe the gap between the C2J model
and the service. They have been removed, but their conclusions are worth keeping, because each one
retires a migration risk that code reading alone leaves open:

  * The service enforces a much stricter contract than the model publishes. Ten model-legal shapes
    were all rejected: "At least one of descriptors or synchronizationConfiguration must be
    provided", "MCP descriptor type requires mcp descriptor", "mcp.server is required",
    "mcp.server must specify inlineContent", "MCP descriptor type can't have other descriptors",
    "CUSTOM descriptor type requires custom descriptor", "synchronizationConfiguration is not
    supported for the specified descriptor type". So a descriptor-less record, an empty descriptor,
    a tools-only MCP record, two descriptor variants at once, and a descriptorType contradicting
    its body are all impossible in a customer registry. In particular the transform-vs-validate
    stage disagreement (transform accepts a content-free descriptor, validate_target_request then
    rejects it) is unreachable, and descriptorType can never disagree with the inferred recordType.
  * The one descriptor-less form that IS accepted is a sync config with no descriptors (fixture A8,
    since removed -- see ONE_SYNC_FIXTURE_ONLY). It does not yield a content-free record: the service
    fetches the URL and materializes the content inline, so the migration only ever sees a
    materialized record or a CREATE_FAILED one.
  * Descriptor content is capped at 100KB in TOTAL across descriptors, not per ``inlineContent`` as
    the model's 102400 bound implies. A single descriptor at exactly 102400 is rejected, so a record
    with two max-size descriptors cannot exist. See TOTAL_CONTENT_MAX.
  * ``RegistryRecordSummary`` declares ``recordVersion`` required, but the service OMITS it for a
    record created without one, so ``_find_existing`` compares None to None and matches. The
    duplicate-creation risk that the required-ness implied does not materialize (fixture B5 stays
    in as a regression guard, since it is a valid record either way).

Live findings that are migration problems rather than retired risks. Both were reproduced against
a real registry, and both are service behaviour rather than tool defects, so neither is seeded any
more -- the fixtures that produced them were removed and the conclusions kept here instead:

  * A successful URL sync OVERWRITES the record's name and recordVersion with the values from the
    fetched document. All eight fixtures pointing at one MCP endpoint were renamed to the same
    (name, recordVersion) -- "contextstudios-mcp" / "1.2.1" -- which is exactly the target dedup key.
    The same overwrite happens again on the target side when the migration recreates the record, so a
    registry holding two records synced from one upstream cannot be migrated under distinct names at
    all: renaming them in Preview does not help, because the sync overwrites the new names too.
    This is the headline migration risk in this matrix, and it cost the tool a real defect --
    the loader used to perform the collapse silently, merging four source records into one target record
    and reporting three of them as successes. It now refuses instead (``_claim_target_record``).
    Only one fixture is seeded on the shared URL; see ONE_SYNC_FIXTURE_ONLY.
  * the target dedup key is (name, recordVersion) and Preview enforced neither part, so a Preview
    registry may legitimately hold two records with one name and no version. The tool handles this
    correctly -- the load stage claims target identities in staged order, names the second claimant
    in a per-record failure, and migrates it under a distinct name once
    ``runtime.transform.duplicateNames`` is set to ``suffix`` -- so a duplicate pair is no longer
    seeded. It only forced every run of this matrix to carry that setting.

Usage:
  python3 tools/seed_preview_test_registry.py --dry-run           # print the matrix only
  python3 tools/seed_preview_test_registry.py --profile <profile>  # create + seed
  python3 tools/seed_preview_test_registry.py --registry-id <id>  # seed an existing registry
  python3 tools/seed_preview_test_registry.py --region us-east-1 --name my-test-registry

Requires AWS credentials with bedrock-agentcore registry create permissions. Records are created
with the Preview API shapes (descriptorType + descriptors.<variant> + inlineContent).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from threading import Event

import boto3
from botocore.exceptions import ClientError, ParamValidationError

# A never-set event provides the same interruptible bounded delay as sleep while making it explicit
# that these waits exist only between state-machine polls.
_POLL_WAIT = Event()

# --- payload fixtures -------------------------------------------------------------------------

# Content rules enforced by the Preview API (discovered empirically -- a fixture that violates
# any of these is rejected with a ValidationException, so the base matrix stays inside them):
#   * mcp.server        schemaVersion must be MCP_SCHEMA_VERSION; content is server.json
#   * mcp.tools         protocolVersion must be MCP_PROTOCOL_VERSION; content is {"tools": [...]}
#   * a2a.agentCard     schemaVersion must be A2A_SCHEMA_VERSION; content is an A2A agent card
#   * skillDefinition   schemaVersion must be SKILL_SCHEMA_VERSION; content is JSON
#   * skillMd           content MUST begin with '---' YAML frontmatter
#   * custom            content MUST be a structured JSON object or array (not a bare string)
#   * synchronizationConfiguration is accepted only for MCP and A2A records, never for
#     AGENT_SKILLS or CUSTOM ("synchronizationConfiguration is not supported for the specified
#     descriptor type")
#
# Sync configuration is also resolved at creation time, asynchronously: the registry fetches the
# URL and exercises any credential provider, and the record settles in CREATE_FAILED if that
# fails. So a sync fixture needs a genuinely reachable URL, and credential-provider fixtures need
# a real OAuth2 credential provider / assumable IAM role (hence --with-credential-providers,
# which is off by default). A successful URL sync also *overwrites* the record's name and
# recordVersion with the values from the fetched document.
DEFAULT_MCP_SYNC_URL = "https://mcp.contextstudios.ai/api/public/mcp"

# ONE_SYNC_FIXTURE_ONLY -- why this matrix contains exactly one MCP sync fixture.
#
# A successful URL sync overwrites the record's name AND recordVersion with the values from the
# fetched document. That happens on BOTH sides: in the Preview registry when the fixture is seeded,
# and again in the target registry when the migration recreates it. So N records synced from one URL
# collapse into one (name, recordVersion) -- which is exactly the target dedup key -- no matter what
# names they were given. Renaming them after creation gets the Preview side past the extract stage's
# duplicate-name guard, but the service renames them again on create, so the collision simply moves to the
# load stage. There is no way to seed two records from one upstream and have both survive.
#
# This matrix therefore keeps ONE fixture on the shared URL (mcp-sync-url-reachable), which covers
# the migration path that matters: a top-level sync config becoming source.fromUrl on the
# descriptor. Five fixtures that also needed it -- A8, D2b, E1, E2, E4 -- were removed. Their
# findings are all CONFIRMED and recorded in the module docstring; testing them again needs one
# further distinct, genuinely reachable MCP endpoint per fixture, not a second name for this one.
#
# The loader refuses this collapse rather than performing it: see _claim_target_record in
# registry_api.py, added after a live run silently merged four source records into one target record.
MCP_SCHEMA_VERSION = "2025-12-11"
MCP_PROTOCOL_VERSION = "2024-11-05"
A2A_SCHEMA_VERSION = "0.3"
SKILL_SCHEMA_VERSION = "0.1.0"

# Limits used by the boundary fixtures. The first four come from the model; the fifth is
# the service's actual enforced cap, which the model does not express -- descriptor content is
# bounded in TOTAL across descriptors, not per inlineContent, so INLINE_CONTENT_MAX is unreachable.
INLINE_CONTENT_MAX = 102400  # InlineContent max, per the model (not achievable in practice)
DESCRIPTION_MAX = 4096  # Description max
RECORD_NAME_MAX = 255  # RegistryRecordName max
RECORD_VERSION_MAX = 255  # RegistryRecordVersion max
TOTAL_CONTENT_MAX = 102400  # "Total descriptor content size exceeds maximum of 100KB"

MCP_SERVER = json.dumps(
    {
        "$schema": f"https://static.modelcontextprotocol.io/schemas/{MCP_SCHEMA_VERSION}/server.schema.json",
        "name": "io.example/weather-mcp",
        "description": "Weather MCP server fixture",
        "version": "1.4.2",
        "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/api/mcp"}],
    }
)
MCP_SERVER_MINIMAL = json.dumps(
    {"name": "io.example/minimal-mcp", "description": "Minimal MCP server fixture", "version": "1.0.0"}
)


def _tools(count: int = 2) -> str:
    tools = [
        {
            "name": f"get_forecast_{index}",
            "description": "Return a forecast for a location",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 14},
                },
                "required": ["location"],
            },
        }
        for index in range(count)
    ]
    return json.dumps({"tools": tools})


def _padded_json(target_length: int, *, note: str = "boundary fixture") -> str:
    """Return a JSON object whose serialized length is exactly ``target_length``."""
    envelope = json.dumps({"note": note, "pad": ""})
    padding = target_length - len(envelope)
    if padding < 0:
        raise ValueError(f"target_length {target_length} is smaller than the envelope")
    return json.dumps({"note": note, "pad": "x" * padding})


MCP_TOOLS = _tools(2)
# ~60 KB of valid tools JSON: large but inside the 102400-character inlineContent limit.
MCP_TOOLS_LARGE = _tools(220)

A2A_CARD = json.dumps(
    {
        "name": "Travel Planner",
        "description": "Plans multi-city itineraries",
        "version": "2.1.0",
        "url": "https://agents.example.com/travel",
        "protocolVersion": A2A_SCHEMA_VERSION,
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "plan-trip",
                "name": "Plan a trip",
                "description": "Build an itinerary",
                "tags": ["travel", "planning"],
            }
        ],
    }
)
UNICODE_TEXT = "多言語対応 — Ünïcödé ✅ 🚀 «τεστ» ダミー内容"
A2A_CARD_UNICODE = json.dumps(
    {
        "name": UNICODE_TEXT,
        "description": f"Unicode agent card {UNICODE_TEXT}",
        "version": "1.0.0",
        "url": "https://agents.example.com/unicode",
        "protocolVersion": A2A_SCHEMA_VERSION,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "echo", "name": UNICODE_TEXT, "description": UNICODE_TEXT, "tags": ["unicode"]}],
    },
    ensure_ascii=False,
)
SKILL_DEFINITION = json.dumps({"name": "summarize-doc", "description": "Summarize a document", "version": "1.0.0"})
SKILL_MD = """---
name: summarize-doc
description: Summarize a document
version: 1.0.0
---
# Summarize a document

Reads a document and returns a concise summary.

## Inputs
- `uri` (string, required) - location of the document
"""
SKILL_MD_UNICODE = f"""---
name: unicode-skill
description: Unicode skill fixture
version: 2.0.0
---
# {UNICODE_TEXT}

Body text with non-ASCII content: {UNICODE_TEXT}
"""
CUSTOM_JSON = json.dumps({"kind": "internal-tool", "owner": "platform", "tier": "gold"})
# custom content must be a structured JSON object or array -- a bare JSON string is rejected.
CUSTOM_JSON_ARRAY = json.dumps([{"id": 1, "kind": "entry"}, {"id": 2, "kind": "entry"}])
LARGE_TEXT = json.dumps({"blob": "x" * 60000, "note": "large payload scenario"})

OAUTH_PROVIDER_ARN_TEMPLATE = (
    "arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default/oauth2credentialprovider/migration-test"
)
IAM_ROLE_ARN_TEMPLATE = "arn:aws:iam::{account}:role/AgentRegistryMigrationSyncTest"


def from_url(url: str, credentials: list | None = None) -> dict:
    from_url_block: dict = {"url": url}
    if credentials:
        from_url_block["credentialProviderConfigurations"] = credentials
    return {"synchronizationType": "URL", "synchronizationConfiguration": {"fromUrl": from_url_block}}


def build_matrix(
    account: str,
    region: str,
    *,
    mcp_sync_url: str = DEFAULT_MCP_SYNC_URL,
    a2a_sync_url: str | None = None,
    include_credential_providers: bool = False,
) -> list[dict]:
    """Return the record fixtures, each as {scenario, why, expect, request-fields...}.

    Every fixture is a shape the live service accepts, so a rejection during seeding is a real
    failure rather than an expected outcome.

    ``synchronizationConfiguration`` scenarios are only useful with real infrastructure, because
    the registry resolves them at creation time (see the module docstring): the URL must be
    reachable, and a credential provider / IAM role must actually exist. Records that fail those
    checks are still created but settle in ``CREATE_FAILED``.
    """
    oauth_creds = [
        {
            "credentialProviderType": "OAUTH",
            "credentialProvider": {
                "oauthCredentialProvider": {
                    "providerArn": OAUTH_PROVIDER_ARN_TEMPLATE.format(region=region, account=account),
                    "grantType": "CLIENT_CREDENTIALS",
                    "scopes": ["mcp.read", "mcp.invoke"],
                }
            },
        }
    ]
    iam_creds = [
        {
            "credentialProviderType": "IAM",
            "credentialProvider": {
                "iamCredentialProvider": {
                    "roleArn": IAM_ROLE_ARN_TEMPLATE.format(account=account),
                    "service": "bedrock-agentcore",
                    "region": region,
                }
            },
        }
    ]

    records: list[dict] = [
        # ---- MCP ---------------------------------------------------------------------------
        {
            "scenario": "mcp-server-only",
            "why": "minimal MCP record: single primary descriptor, no source",
            "target_status": "PENDING_APPROVAL",
            "name": "mcp-server-only",
            "descriptorType": "MCP",
            "descriptors": {
                "mcp": {"server": {"inlineContent": MCP_SERVER_MINIMAL, "schemaVersion": MCP_SCHEMA_VERSION}}
            },
        },
        {
            "scenario": "mcp-server-with-tools",
            "why": "nested supplementary descriptor: tools must land under mcpServer.additionalData",
            "name": "mcp-server-with-tools",
            "description": "MCP server plus a tools manifest",
            "descriptorType": "MCP",
            "descriptors": {
                "mcp": {
                    "server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION},
                    "tools": {"inlineContent": MCP_TOOLS, "protocolVersion": MCP_PROTOCOL_VERSION},
                }
            },
        },
        {
            "scenario": "mcp-sync-url-reachable",
            "why": "top-level sync config must move onto the mcpServer descriptor as source.fromUrl "
            "(uses a reachable URL so the record reaches DRAFT)",
            # The sync overwrites name and recordVersion with the values from the fetched document,
            # so restore the intended name to keep this fixture identifiable in the run reports. The service
            # renames it again when the migration recreates it, which is expected and harmless while
            # this is the only record on the URL -- see ONE_SYNC_FIXTURE_ONLY.
            "post_create_update": {"name": "mcp-sync-url-reachable"},
            "name": "mcp-sync-url-reachable",
            "description": "MCP record synced from a reachable public URL",
            "descriptorType": "MCP",
            "descriptors": {"mcp": {"server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION}}},
            **from_url(mcp_sync_url),
        },
        {
            "scenario": "mcp-shared-name-first-version",
            "why": "recordVersion set (first half of the name+version uniqueness pair)",
            "name": "mcp-shared-name",
            "recordVersion": "1.0",
            "descriptorType": "MCP",
            "descriptors": {"mcp": {"server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION}}},
        },
        {
            "scenario": "mcp-same-name-second-version",
            "why": "same name as the previous record with a different recordVersion",
            "name": "mcp-shared-name",
            "recordVersion": "2.0-beta.1",
            "descriptorType": "MCP",
            "descriptors": {"mcp": {"server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION}}},
        },
        {
            "scenario": "mcp-tools-large-payload",
            "why": "large supplementary descriptor (~60 KB tools manifest) exercises batching",
            "name": "mcp-tools-large-payload",
            "descriptorType": "MCP",
            "descriptors": {
                "mcp": {
                    "server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION},
                    "tools": {"inlineContent": MCP_TOOLS_LARGE, "protocolVersion": MCP_PROTOCOL_VERSION},
                }
            },
        },
        # ---- A2A ---------------------------------------------------------------------------
        {
            "scenario": "a2a-card-only",
            "why": "minimal A2A record (real Preview nesting is descriptors.a2a.agentCard)",
            "target_status": "APPROVED",
            "name": "a2a-card-only",
            "descriptorType": "A2A",
            "descriptors": {"a2a": {"agentCard": {"inlineContent": A2A_CARD, "schemaVersion": A2A_SCHEMA_VERSION}}},
        },
        {
            "scenario": "a2a-card-no-schema-version",
            "why": "descriptor with no explicit version: target dataSchemaVersion must simply be absent",
            "name": "a2a-card-no-schema-version",
            "descriptorType": "A2A",
            "descriptors": {"a2a": {"agentCard": {"inlineContent": A2A_CARD}}},
        },
        # The A2A sync fixture is opt-in on --a2a-sync-url, like the credential-provider fixtures are
        # on --with-credential-providers, because it needs infrastructure this script cannot conjure.
        # It used to fall back to an unreachable placeholder URL, on the grounds that the resulting
        # CREATE_FAILED source record was itself a useful fixture. It is not worth the cost: the service
        # cannot fetch that URL either, so the record is recreated in CREATE_FAILED and the load
        # stage reports a failed record on every single run. That failure says nothing about the
        # migration -- the tool faithfully migrated a record whose upstream does not exist -- and it
        # buries the failures that do mean something. The CREATE_FAILED *source status* path (which
        # the migration reports and leaves in DRAFT, by design) is covered by unit tests instead.
        {
            "scenario": "a2a-card-unicode",
            "why": "non-ASCII content and description must round-trip unchanged",
            "name": "a2a-card-unicode",
            "description": f"Unicode description {UNICODE_TEXT}",
            "descriptorType": "A2A",
            "descriptors": {
                "a2a": {"agentCard": {"inlineContent": A2A_CARD_UNICODE, "schemaVersion": A2A_SCHEMA_VERSION}}
            },
        },
        # ---- AGENT_SKILLS ------------------------------------------------------------------
        {
            "scenario": "skills-definition-only",
            "why": "skillDefinition alone maps to agentSkillsDefinition",
            "name": "skills-definition-only",
            "descriptorType": "AGENT_SKILLS",
            "descriptors": {
                "agentSkills": {
                    "skillDefinition": {"inlineContent": SKILL_DEFINITION, "schemaVersion": SKILL_SCHEMA_VERSION}
                }
            },
        },
        {
            "scenario": "skills-definition-with-md",
            "why": "nested supplementary descriptor: skillMd under agentSkillsDefinition.additionalData",
            "name": "skills-definition-with-md",
            "description": "Skill definition plus human-readable markdown",
            "descriptorType": "AGENT_SKILLS",
            "descriptors": {
                "agentSkills": {
                    "skillDefinition": {"inlineContent": SKILL_DEFINITION, "schemaVersion": SKILL_SCHEMA_VERSION},
                    "skillMd": {"inlineContent": SKILL_MD},
                }
            },
        },
        {
            "scenario": "skills-md-only",
            "why": "markdown-only skill: KNOWN REJECTION (the service allows only agentSkillsDefinition/custom for SKILL)",
            "name": "skills-md-only",
            "descriptorType": "AGENT_SKILLS",
            "descriptors": {"agentSkills": {"skillMd": {"inlineContent": SKILL_MD}}},
        },
        {
            "scenario": "skills-definition-no-version",
            "why": "skills definition without a schemaVersion",
            "name": "skills-definition-no-version",
            "descriptorType": "AGENT_SKILLS",
            "descriptors": {"agentSkills": {"skillDefinition": {"inlineContent": SKILL_DEFINITION}}},
        },
        {
            "scenario": "skills-definition-md-unicode-versioned",
            "why": "skills with markdown, unicode, and a recordVersion together",
            "name": "skills-unicode.v2",
            "recordVersion": "2.0",
            "descriptorType": "AGENT_SKILLS",
            "descriptors": {
                "agentSkills": {
                    "skillDefinition": {"inlineContent": SKILL_DEFINITION, "schemaVersion": SKILL_SCHEMA_VERSION},
                    "skillMd": {"inlineContent": SKILL_MD_UNICODE},
                }
            },
        },
        # ---- CUSTOM ------------------------------------------------------------------------
        {
            "scenario": "custom-json-array",
            "why": "custom content is a JSON array rather than an object",
            "name": "custom-json-array",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": CUSTOM_JSON_ARRAY}},
        },
        {
            "scenario": "custom-json",
            "why": "custom descriptor with JSON content and a description",
            "target_status": "REJECTED",
            "name": "custom-json",
            "description": "Custom record carrying JSON metadata",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": CUSTOM_JSON}},
        },
        {
            "scenario": "custom-large-payload",
            "why": "large custom payload (~60 KB)",
            "name": "custom-large-payload",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": LARGE_TEXT}},
        },
        {
            "scenario": "custom-unicode",
            "why": "non-ASCII custom content and description",
            "name": "custom-unicode",
            "description": f"Custom unicode {UNICODE_TEXT}",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": UNICODE_TEXT}, ensure_ascii=False)}},
        },
        {
            "scenario": "custom-dotted-slashed-name-versioned",
            "why": "name using the full allowed charset (dots, dashes, slashes) plus a version",
            "name": "team.platform/custom-record_v3",
            "recordVersion": "3.1.4",
            "description": "Name exercising dots, slash, underscore and dash",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": CUSTOM_JSON}},
        },
        {
            "scenario": "custom-max-length-name",
            "why": "255-character name (upper bound of the Preview name constraint)",
            "name": "custom-max-length-" + "n" * (RECORD_NAME_MAX - len("custom-max-length-")),
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "max length name scenario"})}},
        },
        # A duplicate-name pair (two records sharing one name with no recordVersion) used to sit
        # here. It was removed: it is not a tool defect. The target dedup key is (name, recordVersion),
        # Preview enforced neither, and the load stage already claims target identities in staged
        # order -- refusing the second claimant and naming both records in the failure report, or
        # migrating it under a distinct name once ``runtime.transform.duplicateNames`` is set to
        # ``suffix``. Keeping the pair only forced every run of this matrix to carry that setting,
        # which masked the rest of the matrix.
        # ---- the one shape-space gap in the Preview model the service allows -----------
        # The model is far looser than the service: it requires only registryId + name +
        # descriptorType, no descriptor sub-structure has a required member, Descriptors is a
        # plain structure rather than a union, and descriptorType may contradict the body. Ten
        # fixtures exploiting that were seeded live and the service rejected every one, so they
        # were dropped -- a record that cannot be created cannot reach the migration. Their
        # conclusions are in the module docstring.
        #
        # A8 (a sync config with NO descriptors) used to sit here. Its question is answered and
        # recorded in the module docstring -- the service fetches the URL and materializes the
        # content, so the migration never sees a content-free record -- and it can no longer be
        # seeded alongside the other sync fixtures. See ONE_SYNC_FIXTURE_ONLY.
        # ---- dedup-key gaps: the target key is (name, recordVersion), Preview enforces neither -
        # A B1 pair (two records syncing from the SAME url under DIFFERENT names) used to sit here.
        # Its finding is CONFIRMED and recorded in the module docstring, and it is service
        # behaviour rather than a tool defect, so the fixtures were removed. It also could not
        # coexist with the rest of the matrix: the collision it creates aborts the whole extract,
        # so no other fixture could be tested while it was present. See the docstring note on
        # restoring distinct names after creation for how the remaining sync fixtures avoid it.
        {
            "scenario": "B2-case-only-name-difference-upper",
            "why": "the name pattern is case-sensitive, but nothing states whether the target registry's (name, recordVersion) key is",
            "expect": "the migration treats these as two distinct records and issues two creates. If "
            "the target key is case-insensitive the second create collides at the service",
            "name": "B2-Payments-MCP",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"case": "upper"})}},
        },
        {
            "scenario": "B2-case-only-name-difference-lower",
            "why": "second half of B2",
            "expect": "see B2-case-only-name-difference-upper",
            "name": "b2-payments-mcp",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"case": "lower"})}},
        },
        {
            "scenario": "B3-name-without-trailing-slash",
            "why": "the name pattern permits a trailing '/', so two names can differ only by a separator",
            "expect": "the migration treats these as distinct. If the service normalizes separators the second create collides",
            "name": "b3-team/svc",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"variant": "no-trailing-slash"})}},
        },
        {
            "scenario": "B3b-name-with-trailing-slash",
            "why": "second half of B3",
            "expect": "see B3-name-without-trailing-slash",
            "name": "b3-team/svc/",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"variant": "trailing-slash"})}},
        },
        {
            "scenario": "B3c-name-with-double-slash",
            "why": "the pattern permits '//' inside a name",
            "expect": "accepted by the target name pattern too; included to prove neither side rejects it",
            "name": "b3c-team//svc",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"variant": "double-slash"})}},
        },
        {
            "scenario": "B4-case-only-version-difference-upper",
            "why": "RegistryRecordVersion permits both cases, and _normalized_version is an exact "
            "string compare, so 'V1.0' and 'v1.0' are two different keys to the migration",
            "expect": "two records with the SAME name are created, distinguished only by version "
            "case. _claim_name does not fire. If the service compares versions case-insensitively the second "
            "create collides at the service",
            "name": "b4-shared-name",
            "recordVersion": "V1.0",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"version": "V1.0"})}},
        },
        {
            "scenario": "B4b-case-only-version-difference-lower",
            "why": "second half of B4",
            "expect": "see B4-case-only-version-difference-upper",
            "name": "b4-shared-name",
            "recordVersion": "v1.0",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"version": "v1.0"})}},
        },
        {
            "scenario": "B5-no-record-version",
            "why": "RegistryRecordSummary REQUIRES recordVersion but CreateRegistryRecord and "
            "GetRegistryRecord do not. If the service synthesizes one in list summaries, "
            "_find_existing compares synthesized-vs-None and never matches",
            "expect": "ANSWERED by the live run: the service OMITS recordVersion from the summary "
            "despite the model marking it required, so _find_existing compares None-to-None and "
            "matches. No duplication. Keep the fixture as a regression guard in case that changes",
            "name": "b5-no-record-version",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "version-less record"})}},
        },
        # ---- lifecycle states the base matrix does not produce ---------------------------
        {
            "scenario": "C1-deprecated",
            "why": "DEPRECATED is in the status enum and no other fixture reaches it. It is in the "
            "migration's REPRODUCIBLE_SOURCE_STATUSES, so it is replayed rather than warned about",
            "expect": "transform emits NO warning, and the load stage drives the target record to "
            "DEPRECATED via UpdateRegistryRecordStatus. Confirms the deprecation survives",
            "target_status": "DEPRECATED",
            "name": "c1-deprecated",
            "description": "Record deprecated in Preview",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "deprecated fixture"})}},
        },
        {
            "scenario": "C2-approved-then-content-edited",
            "why": "an APPROVED record whose content was edited after approval -- so updatedAt > "
            "createdAt, which is what an incremental watermark actually keys on",
            "expect": "extract picks it up on an INCREMENTAL run keyed on the edit time, and the "
            "load stage restores APPROVED after creating it in DRAFT",
            "target_status": "APPROVED",
            "post_create_update": {"description": "Edited after approval, so updatedAt > createdAt"},
            "name": "c2-approved-then-edited",
            "description": "Approved, then edited",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "edited after approval"})}},
        },
        # ---- boundary values, at the limit the service actually enforces ---------------
        {
            "scenario": "D1-inline-content-near-max",
            "why": f"the largest single descriptor the service accepts. The model's per-field "
            f"InlineContent bound of {INLINE_CONTENT_MAX} is unreachable -- the real cap is "
            f"{TOTAL_CONTENT_MAX} summed ACROSS descriptors (a fixture at exactly {INLINE_CONTENT_MAX} "
            "was rejected with 'Total descriptor content size exceeds maximum of 100KB'), so this "
            f"sits just under it. The other large-payload fixtures stop at ~60 KB",
            "expect": "transform and validate_target_request both accept it; the target create body carries a "
            "~100 KB descriptor. The largest single-descriptor request the migration will issue",
            "name": "d1-inline-content-near-max",
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": _padded_json(TOTAL_CONTENT_MAX - 2048, note="D1 boundary")}},
        },
        {
            "scenario": "D2-description-at-max",
            "why": f"Description max is {DESCRIPTION_MAX} and is marked sensitive in the model",
            "expect": "carried through unchanged as the target registry description",
            "name": "d2-description-at-max",
            "description": "D" * DESCRIPTION_MAX,
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "max description"})}},
        },
        # D2b (a source-backed record with no description at all) needed the shared sync URL too.
        # See ONE_SYNC_FIXTURE_ONLY.
        {
            "scenario": "D3-record-version-at-max",
            "why": f"RegistryRecordVersion max is {RECORD_VERSION_MAX} with pattern [a-zA-Z0-9.-]+ "
            "(note: no underscore)",
            "expect": "carried through unchanged and used as half the target dedup key",
            "name": "d3-record-version-at-max",
            "recordVersion": "1." + "9" * (RECORD_VERSION_MAX - 2),
            "descriptorType": "CUSTOM",
            "descriptors": {"custom": {"inlineContent": json.dumps({"note": "max version"})}},
        },
        {
            "scenario": "D4-primary-and-additional-near-total-max",
            "why": "two descriptors on one record, sized to sit just under the enforced total. The "
            f"model's per-field bound of {INLINE_CONTENT_MAX} implies two max-size descriptors are "
            f"legal, but the real cap is {TOTAL_CONTENT_MAX} SUMMED across them, so that record "
            "cannot exist -- this is the closest a multi-descriptor record can get",
            "expect": "the largest multi-descriptor request the migration will issue; the tools "
            "payload must still land under mcpServer.additionalData intact",
            "name": "d4-both-descriptors-near-total-max",
            "descriptorType": "MCP",
            "descriptors": {
                "mcp": {
                    "server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION},
                    "tools": {
                        "inlineContent": json.dumps({"tools": json.loads(_tools(220))["tools"], "pad": "x" * 30000}),
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                    },
                }
            },
        },
        # ---- records whose own history is what the re-run logic keys on -----------------
        # E1 (renamed after creation), E2 (content edited after creation) and E4 (description edited
        # after creation) used to sit here. All three are source-backed by definition -- that is what
        # the re-run logic keys on -- so all three needed the shared sync URL. Their findings are
        # CONFIRMED and recorded in the module docstring. See ONE_SYNC_FIXTURE_ONLY: restoring them
        # needs three further distinct reachable MCP endpoints, one per fixture.
    ]

    if a2a_sync_url:
        # Opt-in: A2A supports source.fromUrl on the a2aAgentCard descriptor, but only a genuinely
        # reachable agent-card URL exercises it. Without one there is nothing to test, so the fixture
        # is omitted rather than pointed at a placeholder (see the note above).
        records.append(
            {
                "scenario": "a2a-card-sync-url",
                "why": "A2A supports source.fromUrl on the a2aAgentCard descriptor; the sync config "
                "must move onto that descriptor as source.fromUrl",
                "expect": "the record reaches DRAFT and the migration recreates it with the source "
                "on the descriptor. Note the sync overwrites the name from the fetched card",
                "name": "a2a-card-sync-url",
                "descriptorType": "A2A",
                "descriptors": {"a2a": {"agentCard": {"inlineContent": A2A_CARD, "schemaVersion": A2A_SCHEMA_VERSION}}},
                **from_url(a2a_sync_url),
            }
        )

    if include_credential_providers:
        # Opt-in: these only reach DRAFT when the referenced OAuth2 credential provider and IAM
        # role actually exist in this account, because the registry uses them during creation.
        records.extend(
            [
                {
                    "scenario": "mcp-sync-url-oauth-credentials",
                    "why": "credentialProviderConfigurations must survive the move onto the descriptor "
                    "(requires a real OAuth2 credential provider named migration-test)",
                    "name": "mcp-sync-url-oauth-credentials",
                    "descriptorType": "MCP",
                    "descriptors": {
                        "mcp": {
                            "server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION},
                            "tools": {"inlineContent": MCP_TOOLS, "protocolVersion": MCP_PROTOCOL_VERSION},
                        }
                    },
                    **from_url(mcp_sync_url, oauth_creds),
                },
                {
                    "scenario": "mcp-sync-url-iam-credentials",
                    "why": "IAM credential provider on the descriptor source "
                    "(requires an assumable AgentRegistryMigrationSyncTest role)",
                    "name": "mcp-sync-url-iam-credentials",
                    "descriptorType": "MCP",
                    "descriptors": {
                        "mcp": {"server": {"inlineContent": MCP_SERVER, "schemaVersion": MCP_SCHEMA_VERSION}}
                    },
                    **from_url(mcp_sync_url, iam_creds),
                },
            ]
        )

    return records


def _error_code(error: ClientError) -> str:
    """The service's error code for a ClientError, or ``"ClientError"`` when it carries none."""
    return str((getattr(error, "response", None) or {}).get("Error", {}).get("Code", "ClientError"))


#: Codes that mean "the state machine does not allow this transition from here", as opposed to a
#: permissions problem or a throttle. Only these are worth retrying the long way round.
_STATE_MACHINE_REFUSALS = frozenset({"ValidationException", "ConflictException", "InvalidRequestException"})


def wait_until_stable(client, registry_id: str, record_id: str, attempts: int = 30, delay: int = 5) -> str:
    """Poll a record until it leaves CREATING/UPDATING and returns its settled status."""
    status = "UNKNOWN"
    for _ in range(attempts):
        response = client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = str(response.get("status", "UNKNOWN"))
        if status not in ("CREATING", "UPDATING"):
            return status
        _POLL_WAIT.wait(delay)
    return status


def wait_until_ready(client, registry_id: str, attempts: int = 40, delay: int = 10) -> str:
    status = "UNKNOWN"
    for _ in range(attempts):
        response = client.get_registry(registryId=registry_id)
        status = str(response.get("status", "UNKNOWN"))
        if status == "READY":
            return status
        if "FAILED" in status:
            print(f"  registry status={status} reason={response.get('statusReason')}")
            return status
        print(f"  waiting... status={status}", flush=True)
        _POLL_WAIT.wait(delay)
    return status


def apply_status(client, registry_id: str, record_id: str, target_status: str) -> tuple[bool, str]:
    """Drive a DRAFT record to ``target_status``, returning (ok, detail).

    Mirrors the target registry ladder the migration itself uses, because the Preview state machine is the
    same shape: PENDING_APPROVAL is one submit; APPROVED/REJECTED are only reachable from
    PENDING_APPROVAL; DEPRECATED is attempted directly first and falls back to going through
    approval, since a service may require the record to have left DRAFT.
    """
    reason = f"Seeded as {target_status.lower()} fixture for the migration test matrix"
    try:
        if target_status == "DEPRECATED":
            try:
                client.update_registry_record_status(
                    registryId=registry_id,
                    recordId=record_id,
                    status=target_status,
                    statusReason=reason,
                )
                return True, "updateStatus=DEPRECATED"
            except ClientError as error:
                # Only a state-machine refusal is worth the long way round. An AccessDenied or a
                # throttle is not "this transition needs approval first", and treating it as one ran
                # three more calls that would fail the same way while discarding the real cause --
                # the sibling handlers below already check the code, so this one now does too.
                if _error_code(error) not in _STATE_MACHINE_REFUSALS:
                    raise
                # A service that only allows this out of an approved state: go the long way.
                client.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
                wait_until_stable(client, registry_id, record_id)
                client.update_registry_record_status(
                    registryId=registry_id,
                    recordId=record_id,
                    status="APPROVED",
                    statusReason=reason,
                )
                wait_until_stable(client, registry_id, record_id)
                client.update_registry_record_status(
                    registryId=registry_id,
                    recordId=record_id,
                    status=target_status,
                    statusReason=reason,
                )
                return True, "submit -> APPROVED -> DEPRECATED"

        client.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
        if target_status == "PENDING_APPROVAL":
            return True, "submitForApproval"
        wait_until_stable(client, registry_id, record_id)
        client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status=target_status,
            statusReason=reason,
        )
        return True, f"submit -> updateStatus={target_status}"
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "ClientError")
        message = error.response.get("Error", {}).get("Message", str(error))
        return False, f"{code}: {message[:160]}"


def apply_post_create_update(client, registry_id: str, record_id: str, patch: dict) -> tuple[bool, str]:
    """Apply an UpdateRegistryRecord after creation, so updatedAt > createdAt.

    Preview PATCH semantics: ``name`` is a bare value, while ``description`` and ``descriptors``
    take the ``{"optionalValue": ...}`` wrapper. Fixtures supply already-wrapped values for the
    wrapped fields, so this only wraps the plain ``description`` shorthand.
    """
    request: dict = {"registryId": registry_id, "recordId": record_id}
    for field, value in patch.items():
        if field == "description" and not (isinstance(value, dict) and "optionalValue" in value):
            request[field] = {"optionalValue": value}
        else:
            request[field] = value
    try:
        client.update_registry_record(**request)
        return True, "updated: " + ", ".join(sorted(patch))
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "ClientError")
        message = error.response.get("Error", {}).get("Message", str(error))
        return False, f"{code}: {message[:160]}"
    except ParamValidationError as error:
        # A malformed patch is one fixture's problem; it must not abandon the rest of the seed.
        return False, f"ParamValidationError: {str(error)[:200]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default=None, help="AWS profile to use")
    parser.add_argument("--name", default=None, help="registry name (default: generated)")
    parser.add_argument("--registry-id", default=None, help="seed this existing registry instead of creating one")
    parser.add_argument("--dry-run", action="store_true", help="print the matrix without calling AWS")
    parser.add_argument(
        "--mcp-sync-url",
        default=DEFAULT_MCP_SYNC_URL,
        help="reachable MCP endpoint for the sync-from-URL fixtures (the registry fetches it at creation)",
    )
    parser.add_argument(
        "--a2a-sync-url",
        default=None,
        help="reachable agent-card URL; without it the A2A sync fixture settles in CREATE_FAILED",
    )
    parser.add_argument(
        "--with-credential-providers",
        action="store_true",
        help="also seed credential-provider fixtures (needs a real OAuth2 provider and IAM role)",
    )
    args = parser.parse_args(argv)

    matrix_account = "000000000000"
    session = None
    if not args.dry_run:
        session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
        matrix_account = session.client("sts").get_caller_identity()["Account"]
    records = build_matrix(
        matrix_account,
        args.region,
        mcp_sync_url=args.mcp_sync_url,
        a2a_sync_url=args.a2a_sync_url,
        include_credential_providers=args.with_credential_providers,
    )

    if args.dry_run:
        print(f"{len(records)} scenarios:\n")
        by_type: dict[str, int] = {}
        for record in records:
            by_type[record["descriptorType"]] = by_type.get(record["descriptorType"], 0) + 1
            ts = record.get("target_status", "")
            suffix = f"  -> {ts}" if ts else ""
            print(f"  [{record['descriptorType']:<13}] {record['scenario']}{suffix}")
            print(f"                  {record['why']}")
            if record.get("expect"):
                print(f"                  expect: {record['expect']}")
            if record.get("post_create_update"):
                print(f"                  then:   update {sorted(record['post_create_update'])}")
        print("\nper descriptorType:", by_type)
        return 0

    client = session.client("bedrock-agentcore-control", region_name=args.region)

    registry_id = args.registry_id
    if not registry_id:
        registry_name = args.name or f"agentcore-migration-testmatrix-{int(time.time())}"
        print(f"Creating Preview registry {registry_name} in {args.region} (account {matrix_account})...")
        created = client.create_registry(
            name=registry_name,
            description="Preview test matrix for the Agent Registry migration tool",
            authorizerType="AWS_IAM",
            approvalConfiguration={"autoApproval": False},
            clientToken=str(uuid.uuid4()),
        )
        registry_id = created.get("registryId") or str(created.get("registryArn", "")).rsplit("/", 1)[-1]
        print(f"  registryId={registry_id} arn={created.get('registryArn')}")
        status = wait_until_ready(client, registry_id)
        print(f"  status={status}")
        if status != "READY":
            print("Registry did not become READY; not seeding.")
            return 1

    print(f"\nSeeding {len(records)} records into {registry_id}...")

    created_records: list[dict] = []
    rejected: list[dict] = []
    # Fixture-only keys, stripped from each record before it becomes a CreateRegistryRecord request.
    fixture_keys = ("scenario", "target_status", "post_create_update", "why", "expect")
    for record in records:
        # Copied rather than consumed with pop(): mutating build_matrix()'s output in place meant the
        # matrix could not be reused -- for a retry, or a second registry -- after one pass over it.
        fixture = {
            "scenario": record["scenario"],
            "target_status": record.get("target_status"),
            "post_create_update": record.get("post_create_update"),
        }
        payload = {key: value for key, value in record.items() if key not in fixture_keys}

        request = {"registryId": registry_id, "clientToken": str(uuid.uuid4()), **payload}
        try:
            response = client.create_registry_record(**request)
            record_id = response.get("recordId") or str(response.get("recordArn", "")).rsplit("/", 1)[-1]
            created_records.append({**fixture, "recordId": record_id})
            print(f"  OK   {fixture['scenario']} -> {record_id}")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "ClientError")
            message = error.response.get("Error", {}).get("Message", str(error))
            rejected.append({**fixture, "reason": f"{code}: {message}"})
            print(f"  REJ  {fixture['scenario']} -> {code}: {message[:150]}")

    print(f"\nCreated {len(created_records)} / {len(records)} records in registry {registry_id}")
    if rejected:
        # Every fixture in the matrix is a shape the service is known to accept, so a rejection is a
        # real problem -- either the fixture drifted or the service contract moved.
        print("\nRejected by the Preview API -- these records are MISSING from the test registry:")
        for fixture in rejected:
            print(f"  - {fixture['scenario']}: {fixture['reason'][:220]}")

    updates = [fixture for fixture in created_records if fixture["post_create_update"]]
    if updates:
        print(f"\nApplying post-create updates for {len(updates)} records (makes updatedAt > createdAt)...")
        for fixture in updates:
            stable = wait_until_stable(client, registry_id, fixture["recordId"])
            if stable in ("CREATE_FAILED", "UPDATE_FAILED"):
                print(f"  SKIP  {fixture['scenario']} (status={stable})")
                continue
            ok, detail = apply_post_create_update(
                client, registry_id, fixture["recordId"], fixture["post_create_update"]
            )
            print(f"  {'OK  ' if ok else 'FAIL'}  {fixture['scenario']}: {detail}")

    transitions = [fixture for fixture in created_records if fixture["target_status"]]
    if transitions:
        print(f"\nApplying status transitions for {len(transitions)} records...")
        for fixture in transitions:
            stable = wait_until_stable(client, registry_id, fixture["recordId"])
            if stable != "DRAFT":
                print(
                    f"  SKIP  {fixture['scenario']} (status={stable}, cannot transition to {fixture['target_status']})"
                )
                continue
            ok, detail = apply_status(client, registry_id, fixture["recordId"], str(fixture["target_status"]))
            print(f"  {'->  ' if ok else 'FAIL'}  {fixture['target_status']:<18} {fixture['scenario']}: {detail}")

    print("\nFinal record states:")
    counts: dict[str, int] = {}
    for fixture in created_records:
        status = wait_until_stable(client, registry_id, fixture["recordId"], attempts=6, delay=3)
        counts[status] = counts.get(status, 0) + 1
    for status, count in sorted(counts.items()):
        print(f"  {status:<18} {count}")

    print("\nPoint the migration source at this registry to test:")
    print(
        f'  "source": {{ "accountId": "{matrix_account}", "region": "{args.region}", "registryId": "{registry_id}" }}'
    )
    print("\nB5 follow-up -- does the list summary synthesize a recordVersion the create did not set?")
    print(f"  aws bedrock-agentcore-control list-registry-records --registry-id {registry_id} \\")
    print(
        "    --name b5-no-record-version --region "
        + args.region
        + (f" --profile {args.profile}" if args.profile else "")
    )
    return 2 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
