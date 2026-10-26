"""Preview-to-new-version record and registry schema transformation.

This module owns every breaking-change mapping between the Public Preview shape and the
July 2026 target API contract:

* the Preview ``descriptors`` discriminated union (``agent``/``mcp``/``agentSkills``/
  ``custom``) becomes a target struct-dict keyed by a single granular primary descriptor
  (``a2aAgentCard``/``mcpServer``/``agentSkillsDefinition``/``custom``), with
  supplementary descriptors moved under the primary's ``additionalData``;
* ``inlineContent`` becomes ``data`` and the schema/protocol version fields collapse to
  ``dataSchemaVersion``;
* the single top-level ``synchronizationConfiguration`` becomes a per-descriptor
  ``source.fromUrl``;
* the Preview record's name is carried over as the target registry ``name`` -- the new required dedup key --
  and also becomes the target ``displayName``. The service accepts the same name shape the Preview API enforces,
  so the two stay identical; only an unusable or absent name falls back to a generated one.

The Preview reader is lenient (it accepts several equivalent input spellings), while the
target writer is strict: ``_validate_target_record`` enforces the "exactly one valid primary per
recordType" rule before any record leaves this module.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Any


class TransformError(ValueError):
    """Raised when a Preview record cannot be mapped onto a valid target record."""


@dataclass(frozen=True)
class TransformResult:
    """A transformed target record plus non-fatal warnings and the source record id."""

    record: dict[str, Any]
    warnings: list[str]
    old_record_id: str | None
    # The status the record had in the Preview registry. Not part of the target registry payload -- status is
    # server-managed and every created record starts at DRAFT -- but carried here so the load stage
    # can drive the new record to the same status, and so the two can be reconciled in the report.
    source_status: str | None = None
    # The name the record had in Preview, kept alongside the target name for the crosswalk even when
    # the two are identical.
    preview_name: str | None = None


# Target primary descriptor keys. ``agentSkillsMd`` is a Preview-side selection result only: the live
# new service does not accept it as a primary descriptor, so a markdown-only skill is normalized onto
# ``agentSkillsDefinition`` before it leaves this module (see
# :func:`_markdown_skill_to_definition`). It stays in this tuple because the selection logic below
# still needs to recognise the Preview shape by that name.
_PRIMARY_KEYS = (
    "a2aAgentCard",
    "mcpServer",
    "agentSkillsDefinition",
    "agentSkillsMd",
    "custom",
)
# Preview discriminated-union variant keys. ``a2a`` is an accepted spelling of ``agent``.
_VARIANT_KEYS = ("agent", "a2a", "mcp", "agentSkills", "custom")
# Within each Preview variant, the primary descriptor may be spelled in more than one way.
# Each entry maps an accepted Preview alias to its canonical target primary key.
_PRIMARY_ALIASES_BY_VARIANT: dict[str, tuple[tuple[str, str], ...]] = {
    "agent": (
        ("a2aAgentCard", "a2aAgentCard"),
        ("agentCard", "a2aAgentCard"),
        ("mcpServer", "mcpServer"),
        ("server", "mcpServer"),
        ("custom", "custom"),
    ),
    "a2a": (
        ("a2aAgentCard", "a2aAgentCard"),
        ("agentCard", "a2aAgentCard"),
        ("custom", "custom"),
    ),
    "mcp": (
        ("mcpServer", "mcpServer"),
        ("server", "mcpServer"),
        ("custom", "custom"),
    ),
    "agentSkills": (
        ("agentSkillsDefinition", "agentSkillsDefinition"),
        ("skillDefinition", "agentSkillsDefinition"),
        ("agentSkillsMd", "agentSkillsMd"),
        ("custom", "custom"),
    ),
    "custom": (("custom", "custom"),),
}
_PRIMARY_SOURCE_ALIASES = {
    alias: canonical for aliases in _PRIMARY_ALIASES_BY_VARIANT.values() for alias, canonical in aliases
}
_PRIMARY_SOURCE_KEYS = tuple(_PRIMARY_SOURCE_ALIASES)
# Supplementary (non-primary) descriptors and the target registry ``additionalData`` child they map to.
_SUPPLEMENTARY_ALIASES = {
    "tools": "tools",
    "skillsMd": "skillMd",
    "skillMd": "skillMd",
}
_SUPPLEMENTARY_KEYS = tuple(_SUPPLEMENTARY_ALIASES)
_ALLOWED_ADDITIONAL_DATA = {
    "mcpServer": {"tools"},
    "agentSkillsDefinition": {"skillMd"},
}
_SOURCE_SUPPORTED_DESCRIPTORS = {"a2aAgentCard", "mcpServer", "agentSkillsMd", "skillMd"}
# What the target registry's customJWTAuthorizer actually models. Preview registries carry
# ``bedrock-agentcore``'s authorizer shape, which is shared with Gateway and Runtime and therefore
# has members the target registry's registry API does not (advertisedScopeMapping, allowedWorkloadConfiguration,
# privateEndpoint, privateEndpointOverrides). Anything outside this set is dropped with a warning
# rather than copied into a payload the service would refuse.
_TARGET_JWT_AUTHORIZER_FIELDS = (
    "discoveryUrl",
    "allowedAudience",
    "allowedClients",
    "allowedScopes",
    "customClaims",
)
# Target record names follow the same shape the Preview API enforces, so a preview name is normally
# usable as-is. Kept here as the single definition of what may be carried over unchanged.
_TARGET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-./]*$")
_TARGET_NAME_MAX_LENGTH = 255
# Source statuses the load stage can put a migrated record into. The rest (CREATING, UPDATING,
# CREATE_FAILED, UPDATE_FAILED) are transient or describe a failed operation on the source record,
# so they say nothing a new target record could be set to.
_REPRODUCIBLE_SOURCE_STATUSES = frozenset({"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"})

_SERVER_MANAGED_FIELDS = {
    "recordId",
    "recordArn",
    "registryArn",
    "arn",
    "createdAt",
    "updatedAt",
    "status",
    "statusReason",
    "revision",
}
_KNOWN_TOP_LEVEL_FIELDS = _SERVER_MANAGED_FIELDS | {
    "name",
    "displayName",
    "description",
    "identifier",
    "recordVersion",
    "recordType",
    "descriptorType",
    "descriptors",
    "synchronizationConfiguration",
}
_DESCRIPTOR_CONTROL_FIELDS = {
    "inlineContent",
    "data",
    "schemaVersion",
    "protocolVersion",
    "descriptorTypeVersion",
    "dataSchemaVersion",
    "synchronizationConfiguration",
    "source",
    "additionalData",
    "recordType",
    "protocol",
    "descriptor",
    "definition",
    *_PRIMARY_KEYS,
    *_PRIMARY_SOURCE_KEYS,
    *_SUPPLEMENTARY_KEYS,
}


class RecordTransformer:
    """Maps a single Preview registry record onto a valid target registry record."""

    def __init__(self, transform_config: dict[str, Any]) -> None:
        # Only used for records whose own name cannot be carried over (absent, or not a shape the service
        # accepts). A migrated record normally keeps the name it had in the Preview registry.
        configured_prefix = transform_config.get(
            "namePrefix",
            transform_config.get("identifierPrefix", "migrated"),
        )
        self._name_prefix = _name_part(configured_prefix)
        self._allowed_record_types = {
            str(value) for value in transform_config.get("allowedRecordTypes", ["AGENT", "MCP", "SKILL", "CUSTOM"])
        }
        configured_passthrough = [str(value) for value in transform_config.get("passthroughFields", ["description"])]
        unsupported_passthrough = sorted(set(configured_passthrough) - {"description"})
        if unsupported_passthrough:
            raise TransformError(
                "transform.passthroughFields contains fields that are not supported "
                "end-to-end by the target create/update contract: " + ", ".join(unsupported_passthrough)
            )
        self._passthrough_fields = configured_passthrough

    def transform(self, preview_record: dict[str, Any], context: dict[str, Any]) -> TransformResult:
        """Transform one Preview record into a validated target record plus warnings."""
        if not isinstance(preview_record, dict):
            raise TransformError("Preview record must be an object")
        warnings: list[str] = []
        old_record_id = self._require_old_record_id(preview_record, context)

        preview_descriptors = preview_record.get("descriptors")
        preview_variant = _preview_variant(preview_descriptors)
        primary_key, primary_value, descriptor_container = _select_primary_descriptor(preview_descriptors)
        record_type = self._record_type(primary_key, preview_variant)
        if record_type not in self._allowed_record_types:
            raise TransformError(f"Inferred recordType {record_type!r} is not allowed by transform.allowedRecordTypes")

        inherited_source = preview_record.get("synchronizationConfiguration")
        primary_descriptor = self._build_primary_descriptor(
            primary_key, primary_value, descriptor_container, inherited_source, warnings
        )
        if primary_key == "agentSkillsMd":
            primary_key, primary_descriptor = _markdown_skill_to_definition(primary_descriptor, warnings)

        if _optional_text(preview_record.get("identifier")):
            warnings.append(
                "Preview identifier was not carried over: the target dedup key is name "
                "(+ recordVersion), which takes the source record's name."
            )
        name = self._resolve_name(preview_record, context, old_record_id, warnings)
        display_name = self._resolve_display_name(preview_record, old_record_id, warnings)

        result: dict[str, Any] = {
            "name": name,
            "displayName": display_name,
            "recordType": record_type,
            "descriptors": {primary_key: primary_descriptor},
        }
        record_version = preview_record.get("recordVersion")
        if record_version not in (None, ""):
            result["recordVersion"] = copy.deepcopy(record_version)
        for field in self._passthrough_fields:
            if field in preview_record and field not in result:
                result[field] = copy.deepcopy(preview_record[field])

        ignored = sorted(
            key for key in preview_record if key not in _KNOWN_TOP_LEVEL_FIELDS and key not in self._passthrough_fields
        )
        if ignored:
            warnings.append(f"Ignored unmapped preview fields: {', '.join(ignored)}")

        # Approval state is not part of the create payload -- target creates every record in DRAFT -- so
        # it travels on the result for the load stage to reproduce through the status APIs. Warn only
        # about statuses no new record can be put into, since those describe what happened to the
        # *source* record rather than a state a migrated record could hold.
        source_status = _optional_text(preview_record.get("status"))
        if source_status and source_status.upper() not in _REPRODUCIBLE_SOURCE_STATUSES:
            warnings.append(
                f"Source record was {source_status}, which describes that record's own history and "
                "cannot be reproduced on a newly created target record; it will be left in DRAFT."
            )

        _validate_target_record(result)
        return TransformResult(
            record=result,
            warnings=warnings,
            old_record_id=old_record_id,
            source_status=source_status,
            preview_name=_optional_text(preview_record.get("name")),
        )

    @staticmethod
    def _require_old_record_id(preview_record: dict[str, Any], context: dict[str, Any]) -> str:
        """Return the normalized source record id, preferring the extract-stage context."""
        old_record_id = _optional_text(context.get("oldRecordId")) or _optional_text(preview_record.get("recordId"))
        if not old_record_id:
            raise TransformError("A normalized source oldRecordId is required before transformation")
        return old_record_id

    @staticmethod
    def _build_primary_descriptor(
        primary_key: str,
        primary_value: Any,
        descriptor_container: dict[str, Any],
        inherited_source: Any,
        warnings: list[str],
    ) -> dict[str, Any]:
        """Build the target primary descriptor and merge supplementary ``additionalData`` children."""
        primary_descriptor = _transform_descriptor(primary_value, inherited_source, primary_key, warnings)
        additional_data = _collect_additional_data(
            descriptor_container, primary_key, primary_value, inherited_source, warnings
        )
        if additional_data:
            existing = primary_descriptor.get("additionalData")
            if existing is not None and not isinstance(existing, dict):
                raise TransformError("Primary descriptor additionalData must be an object")
            merged = dict(existing or {})
            for key, value in additional_data.items():
                if key in merged:
                    warnings.append(
                        f"Supplementary descriptor {key!r} appeared more than once; "
                        "the explicit additionalData value was kept."
                    )
                    continue
                merged[key] = value
            primary_descriptor["additionalData"] = merged

        if inherited_source not in (None, {}) and not _contains_source(primary_descriptor):
            warnings.append(
                "Preview synchronizationConfiguration could not be represented because the "
                f"Target {primary_key} descriptor shape does not support source; it was omitted."
            )
        return primary_descriptor

    @staticmethod
    def _resolve_display_name(
        preview_record: dict[str, Any],
        old_record_id: str,
        warnings: list[str],
    ) -> str:
        """Derive the target registry ``displayName`` from the Preview ``name``, with a bounded fallback.

        The Preview ``name`` wins over a Preview ``displayName`` on purpose: the target registry's ``name`` is the
        dedup key and what records are looked up by, and keeping the two identical is what makes a
        migrated record recognisable. But a record that carried its own, different ``displayName``
        is losing it, so that is now said out loud -- it used to be dropped silently, and a
        human-facing label disappearing is exactly the kind of change nobody notices until later.
        """
        preview_name = _optional_text(preview_record.get("name"))
        preview_display_name = _optional_text(preview_record.get("displayName"))
        display_name = preview_name or preview_display_name
        if not display_name:
            display_name = f"Migrated {old_record_id}"
            warnings.append("Preview record had no name; a deterministic fallback displayName was generated.")
        elif preview_name and preview_display_name and preview_display_name != preview_name:
            warnings.append(
                f"Preview displayName {preview_display_name!r} was not carried over: the target registry "
                f"displayName is the record's name ({preview_name!r}), so the two stay identical. "
                "Set the target registry displayName afterwards if the distinct label matters."
            )
        return display_name[:255]

    def _record_type(self, primary_key: str, preview_variant: str | None) -> str:
        """Infer the target registry ``recordType`` from the Preview variant, then the primary key."""
        if preview_variant in {"agent", "a2a"}:
            return "AGENT"
        if preview_variant == "mcp":
            return "MCP"
        if preview_variant == "agentSkills":
            return "SKILL"
        if preview_variant == "custom":
            return "CUSTOM"
        if primary_key == "a2aAgentCard":
            return "AGENT"
        if primary_key == "mcpServer":
            return "MCP"
        if primary_key in {"agentSkillsDefinition", "agentSkillsMd"}:
            return "SKILL"
        return "CUSTOM"

    def _resolve_name(
        self,
        preview_record: dict[str, Any],
        context: dict[str, Any],
        old_record_id: str,
        warnings: list[str],
    ) -> str:
        """Return the target registry ``name``: the source record's own name, carried over unchanged.

        ``name`` is the target dedup key and the thing you filter and look records up by, so it has to
        stay recognisable. The Preview API already constrains record names to
        ``[a-zA-Z0-9][a-zA-Z0-9_\\-./]*`` within 255 characters -- the same shape the service accepts -- so a
        preview name is normally usable verbatim, and that is what happens here.

        Two fallbacks, each warned about because the result is no longer identical to the source:

        * a name that does not fit the target shape (possible for records created through older APIs)
          is sanitised, with a short digest appended so two different names cannot sanitise to one;
        * a record with no name at all gets the deterministic ``<prefix>-<digest>`` form.

        Preview did not require names to be unique within a registry and the target registry does; a name shared with
        another source record is not resolved here -- the load stage's target client refuses the second
        claimant rather than silently overwriting the first, and that failure is reported per record.
        """
        preview_name = _optional_text(preview_record.get("name"))
        if not preview_name:
            warnings.append(
                "Preview record had no name; a deterministic name was generated from the source "
                "registry and recordId. Set a name on the source record to keep them identical."
            )
            return self._generated_name(context, old_record_id)

        if _TARGET_NAME_PATTERN.match(preview_name) and len(preview_name) <= _TARGET_NAME_MAX_LENGTH:
            return preview_name

        sanitized = _sanitize_name(preview_name)
        if not sanitized:
            warnings.append(
                f"Preview name {preview_name!r} contains no characters the service accepts in a name; "
                "a deterministic name was generated instead."
            )
            return self._generated_name(context, old_record_id)
        # The digest keeps two different source names from collapsing onto one target name.
        suffix = hashlib.sha256(preview_name.encode("utf-8")).hexdigest()[:8]
        final = f"{sanitized[: _TARGET_NAME_MAX_LENGTH - len(suffix) - 1]}-{suffix}"
        warnings.append(
            f"Preview name {preview_name!r} is not a valid target name, so it was migrated as "
            f"{final!r}. Look records up by that name, or rename the source record."
        )
        return final

    def _generated_name(self, context: dict[str, Any], old_record_id: str) -> str:
        """Deterministic fallback name, derived from the source identity of the record."""
        return f"{self._name_prefix}-{self._source_digest(context, old_record_id)[:32]}"

    @staticmethod
    def _source_digest(context: dict[str, Any], old_record_id: str) -> str:
        """Hash of where a record came from: source account, region, registry, and its recordId.

        The one piece of identity a migrated record always has, and it is stable across runs, which
        is what makes both the generated-name fallback and duplicate-name disambiguation repeatable.
        """
        source = context.get("source") if isinstance(context.get("source"), dict) else {}
        material = "|".join(
            [
                str(source.get("accountId", "")),
                str(source.get("region", "")),
                str(source.get("registryId", "")),
                old_record_id,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def transform_registry_configuration(
    preview_registry: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    source_registry_id: str | None = None,
) -> dict[str, Any]:
    """Builds a final target CreateRegistry payload from a Preview Registry response.

    ``warnings`` collects anything about the derived payload a person has to decide on before
    applying it -- a preview-only authorizer field that had to be dropped, or an audience that
    names the registry being replaced. They are warnings rather than errors because the payload is
    still the right starting point; what they cannot be is silent, since this payload is applied by
    hand and an unreviewed one either fails validation or quietly authorizes the wrong thing.

    ``source_registry_id`` is the registry being replaced, used to recognise a value that points
    back at it. Taken from the caller (which reads it from the configuration) in preference to the
    response, so the check still works if a response does not echo the id.
    """
    if not isinstance(preview_registry, dict):
        raise TransformError("Preview registry must be an object")
    collected_warnings = warnings if warnings is not None else []
    name = _optional_text(preview_registry.get("name"))
    if not name:
        raise TransformError("Preview registry name is required")
    result: dict[str, Any] = {"name": name}
    description = preview_registry.get("description")
    if description not in (None, ""):
        result["description"] = copy.deepcopy(description)

    existing_discovery = preview_registry.get("discoveryConfiguration")
    if existing_discovery is not None and not isinstance(existing_discovery, dict):
        raise TransformError("discoveryConfiguration must be an object")
    discovery = copy.deepcopy(existing_discovery or {})
    authorizer_type = preview_registry.get("authorizerType")
    authorizer_configuration = preview_registry.get("authorizerConfiguration", discovery.get("authorizerConfiguration"))
    if authorizer_type is not None:
        discovery["authorizerType"] = copy.deepcopy(authorizer_type)
    if authorizer_configuration is not None:
        discovery["authorizerConfiguration"] = _transform_authorizer_configuration(
            authorizer_configuration,
            source_registry_id=(
                _optional_text(source_registry_id) or _optional_text(preview_registry.get("registryId"))
            ),
            warnings=collected_warnings,
        )
    if not _optional_text(discovery.get("authorizerType")):
        raise TransformError("Registry discoveryConfiguration.authorizerType is required")
    result["discoveryConfiguration"] = discovery

    approval_value = preview_registry.get("approvalConfiguration")
    if approval_value is not None:
        if not isinstance(approval_value, dict):
            raise TransformError("approvalConfiguration must be an object")
        approval = copy.deepcopy(approval_value)
        auto_approval = approval.pop("autoApproval", None)
        if "autoApprovalRules" not in approval and auto_approval is not None:
            approval["autoApprovalRules"] = ["APPROVE_ALL"] if auto_approval is True else []
        rules = approval.get("autoApprovalRules", [])
        if not isinstance(rules, list) or any(rule != "APPROVE_ALL" for rule in rules):
            raise TransformError("Only the APPROVE_ALL auto-approval rule is supported in the new version")
        result["approvalConfiguration"] = approval

    tags = preview_registry.get("tags")
    if tags is not None:
        if not isinstance(tags, dict):
            raise TransformError("Registry tags must be an object")
        result["tags"] = copy.deepcopy(tags)
    return result


def _transform_authorizer_configuration(
    value: Any,
    *,
    source_registry_id: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Project a Preview registry authorizer configuration onto the target shape.

    The two shapes are not the same, which a straight copy hid. Preview registries share
    ``bedrock-agentcore``'s authorizer structure with Gateway and Runtime, so a preview
    ``customJWTAuthorizer`` can carry fields the target registry API has no member for
    (``advertisedScopeMapping``, ``allowedWorkloadConfiguration``, the private-endpoint fields).
    Copying those through produces a payload the new Registry service rejects, so they are dropped and named
    -- dropped because there is nowhere to put them, named because a scope mapping or a workload
    restriction is an access-control decision and losing one silently is the worst outcome here.

    What survives is only whin the new version models: ``discoveryUrl``, ``allowedAudience``, ``allowedClients``,
    ``allowedScopes`` and ``customClaims``.
    """
    if not isinstance(value, dict):
        raise TransformError("authorizerConfiguration must be an object")
    unsupported_variants = sorted(set(value) - {"customJWTAuthorizer"})
    if unsupported_variants:
        raise TransformError(
            "authorizerConfiguration supports only customJWTAuthorizer in the new version; found: "
            + ", ".join(unsupported_variants)
        )
    jwt_value = value.get("customJWTAuthorizer")
    if jwt_value is None:
        return {}
    if not isinstance(jwt_value, dict):
        raise TransformError("authorizerConfiguration.customJWTAuthorizer must be an object")

    jwt: dict[str, Any] = {
        key: copy.deepcopy(jwt_value[key])
        for key in _TARGET_JWT_AUTHORIZER_FIELDS
        if key in jwt_value and jwt_value[key] is not None
    }
    dropped = sorted(str(key) for key in jwt_value if key not in _TARGET_JWT_AUTHORIZER_FIELDS)
    if dropped:
        warnings.append(
            "Dropped authorizer field(s) the target registry API does not accept: "
            + ", ".join(dropped)
            + ". They exist on the Preview shape because it is shared with Gateway and Runtime. "
            "Re-apply the equivalent access control by hand if you relied on them."
        )
    if not jwt.get("discoveryUrl"):
        raise TransformError("authorizerConfiguration.customJWTAuthorizer.discoveryUrl is required")

    _warn_on_stale_registry_references(jwt, source_registry_id, warnings)
    return {"customJWTAuthorizer": jwt}


def _warn_on_stale_registry_references(
    jwt: dict[str, Any],
    source_registry_id: str | None,
    warnings: list[str],
) -> None:
    """Flag authorizer values that name the Preview registry this one is replacing.

    A registry's own endpoint is a legitimate audience, so an ``allowedAudience`` entry like
    ``https://bedrock-agentcore.us-west-2.amazonaws.com/registry/<id>/mcp`` is a value pointing at
    the registry being migrated away from. It cannot be corrected here: the target registry does not
    exist yet, so its id is not knowable until the ``CreateRegistry`` this payload feeds has
    returned. Rewriting it to a guess would be worse than leaving it -- an audience that validates
    tokens against the wrong resource is an authorization bug, not a cosmetic one.

    So it is reported, precisely enough to act on: which field, which value, and what to do about it
    once the new registry id exists.
    """
    if not source_registry_id:
        return
    stale: list[str] = []
    for field, value in jwt.items():
        for text in _iter_strings(value):
            if source_registry_id in text:
                stale.append(f"{field}: {text}")
    if not stale:
        return
    warnings.append(
        f"Authorizer value(s) name the Preview registry {source_registry_id}, which the target registry "
        "registry replaces: "
        + "; ".join(sorted(stale))
        + ". The target registry id is only known once CreateRegistry has run, so these cannot be "
        "corrected here. Create the registry, then update the authorizer with the new id "
        "(UpdateRegistry) before pointing clients at it."
    )


def _iter_strings(value: Any) -> list[str]:
    """Every string inside ``value``, however deeply nested in lists or objects."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _iter_strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _iter_strings(item)]
    return []


def _select_primary_descriptor(descriptors: Any) -> tuple[str, Any, dict[str, Any]]:
    """Locate the single Preview primary descriptor across the accepted input shapes.

    Tries, in order: a discriminated-union variant key, a ``recordType`` discriminator,
    then a flat search for a known primary/markdown key. Returns the canonical target primary
    key, its value, and the container the supplementary descriptors were found in.
    """
    if not isinstance(descriptors, dict) or not descriptors:
        raise TransformError("Preview record descriptors must be a non-empty object")

    present_variants = [key for key in _VARIANT_KEYS if key in descriptors and descriptors[key] is not None]
    if len(present_variants) > 1:
        raise TransformError(f"Preview descriptors contain multiple union variants: {', '.join(present_variants)}")
    if present_variants:
        variant = present_variants[0]
        container = descriptors[variant]
        if not isinstance(container, dict):
            if variant == "custom":
                return "custom", container, descriptors
            raise TransformError(f"Preview descriptor variant {variant!r} must be an object")
        primary_key, primary_value = _primary_for_variant(variant, container)
        return primary_key, primary_value, container

    discriminator = str(descriptors.get("recordType", "")).strip()
    if discriminator:
        variant = _normalize_variant(discriminator)
        if variant:
            container = _unwrap_container(descriptors)
            primary_key, primary_value = _primary_for_variant(variant, container)
            return primary_key, primary_value, container

    matches = _find_named_values(descriptors, set(_PRIMARY_SOURCE_KEYS))
    canonical_matches = [(_PRIMARY_SOURCE_ALIASES[key], value) for key, value in matches]
    if not canonical_matches:
        markdown_matches = [
            (key, value)
            for key, value in _find_named_values(descriptors, set(_SUPPLEMENTARY_KEYS))
            if _SUPPLEMENTARY_ALIASES[key] == "skillMd"
        ]
        if len(markdown_matches) == 1:
            return "agentSkillsMd", markdown_matches[0][1], descriptors
        if len(markdown_matches) > 1:
            raise TransformError(
                "Preview descriptors contain multiple markdown-only skill aliases: "
                + ", ".join(key for key, _ in markdown_matches)
            )
    if len(canonical_matches) != 1:
        names = ", ".join(key for key, _ in matches) or "none"
        raise TransformError(
            f"Expected exactly one primary descriptor in preview shape; found {len(canonical_matches)} ({names})"
        )
    primary_key, primary_value = canonical_matches[0]
    return primary_key, primary_value, descriptors


def _primary_for_variant(variant: str, container: dict[str, Any]) -> tuple[str, Any]:
    """Resolve the canonical primary key/value for a known Preview variant container."""
    unwrapped = _unwrap_container(container)
    matches = [
        (alias, canonical, unwrapped[alias])
        for alias, canonical in _PRIMARY_ALIASES_BY_VARIANT[variant]
        if alias in unwrapped and unwrapped[alias] is not None
    ]

    if len(matches) > 1:
        raise TransformError(
            f"Preview {variant} descriptor contains multiple possible primary descriptors: "
            + ", ".join(alias for alias, _, _ in matches)
        )
    if matches:
        _, canonical, value = matches[0]
        return canonical, value

    if variant == "agentSkills":
        markdown_matches = [
            (alias, unwrapped[alias])
            for alias, canonical in _SUPPLEMENTARY_ALIASES.items()
            if canonical == "skillMd" and alias in unwrapped and unwrapped[alias] is not None
        ]
        if len(markdown_matches) > 1:
            raise TransformError(
                "Preview agentSkills descriptor contains multiple markdown aliases: "
                + ", ".join(alias for alias, _ in markdown_matches)
            )
        if markdown_matches:
            return "agentSkillsMd", markdown_matches[0][1]

    default_by_variant = {
        "agent": "a2aAgentCard",
        "a2a": "a2aAgentCard",
        "mcp": "mcpServer",
        "agentSkills": "agentSkillsDefinition",
        "custom": "custom",
    }
    return default_by_variant[variant], unwrapped


def _collect_additional_data(
    container: dict[str, Any],
    primary_key: str,
    primary_value: Any,
    inherited_source: Any,
    warnings: list[str],
) -> dict[str, Any]:
    """Collect and transform supplementary descriptors into the target registry ``additionalData`` children."""
    allowed = _ALLOWED_ADDITIONAL_DATA.get(primary_key, set())
    collected: dict[str, Any] = {}
    if isinstance(primary_value, dict):
        explicit = primary_value.get("additionalData")
        if explicit is not None:
            if not isinstance(explicit, dict):
                raise TransformError("Preview additionalData must be an object")
            for key, value in explicit.items():
                output_key = _SUPPLEMENTARY_ALIASES.get(str(key), str(key))
                if output_key not in allowed:
                    raise TransformError(
                        f"Target primary descriptor {primary_key!r} does not support additionalData.{output_key}"
                    )
                if output_key in collected:
                    warnings.append(
                        f"Supplementary descriptor aliases for {output_key!r} appeared more than "
                        "once; the first value was kept."
                    )
                    continue
                collected[output_key] = _transform_descriptor(
                    value,
                    inherited_source,
                    output_key,
                    warnings,
                )

    for key, value in _find_named_values(container, set(_SUPPLEMENTARY_KEYS)):
        output_key = _SUPPLEMENTARY_ALIASES[key]
        if primary_key == "agentSkillsMd" and output_key == "skillMd" and value == primary_value:
            continue
        if output_key not in allowed:
            if value != primary_value:
                raise TransformError(
                    f"Target primary descriptor {primary_key!r} does not support additionalData.{output_key}"
                )
            continue
        if output_key in collected:
            continue
        collected[output_key] = _transform_descriptor(
            value,
            inherited_source,
            output_key,
            warnings,
        )
    return collected


def _markdown_skill_to_definition(
    descriptor: dict[str, Any],
    warnings: list[str],
) -> tuple[str, dict[str, Any]]:
    """Normalize a markdown-only skill onto the target registry ``agentSkillsDefinition`` primary.

    the new version has no ``agentSkillsMd`` primary descriptor. The live service answers a record that uses one
    with "Exactly one valid descriptor is allowed for record type SKILL. Valid descriptors:
    [agentSkillsDefinition, custom]", so the Preview shape has to be carried by one of those two.

    It cannot go in ``data``: the service parses every descriptor's ``data`` as JSON, and Markdown is
    not JSON ("data is not valid JSON"), which rules out both ``agentSkillsDefinition.data`` and a
    ``custom`` record. What the service does accept is ``agentSkillsDefinition`` carrying the Markdown
    under ``additionalData.skillMd`` and carrying no ``data`` of its own -- the same place a skill that
    *has* a definition already puts its Markdown, which keeps one shape for both kinds of skill.

    ``source`` stays on the ``skillMd`` child, where the target API contract allows it, rather than moving to
    the definition primary, which does not support it.
    """
    child = {key: value for key, value in descriptor.items() if key != "additionalData"}
    definition: dict[str, Any] = {"additionalData": {"skillMd": child}}
    # A markdown-only skill has no additionalData of its own in practice, but if a Preview record
    # carried some anyway it is kept rather than dropped -- skillMd is the value being introduced
    # here, so it wins only when the source did not already provide one.
    existing_additional = descriptor.get("additionalData")
    if isinstance(existing_additional, dict):
        for key, value in existing_additional.items():
            definition["additionalData"].setdefault(key, value)
    warnings.append(
        "Preview markdown-only skill was migrated as an agentSkillsDefinition carrying the Markdown "
        "under additionalData.skillMd, because the service accepts no agentSkillsMd descriptor."
    )
    return "agentSkillsDefinition", definition


def _transform_descriptor(
    value: Any,
    inherited_source: Any,
    descriptor_key: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Map one Preview descriptor to the target registry: ``inlineContent``->``data``, version collapse, source."""
    if isinstance(value, str):
        result: dict[str, Any] = {"data": value}
        if inherited_source not in (None, {}) and descriptor_key in _SOURCE_SUPPORTED_DESCRIPTORS:
            result["source"] = _normalize_source(inherited_source, warnings)
        return result
    if not isinstance(value, dict):
        raise TransformError(f"Descriptor must be an object or string, got {type(value).__name__}")

    value = _unwrap_container(value)
    result: dict[str, Any] = {}
    if "data" in value:
        result["data"] = copy.deepcopy(value["data"])
    elif "inlineContent" in value:
        result["data"] = copy.deepcopy(value["inlineContent"])

    version_candidates = [
        value.get("dataSchemaVersion"),
        value.get("descriptorTypeVersion"),
        value.get("schemaVersion"),
        value.get("protocolVersion"),
    ]
    versions = [candidate for candidate in version_candidates if candidate not in (None, "")]
    if versions:
        result["dataSchemaVersion"] = copy.deepcopy(versions[0])
        if len({str(version) for version in versions}) > 1:
            warnings.append(
                "Descriptor had conflicting schema/protocol versions; dataSchemaVersion precedence was used."
            )

    explicit_source = None
    if "source" in value:
        explicit_source = value.get("source")
    elif "synchronizationConfiguration" in value:
        explicit_source = value.get("synchronizationConfiguration")
    descriptor_source = explicit_source if explicit_source is not None else inherited_source
    if descriptor_source not in (None, {}):
        if descriptor_key not in _SOURCE_SUPPORTED_DESCRIPTORS:
            if explicit_source is not None:
                raise TransformError(f"Target descriptor {descriptor_key!r} does not support source")
        else:
            result["source"] = _normalize_source(descriptor_source, warnings)

    ignored = sorted(str(key) for key in value if key not in _DESCRIPTOR_CONTROL_FIELDS)
    if ignored:
        warnings.append(f"Ignored unmapped fields on {descriptor_key} descriptor: {', '.join(ignored)}")
    if not result:
        raise TransformError(f"Descriptor {descriptor_key!r} produced an empty target payload")
    return result


def _normalize_source(value: Any, warnings: list[str]) -> dict[str, Any]:
    """Normalize a Preview sync config to a target ``{"fromUrl": {...}}`` source (URL-only in the new version)."""
    if value in (None, {}):
        raise TransformError("Descriptor source cannot be empty")
    if not isinstance(value, dict):
        raise TransformError("Descriptor source/synchronizationConfiguration must be an object")
    if value.get("fromAws") is not None:
        raise TransformError("source.fromAws is created in the new version and cannot be migrated by this engine")

    if isinstance(value.get("fromUrl"), dict):
        source_value = value["fromUrl"]
    elif isinstance(value.get("pullFromUrl"), dict):
        source_value = value["pullFromUrl"]
    elif value.get("url"):
        source_value = value
    else:
        raise TransformError("Only URL-backed descriptor sources are supported in the new version")

    if not source_value.get("url"):
        raise TransformError("source.fromUrl.url is required")
    from_url: dict[str, Any] = {"url": copy.deepcopy(source_value["url"])}
    credentials = source_value.get(
        "credentialProviderConfigurations",
        value.get("credentialProviderConfigurations"),
    )
    if credentials not in (None, []):
        from_url["credentialProviderConfigurations"] = copy.deepcopy(credentials)

    ignored = sorted(
        str(key)
        for key in source_value
        if key
        not in {
            "url",
            "credentialProviderConfigurations",
            "syncMode",
            "synchronizationMode",
            "fromUrl",
            "pullFromUrl",
            "fromAws",
        }
    )
    if ignored:
        warnings.append(f"Ignored unsupported source fields: {', '.join(ignored)}")
    if any(key in source_value or key in value for key in ("syncMode", "synchronizationMode")):
        warnings.append(
            "Preview synchronization mode was omitted because the target registry source.fromUrl shape does not contain syncMode."
        )
    return {"fromUrl": from_url}


def _find_named_values(value: Any, names: set[str], depth: int = 0) -> list[tuple[str, Any]]:
    """Recursively find entries whose key is in ``names``, skipping source/additionalData subtrees."""
    if depth > 5 or not isinstance(value, dict):
        return []
    matches: list[tuple[str, Any]] = []
    for key, child in value.items():
        if key in names:
            matches.append((key, child))
            continue
        if key in {"source", "synchronizationConfiguration", "additionalData"}:
            continue
        if isinstance(child, dict):
            matches.extend(_find_named_values(child, names, depth + 1))
    return matches


def _unwrap_container(value: dict[str, Any]) -> dict[str, Any]:
    """Flatten an optional Preview ``protocol``/``descriptor``/``definition`` wrapper layer."""
    for key in ("protocol", "descriptor", "definition"):
        nested = value.get(key)
        if isinstance(nested, dict):
            merged = copy.deepcopy(nested)
            for inherited_key in (
                "inlineContent",
                "data",
                "schemaVersion",
                "protocolVersion",
                "descriptorTypeVersion",
                "dataSchemaVersion",
                "source",
                "synchronizationConfiguration",
                "additionalData",
                *_SUPPLEMENTARY_KEYS,
            ):
                if inherited_key in value and inherited_key not in merged:
                    merged[inherited_key] = copy.deepcopy(value[inherited_key])
            return merged
    return value


def _preview_variant(descriptors: Any) -> str | None:
    """Return the Preview union variant (from a variant key or ``recordType``), if any."""
    if not isinstance(descriptors, dict):
        return None
    present_variants = [key for key in _VARIANT_KEYS if key in descriptors and descriptors[key] is not None]
    if len(present_variants) == 1:
        return present_variants[0]
    discriminator = str(descriptors.get("recordType", "")).strip()
    return _normalize_variant(discriminator) if discriminator else None


def _normalize_variant(value: str) -> str | None:
    """Map a free-form recordType/discriminator string to a canonical Preview variant."""
    normalized = re.sub(r"[^a-z]", "", value.lower())
    return {
        "agent": "agent",
        "a2a": "a2a",
        "mcp": "mcp",
        "tool": "mcp",
        "agentskills": "agentSkills",
        "skill": "agentSkills",
        "custom": "custom",
    }.get(normalized)


def _validate_target_record(record: dict[str, Any]) -> None:
    """Enforce the target API contract: valid name, and exactly one primary valid for the recordType."""
    for field in ("name", "recordType", "descriptors"):
        if record.get(field) in (None, "", {}):
            raise TransformError(f"Transformed record is missing required field {field}")
    name = str(record["name"])
    # The same pattern and bound `_resolve_name` produced the name against. Reusing them rather than
    # restating them here: this used to carry its own copy of the character class, so a change to
    # the target name contract had to be made in two places or the producer and the validator disagreed.
    if len(name) > _TARGET_NAME_MAX_LENGTH or not _TARGET_NAME_PATTERN.match(name):
        raise TransformError("Transformed record name does not satisfy the target name contract")

    descriptors = record["descriptors"]
    if not isinstance(descriptors, dict) or len(descriptors) != 1:
        raise TransformError("Transformed record must contain exactly one primary descriptor")
    primary_key = next(iter(descriptors))
    # Deliberately matches _FINAL_PRIMARY_RECORD_TYPES in registry_api.py. `agentSkillsMd` used to be
    # listed for SKILL, which the live service refuses as a primary descriptor -- so this, the
    # looser of the two validators, would have passed a body `validate_target_request` then rejected.
    # Unreachable in practice (`_markdown_skill_to_definition` normalizes it away first), but a
    # permission that only holds because of ordering elsewhere is one worth not granting.
    allowed_primaries = {
        "AGENT": {"a2aAgentCard", "mcpServer", "custom"},
        "MCP": {"mcpServer", "custom"},
        "SKILL": {"agentSkillsDefinition", "custom"},
        "CUSTOM": {"custom"},
    }
    record_type = str(record["recordType"])
    if record_type not in allowed_primaries:
        raise TransformError(f"Unsupported target recordType: {record_type}")
    if primary_key not in allowed_primaries[record_type]:
        raise TransformError(f"Target primary descriptor {primary_key!r} is invalid for recordType {record_type!r}")
    _validate_source_placement(primary_key, descriptors[primary_key])
    if _contains_key(record, "syncMode") or _contains_key(record, "synchronizationMode"):
        raise TransformError("Transformed target record must not contain a synchronization mode")


def _validate_source_placement(primary_key: str, descriptor: Any) -> None:
    """Ensure ``source`` and ``additionalData`` appear only where the target API contract allows."""
    if not isinstance(descriptor, dict):
        raise TransformError(f"Target primary descriptor {primary_key!r} must be an object")
    if "source" in descriptor and primary_key not in _SOURCE_SUPPORTED_DESCRIPTORS:
        raise TransformError(f"Target descriptor {primary_key!r} does not support source")
    additional = descriptor.get("additionalData")
    if additional is None:
        return
    if not isinstance(additional, dict):
        raise TransformError("Target descriptor additionalData must be an object")
    allowed = _ALLOWED_ADDITIONAL_DATA.get(primary_key, set())
    unsupported = set(additional) - allowed
    if unsupported:
        raise TransformError(
            f"Target descriptor {primary_key!r} has unsupported additionalData keys: " + ", ".join(sorted(unsupported))
        )
    for key, value in additional.items():
        if not isinstance(value, dict):
            raise TransformError(f"Target additionalData.{key} must be an object")
        if "source" in value and key not in _SOURCE_SUPPORTED_DESCRIPTORS:
            raise TransformError(f"Target additionalData.{key} does not support source")


def _contains_source(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "source" in value:
        return True
    return any(_contains_source(child) for child in value.values() if isinstance(child, dict))


def _contains_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(child, target) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, target) for child in value)
    return False


def _name_part(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._/-]", "-", str(value)).strip("-./")
    if not cleaned or not cleaned[0].isalnum():
        raise TransformError("transform.namePrefix must start with an alphanumeric character")
    return cleaned[:64]


def _sanitize_name(value: str) -> str:
    """Coerce a string into the target record-name shape, or return '' when nothing usable remains."""
    cleaned = re.sub(r"[^A-Za-z0-9._/-]", "-", value).strip("-./")
    while cleaned and not cleaned[0].isalnum():
        cleaned = cleaned[1:]
    return cleaned


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
