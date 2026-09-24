"""Glue job argument parsing and SSM-backed configuration loading.

Runtime configuration is split in two: customer-editable run knobs live as individual
parameters under ``<prefix>/config`` (so a single value such as ``dryRun`` can be changed
in isolation), while the internal API adapter and transform rules live in one
CDK-managed ``<prefix>/adapter`` parameter. The replay fingerprint over the transform and
target adapter binds an extract run to the exact code that produced it, so live writes cannot
silently replay against changed logic.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Iterable
from typing import Any

from .util import parse_timestamp, safe_segment


class ConfigurationError(ValueError):
    """Raised when Glue arguments or SSM configuration are missing or invalid."""


# Where a default deployment publishes its configuration: engine.parameterPrefix defaults to
# ``/agent-registry-migration/<deploymentId>`` and deploymentId defaults to ``default``. Commands
# fall back to this so the common case takes no --config-prefix argument.
DEFAULT_CONFIG_PREFIX = "/agent-registry-migration/default"


def _normalize_argument_key(key: str) -> str:
    """Normalize an argument name so ``--config-prefix`` == ``--CONFIG_PREFIX``."""
    return key.strip().lstrip("-").replace("-", "_").upper()


def parse_job_arguments(argv: Iterable[str] | None = None) -> dict[str, str]:
    """Parse ``--key value`` / ``--key=value`` arguments.

    Glue passes upper snake-case names (``--CONFIG_PREFIX``). Running the jobs directly is more
    natural with kebab-case (``--config-prefix``), so every argument is also recorded under its
    normalized upper snake-case name and callers can look it up either way.
    """
    values = list(argv if argv is not None else sys.argv[1:])
    result: dict[str, str] = {}
    index = 0
    while index < len(values):
        item = values[index]
        if not item.startswith("--"):
            index += 1
            continue
        key_value = item[2:].split("=", 1)
        if len(key_value) == 2:
            key, value = key_value[0], key_value[1]
            index += 1
        else:
            key = key_value[0]
            if index + 1 < len(values) and not values[index + 1].startswith("--"):
                value = values[index + 1]
                index += 2
            else:
                value = "true"
                index += 1
        result[key] = value
        normalized = _normalize_argument_key(key)
        if normalized != key:
            result.setdefault(normalized, value)
    return result


def optional_argument(arguments: dict[str, str], name: str) -> str | None:
    """Return an argument (or same-named environment variable), accepting either naming style."""
    normalized = _normalize_argument_key(name)
    for candidate in (name, normalized, normalized.lower(), normalized.replace("_", "-").lower()):
        value = arguments.get(candidate)
        if value:
            return value
    for candidate in (name, normalized):
        value = os.environ.get(candidate)
        if value:
            return value
    return None


_FALSE_FLAG_VALUES = {"false", "0", "no", "off"}


def flag(arguments: dict[str, str], name: str) -> bool:
    """Return whether a boolean flag such as ``--offline`` was passed on the command line.

    Unlike :func:`optional_argument` this deliberately does **not** fall back to a same-named
    environment variable: a stray ``JSON=1`` or ``OFFLINE=1`` in a shell or CI environment must
    never silently change what a job does. A bare ``--flag`` is true; ``--flag=false`` (or
    ``0``/``no``/``off``) is false.
    """
    normalized = _normalize_argument_key(name)
    for candidate in (name, normalized, normalized.lower(), normalized.replace("_", "-").lower()):
        if candidate in arguments:
            return str(arguments[candidate]).strip().lower() not in _FALSE_FLAG_VALUES
    return False


_TRUE_FLAG_VALUES = {"true", "1", "yes", "on"}


def _present_argument_key(arguments: dict[str, str], name: str) -> str | None:
    """Return the key under which ``name`` was passed on the command line, or ``None``."""
    normalized = _normalize_argument_key(name)
    for candidate in (name, normalized, normalized.lower(), normalized.replace("_", "-").lower()):
        if candidate in arguments:
            return candidate
    return None


def live_override(arguments: dict[str, str]) -> bool | None:
    """Return this invocation's explicit live/dry-run intent, or ``None`` when unspecified.

    Whether records reach the target registry is the one decision worth stating per run rather than
    storing, so ``--live`` overrides the configured ``dryRun`` for this invocation only. It is read
    from the command line and never from the environment: a leftover ``LIVE=1`` in a shell or CI
    environment must not be able to turn a review run into a live one.
    """
    candidate = _present_argument_key(arguments, "LIVE")
    if candidate is None:
        return None
    text = str(arguments[candidate]).strip().lower()
    if text in _FALSE_FLAG_VALUES:
        return False
    if text in _TRUE_FLAG_VALUES:
        return True
    raise ConfigurationError(f"--live must be true or false, got {arguments[candidate]!r}")


def load_mode_override(arguments: dict[str, str]) -> str | None:
    """Return this invocation's explicit load mode, or ``None`` when unspecified.

    Which records a run covers is a per-run decision for the same reason ``--live`` is: the
    documented cutover is a FULL load followed by an INCREMENTAL catch-up, so storing the mode in
    the configuration would mean editing a file at exactly the moment nobody wants to. Read from
    the command line only, like every other override.
    """
    candidate = _present_argument_key(arguments, "LOAD_MODE")
    if candidate is None:
        return None
    value = str(arguments[candidate]).strip().upper()
    if value not in {"FULL", "INCREMENTAL"}:
        raise ConfigurationError(f"--load-mode must be FULL or INCREMENTAL, got {arguments[candidate]!r}")
    return value


def changed_after_override(arguments: dict[str, str]) -> str | None:
    """Return this invocation's explicit incremental cutoff, or ``None`` when unspecified.

    An empty value means "unset it and fall back to the saved watermark", which is how a run asks
    for the watermark despite a cutoff being configured.
    """
    candidate = _present_argument_key(arguments, "CHANGED_AFTER")
    if candidate is None:
        return None
    return str(arguments[candidate]).strip()


def apply_run_overrides(settings: dict[str, Any], arguments: dict[str, str]) -> dict[str, Any]:
    """Apply per-invocation overrides to loaded settings, in place, and return them.

    Three things are overridable, and they are the three a person decides per run rather than once:
    whether records are written (``--live``), which records are covered (``--load-mode``), and where
    an incremental run starts (``--changed-after``). Everything else comes from the one
    configuration document, so there is a single place to look when a run behaves unexpectedly.

    The replay fingerprint covers ``transform`` + ``api.target``, none of which this touches, so an
    override cannot make a staged run unloadable. Callers re-validate afterwards, so an override
    cannot smuggle in a value the configuration itself would have been rejected for.
    """
    load = settings.get("load")
    if not isinstance(load, dict):
        return settings

    live = live_override(arguments)
    if live is not None:
        load["dryRun"] = not live

    mode = load_mode_override(arguments)
    if mode is not None:
        load["mode"] = mode

    changed_after = changed_after_override(arguments)
    if changed_after is not None:
        load["changedAfter"] = changed_after or None
    return settings


def required_argument(arguments: dict[str, str], name: str) -> str:
    value = optional_argument(arguments, name)
    if not value:
        raise ConfigurationError(
            f"Missing required argument --{name} (also accepted as "
            f"--{name.replace('_', '-').lower()} or the {name} environment variable)"
        )
    return value


def resolve_run_id(arguments: dict[str, str], *, allow_generate: bool) -> str:
    run_id = arguments.get("RUN_ID") or arguments.get("WORKFLOW_RUN_ID") or os.environ.get("WORKFLOW_RUN_ID")
    if not run_id and allow_generate:
        # Timestamp-prefixed so generated runs sort chronologically and read clearly in the
        # S3 console (e.g. 20260715T174501Z-a1b2c3d4); the short uuid suffix keeps two runs
        # started in the same second distinct.
        stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
    if not run_id:
        raise ConfigurationError(
            "No run id was supplied. Run 'agent-registry-migration run', which generates one and "
            "passes it to both stages -- or pass --RUN_ID when starting a job by hand."
        )
    return safe_segment(run_id)


def replay_configuration_fingerprint(settings: dict[str, Any]) -> str:
    """Return a stable SHA-256 over the transform + target adapter settings.

    The extract stage records this fingerprint; transform/load recomputes it and refuses
    live writes on a mismatch so a run is never replayed against changed logic.
    """
    transform = settings.get("transform")
    api = settings.get("api")
    target_api = api.get("target") if isinstance(api, dict) else None
    if not isinstance(transform, dict) or not isinstance(target_api, dict):
        raise ConfigurationError("Settings must contain transform and api.target objects for replay protection")
    payload = {
        "schemaVersion": 1,
        "transform": transform,
        "targetApi": target_api,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_configuration(ssm_client: Any, parameter_prefix: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load run knobs, the API/transform adapter, and registry mappings from SSM.

    Configuration is grouped one parameter per concern:

    * ``<prefix>/config``     -- customer-editable run knobs, one JSON object.
    * ``<prefix>/registries`` -- customer-editable routing table, one JSON array of mappings.
    * ``<prefix>/adapter``    -- internal API + transform rules (CDK-managed, never edited).

    Deployments created before the grouping used a parameter per knob and per endpoint field;
    both older layouts are still read when the grouped parameter is absent, so an existing
    deployment keeps working until it is redeployed.

    Returns the assembled settings dict and the id-sorted list of registry mappings.
    """
    prefix = parameter_prefix.rstrip("/")

    grouped_config = _get_parameter(ssm_client, f"{prefix}/config")
    if grouped_config is not None:
        knobs = _parse_config_value(grouped_config, f"{prefix}/config")
    else:
        # Legacy layout: one parameter per knob under <prefix>/config/<knob>.
        knobs = _read_parameters_by_path(ssm_client, f"{prefix}/config", recursive=False)

    adapter_value = _get_parameter(ssm_client, f"{prefix}/adapter")
    if adapter_value is None:
        # This is the message a first run hits when nothing is deployed yet, or when the deployment
        # uses a different prefix, so it names both causes and the fix for each.
        raise ConfigurationError(
            f"No migration deployment found at {prefix} ({prefix}/adapter does not exist). "
            "Deploy the Glue engine with: agent-registry-migration deploy -- or drop --glue to run "
            "the migration here, which needs no deployment at all."
            + (
                f" If a deployment does exist, it publishes elsewhere: this looked at {prefix}, "
                f"while the default is {DEFAULT_CONFIG_PREFIX}. Set engine.parameterPrefix in your "
                "configuration file to the ConfigurationParameterPrefix stack output."
                if prefix != DEFAULT_CONFIG_PREFIX.rstrip("/")
                else " If a deployment does exist under a non-default engine.deploymentId, set "
                "engine.parameterPrefix in your configuration file to the "
                "ConfigurationParameterPrefix stack output."
            )
        )
    adapter = _json_object(adapter_value, f"{prefix}/adapter")
    mappings = _load_registry_mappings(ssm_client, f"{prefix}/registries")
    return _assemble_settings(knobs, adapter, mappings)


def load_configuration_from_file(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load the same configuration from a local JSON file instead of SSM.

    This exists so the jobs can be run as plain Python (outside Glue) against a file. The
    document mirrors the three grouped SSM parameters::

        {
          "config":     { "loadMode": "FULL", "dryRun": true, ... },
          "registries": [ { "id": "...", "source": {...}, "target": {...} } ],
          "adapter":    { "schemaVersion": 1, "transform": {...}, "api": {...} }
        }

    Full parameter paths (``/prefix/config``) are accepted as keys too, so a file assembled by
    dumping the deployed parameters works as-is.
    """
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved):
        raise ConfigurationError(f"Configuration file not found: {resolved}")
    with open(resolved, "r", encoding="utf-8") as handle:
        try:
            document = json.load(handle)
        except json.JSONDecodeError as error:
            raise ConfigurationError(f"{resolved} must contain valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ConfigurationError(f"{resolved} must contain a JSON object")

    # Accept both bare keys and full SSM parameter paths ("/prefix/config" -> "config").
    sections = {str(key).rstrip("/").rsplit("/", 1)[-1]: value for key, value in document.items()}
    adapter = sections.get("adapter")
    if not isinstance(adapter, dict):
        # No adapter section. A deployment config (the `migration.json` this repo's CDK app reads)
        # carries the same information in a different shape, so accept it and build the adapter
        # locally from the contract bundled in this package. That is what lets a run work with no
        # deployment at all -- there is nothing to export an adapter from.
        deployment_shaped = _deployment_shaped_sections(sections)
        if deployment_shaped is None:
            raise ConfigurationError(
                f"{resolved} must contain either an 'adapter' object (export it from a deployment "
                "with: aws ssm get-parameter --name <prefix>/adapter --query Parameter.Value "
                "--output text) or a 'registries' array, which is what config/migration.json holds "
                "and is all a local run needs."
            )
        sections = deployment_shaped
        adapter = sections["adapter"]

    # Each section may be given either as a JSON structure or as the same key=value text used in
    # SSM, so a file assembled by dumping the deployed parameters loads unchanged.
    knobs_section = sections.get("config") or {}
    if isinstance(knobs_section, str):
        knobs = _parse_config_value(knobs_section, f"{resolved} 'config'")
    elif isinstance(knobs_section, dict):
        knobs = knobs_section
    else:
        raise ConfigurationError(f"{resolved} 'config' must be a JSON object or a key=value document")

    registries_section = sections.get("registries") or []
    if isinstance(registries_section, str):
        mappings = _parse_registries_value(registries_section, f"{resolved} 'registries'")
    elif isinstance(registries_section, (list, dict)):
        mappings = _parse_grouped_registries(json.dumps(registries_section), f"{resolved} 'registries'")
    else:
        raise ConfigurationError(f"{resolved} 'registries' must be a JSON array or a key=value document")
    return _assemble_settings(knobs, adapter, mappings)


def _deployment_shaped_sections(sections: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a CDK deployment config into the three sections the jobs read, or ``None``.

    ``config/migration.json`` is the file a user already maintains: ``engine``, ``runtime.load``,
    ``runtime.transform`` and ``registries``. It has no ``adapter`` because a deployment publishes
    that itself. Accepting this shape means a local run points at the same file the stack uses,
    rather than a second document that has to be kept in step with it.
    """
    registries = sections.get("registries")
    if not isinstance(registries, (list, dict)):
        return None
    runtime = sections.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    load = runtime.get("load")
    transform = runtime.get("transform")

    # Imported here rather than at module import time: reading the bundled contract is only needed
    # on this path, and settings.py is imported by everything.
    from .adapter_defaults import local_adapter

    knobs = sections.get("config")
    if not isinstance(knobs, (dict, str)):
        knobs = _load_knobs_from_deployment_config(load if isinstance(load, dict) else {})

    return {
        "config": knobs,
        "registries": registries,
        "adapter": local_adapter(transform=transform if isinstance(transform, dict) else None),
    }


def _load_knobs_from_deployment_config(load: dict[str, Any]) -> dict[str, Any]:
    """Translate a deployment config's ``runtime.load`` block into run-knob names.

    The two shapes disagree on exactly one name: the deployment config calls it ``mode`` (validated
    as such by lib/config.ts) while the run knobs -- and the ``<prefix>/config`` parameter a
    deployment publishes -- call it ``loadMode``. Passing the block through untranslated silently
    dropped ``mode: INCREMENTAL`` and left every file-driven run reading every record as a FULL
    load, which is the failure this mapping exists to prevent. Every other key is spelled the same
    on both sides, so it is copied as-is.
    """
    knobs = {key: value for key, value in load.items() if key != "mode"}
    mode = load.get("loadMode", load.get("mode"))
    if mode is not None:
        knobs["loadMode"] = mode
    return knobs


def resolve_configuration(
    arguments: dict[str, str],
    ssm_client_factory: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Load configuration from a local file when given, otherwise from SSM.

    Returns ``(settings, mappings, source_description)``. ``--CONFIG_FILE`` (or ``--config-file``)
    selects the file path; ``--CONFIG_PREFIX`` selects the SSM prefix and defaults to
    :data:`DEFAULT_CONFIG_PREFIX`, which is where a default deployment publishes it -- so the
    common case needs no argument at all. This lets the same job body run under Glue (SSM) and as
    a standalone script (either source).

    Per-invocation overrides are applied here, so every caller honours them identically, and the
    result is re-validated: loading validates the document, but an override arrives afterwards and
    must be held to the same rules rather than reaching a job unchecked.
    """
    config_file = optional_argument(arguments, "CONFIG_FILE")
    if config_file:
        settings, mappings = load_configuration_from_file(config_file)
        source = f"file {config_file}"
    else:
        config_prefix = optional_argument(arguments, "CONFIG_PREFIX") or DEFAULT_CONFIG_PREFIX
        if ssm_client_factory is None:
            import boto3  # imported lazily so a file-based run needs no AWS session

            # Read the configuration from the region the engine was deployed into, not whatever the
            # ambient session points at. The caller's default region is frequently not the engine's
            # -- and the parameters only exist in the engine's -- so without this a deployment in
            # any other region reports itself as missing.
            region = optional_argument(arguments, "REGION")
            session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
            ssm_client_factory = lambda: session.client("ssm")
        settings, mappings = load_configuration(ssm_client_factory(), config_prefix)
        source = f"SSM {config_prefix}"

    apply_run_overrides(settings, arguments)
    validate_runtime_configuration(settings, mappings)
    return settings, mappings, source


def resolve_staging_bucket(
    arguments: dict[str, str],
    settings: dict[str, Any],
    *,
    required: bool = True,
) -> str | None:
    """Return the staging bucket to use: the explicit argument, else the deployment's own.

    The deploy publishes the bucket it created into the configuration (``engine.stagingBucket``),
    so an operator who already named the configuration does not have to copy a generated bucket
    name out of the stack outputs as well. An explicit ``--staging-bucket`` still wins, which is
    what Glue passes and what a local run against another deployment's data needs.
    """
    explicit = optional_argument(arguments, "STAGING_BUCKET")
    if explicit:
        return explicit
    engine = settings.get("engine")
    published = engine.get("stagingBucket") if isinstance(engine, dict) else None
    if published:
        return str(published)
    if not required:
        return None
    raise ConfigurationError(
        "No staging bucket. Pass --staging-bucket <bucket>, or redeploy so the configuration "
        "publishes it (the deployed <prefix>/adapter parameter carries engine.stagingBucket). "
        "The bucket name is the StagingBucketName stack output."
    )


def _assemble_settings(
    knobs: dict[str, Any],
    adapter: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build and validate the settings dict from run knobs, the adapter, and the mappings."""
    # ``engine`` carries values the deployment already knows -- most usefully the staging bucket --
    # so a command does not have to be told what the stack just created. Absent on deployments made
    # before this was added, which is why every reader treats it as optional.
    engine = adapter.get("engine")
    settings: dict[str, Any] = {
        "schemaVersion": 1,
        "engine": engine if isinstance(engine, dict) else {},
        "load": _build_load(knobs),
        "transform": adapter.get("transform"),
        "api": adapter.get("api"),
    }
    validate_runtime_configuration(settings, mappings)
    return settings, mappings


def _load_registry_mappings(ssm_client: Any, base_path: str) -> list[dict[str, Any]]:
    """Load the registry mappings, preferring the grouped ``<prefix>/registries`` parameter.

    The grouped value is a JSON array of ``{id, source, target}`` objects (a JSON object keyed
    by mapping id is also accepted). When that parameter does not exist, fall back to the older
    per-field parameter layout. Returns mappings sorted by id.
    """
    grouped = _get_parameter(ssm_client, base_path)
    if grouped is not None:
        return _parse_registries_value(grouped, base_path)
    return _load_registry_mappings_from_fields(ssm_client, base_path)


def _parse_config_value(value: str, source_name: str) -> dict[str, Any]:
    """Read the run knobs from either the key=value document or a JSON object."""
    if _looks_like_json(value):
        return _json_object(value, source_name)
    return dict(parse_key_value_document(value, source_name))


def _parse_registries_value(value: str, source_name: str) -> list[dict[str, Any]]:
    """Read the routing table from either the key=value document or JSON."""
    if _looks_like_json(value):
        return _parse_grouped_registries(value, source_name)
    return parse_registry_document(value, source_name)


def _parse_grouped_registries(value: str, source_name: str) -> list[dict[str, Any]]:
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        # Accept an object keyed by mapping id, filling in the id when omitted.
        entries = [
            {**entry, "id": entry.get("id", key)} if isinstance(entry, dict) else entry for key, entry in parsed.items()
        ]
    elif isinstance(parsed, list):
        entries = parsed
    else:
        raise ConfigurationError(f"{source_name} must contain a JSON array of registry mappings")
    mappings: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigurationError(f"{source_name} entries must be JSON objects")
        mapping_id = str(entry.get("id", ""))
        if not mapping_id:
            raise ConfigurationError(f"{source_name} entries require an id")
        if mapping_id in mappings:
            raise ConfigurationError(f"Duplicate registry mapping id in {source_name}: {mapping_id}")
        mappings[mapping_id] = {**entry, "id": mapping_id}
    return [mappings[key] for key in sorted(mappings)]


def _load_registry_mappings_from_fields(ssm_client: Any, base_path: str) -> list[dict[str, Any]]:
    """Reconstruct registry mappings from the per-field parameters under ``<prefix>/registries``.

    Each mapping is stored as individual parameters named ``<id>/source/<field>`` and
    ``<id>/target/<field>`` (accountId, region, registryId, and optionally roleArn/externalId/
    registryArn) so an operator can read and edit a single value in the SSM console without
    editing JSON. A whole-mapping JSON blob at ``<id>`` is still accepted for backward
    compatibility with the pre-split layout. Returns mappings sorted by id.
    """
    parameters = _read_parameters_by_path(ssm_client, base_path, recursive=True)
    mappings: dict[str, dict[str, Any]] = {}
    for relative_name, value in parameters.items():
        parts = relative_name.split("/")
        if len(parts) == 1:
            blob = json.loads(value)
            if not isinstance(blob, dict):
                raise ConfigurationError(f"Registry mapping {base_path}/{relative_name} must be a JSON object")
            mappings[str(blob.get("id", parts[0]))] = blob
            continue
        if len(parts) != 3 or parts[1] not in ("source", "target"):
            raise ConfigurationError(
                f"Unexpected registry parameter {base_path}/{relative_name}; expected "
                "'<id>/source/<field>' or '<id>/target/<field>'"
            )
        mapping_id, side, field = parts
        mapping = mappings.setdefault(mapping_id, {"id": mapping_id, "source": {}, "target": {}})
        mapping[side][field] = value
    return [mappings[key] for key in sorted(mappings)]


def _get_parameter(ssm_client: Any, name: str) -> str | None:
    """Return a single parameter's value, or ``None`` when it does not exist.

    A missing parameter is an expected outcome here: it distinguishes the grouped layout from
    the older per-field layout. Any other SSM error propagates.
    """
    try:
        response = ssm_client.get_parameter(Name=name, WithDecryption=False)
    except Exception as error:
        if _is_parameter_not_found(ssm_client, error):
            return None
        raise
    return str(response["Parameter"]["Value"])


def _is_parameter_not_found(ssm_client: Any, error: Exception) -> bool:
    not_found = getattr(getattr(ssm_client, "exceptions", None), "ParameterNotFound", None)
    if not_found is not None and isinstance(error, not_found):
        return True
    code = getattr(error, "response", {}).get("Error", {}).get("Code") if hasattr(error, "response") else None
    return code == "ParameterNotFound"


def _looks_like_json(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith(("{", "["))


def parse_key_value_document(value: str, source_name: str) -> dict[str, str]:
    """Parse a ``key = value`` document (one setting per line, ``#`` comments).

    This is the customer-facing configuration format: a flat, self-documenting list an operator
    can edit line by line, adding or removing entries without touching JSON punctuation::

        # comments explain each setting
        loadMode = FULL
        dryRun   = true
        changedAfter =            # empty value means "not set"
    """
    result: dict[str, str] = {}
    for number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(
                f"{source_name} line {number} is not 'key = value' or a '#' comment: {raw_line.strip()!r}"
            )
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigurationError(f"{source_name} line {number} has an empty key")
        if key in result:
            raise ConfigurationError(f"{source_name} defines {key!r} more than once (line {number})")
        result[key] = _strip_inline_comment(raw_value)
    return result


def _strip_inline_comment(value: str) -> str:
    """Trim a trailing ``# comment``, keeping ``#`` characters that are inside a value."""
    text = value.strip()
    marker = text.find(" #")
    if marker >= 0:
        text = text[:marker]
    return text.strip()


def parse_registry_document(value: str, source_name: str) -> list[dict[str, Any]]:
    """Parse the registry routing table from a ``key = value`` document.

    One line per registry mapping -- add a line to migrate another registry, delete a line to
    stop migrating it::

        my-mapping = source=111122223333/us-east-1/src-abc, target=111122223333/us-west-2/tgt-xyz

    ``source``/``target`` take the compact ``<account>/<region>/<registryId>`` form. Optional
    fields are given as dotted keys in the same comma-separated list, for example
    ``source.roleArn=arn:aws:iam::444455556666:role/Reader``.
    """
    entries = parse_key_value_document(value, source_name)
    mappings: list[dict[str, Any]] = []
    for mapping_id, definition in entries.items():
        mapping: dict[str, Any] = {"id": mapping_id, "source": {}, "target": {}}
        if not definition:
            raise ConfigurationError(f"{source_name}: mapping {mapping_id!r} has no source/target definition")
        for part in definition.split(","):
            field = part.strip()
            if not field:
                continue
            if "=" not in field:
                raise ConfigurationError(
                    f"{source_name}: mapping {mapping_id!r} field {field!r} must be '<name>=<value>'"
                )
            name, _, field_value = field.partition("=")
            name = name.strip()
            field_value = field_value.strip()
            side, _, attribute = name.partition(".")
            if side not in ("source", "target"):
                raise ConfigurationError(
                    f"{source_name}: mapping {mapping_id!r} field {name!r} must start with 'source' or 'target'"
                )
            if not attribute:
                # Compact form: source=<account>/<region>/<registryId>
                pieces = [piece.strip() for piece in field_value.split("/") if piece.strip()]
                if len(pieces) != 3:
                    raise ConfigurationError(
                        f"{source_name}: mapping {mapping_id!r} {side} must be "
                        f"'<accountId>/<region>/<registryId>', got {field_value!r}"
                    )
                mapping[side].update({"accountId": pieces[0], "region": pieces[1], "registryId": pieces[2]})
            else:
                mapping[side][attribute] = field_value
        mappings.append(mapping)
    return sorted(mappings, key=lambda item: str(item["id"]))


def _json_object(value: str, source_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{source_name} must contain valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ConfigurationError(f"{source_name} must contain a JSON object")
    return parsed


def _read_parameters_by_path(ssm_client: Any, path: str, *, recursive: bool) -> dict[str, str]:
    base = path.rstrip("/")
    result: dict[str, str] = {}
    next_token: str | None = None
    while True:
        request: dict[str, Any] = {
            "Path": base,
            "Recursive": recursive,
            "WithDecryption": False,
            "MaxResults": 10,
        }
        if next_token:
            request["NextToken"] = next_token
        response = ssm_client.get_parameters_by_path(**request)
        for parameter in response.get("Parameters", []):
            name = str(parameter["Name"])
            relative = name[len(base) + 1 :] if name.startswith(f"{base}/") else name
            result[relative] = parameter["Value"]
        next_token = response.get("NextToken")
        if not next_token:
            break
    return result


def _build_load(knobs: dict[str, Any]) -> dict[str, Any]:
    """Assemble the load settings from the run knobs.

    Accepts both shapes: the grouped ``<prefix>/config`` JSON object (native booleans and
    integers) and the legacy per-parameter layout (every value a string).
    """
    changed_after = knobs.get("changedAfter")
    mode = knobs.get("loadMode", "FULL")
    return {
        "mode": str(mode).strip() if mode is not None else "FULL",
        "changedAfter": str(changed_after).strip() if changed_after else None,
        "dryRun": _as_bool(knobs, "dryRun", True),
        # false (the default): a failed record is skipped and listed in the report; every other
        # staged record still gets processed. true stops the run (nonzero exit, report FAILED) the
        # moment any record fails, for estates that want a load to be all-or-nothing.
        "failOnRecordError": _as_bool(knobs, "failOnRecordError", False),
        "recordsPerObject": _as_int(knobs, "recordsPerObject", 500),
        "loadConcurrency": _as_int(knobs, "loadConcurrency", 32),
        "dumpExtractedRecords": _as_bool(knobs, "dumpExtractedRecords", True),
        "allowReplayConfigurationDrift": _as_bool(knobs, "allowReplayConfigurationDrift", False),
        # On by default: target creates records in DRAFT, and a DRAFT record is invisible to data-plane
        # search and the browsing APIs, so an approved Preview record would arrive undiscoverable.
        "matchSourceStatus": _as_bool(knobs, "matchSourceStatus", True),
    }


def _as_bool(knobs: dict[str, Any], key: str, default: bool) -> bool:
    raw = knobs.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ConfigurationError(f"config {key} must be a boolean (true/false), got {raw!r}")


def _as_int(knobs: dict[str, Any], key: str, default: int) -> int:
    raw = knobs.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ConfigurationError(f"config {key} must be an integer, got {raw!r}")
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError as error:
        raise ConfigurationError(f"config {key} must be an integer, got {raw!r}") from error


def _require_int_in_range(
    load: dict[str, Any],
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> None:
    """Require ``load[field]`` to be a whole number in range, rejecting bools.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(True, int)`` is true and
    ``1 <= True <= 10000`` holds -- which meant ``recordsPerObject: true`` validated and then became
    1. ``loadConcurrency`` excluded bools and ``recordsPerObject`` did not; checking both the same
    way is what keeps the next one from being checked the looser way.
    """
    value = load.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigurationError(f"load.{field} must be an integer between {minimum} and {maximum}, got {value!r}")


def validate_runtime_configuration(settings: dict[str, Any], mappings: list[dict[str, Any]]) -> None:
    """Validate the assembled settings and mappings, raising ``ConfigurationError``."""
    if settings.get("schemaVersion") != 1:
        raise ConfigurationError(f"Unsupported settings schemaVersion: {settings.get('schemaVersion')}")
    load = settings.get("load")
    if not isinstance(load, dict):
        raise ConfigurationError("Settings must contain a load object")
    mode = load.get("mode")
    if mode not in {"FULL", "INCREMENTAL"}:
        raise ConfigurationError(f"Unsupported load mode: {mode}")
    # INCREMENTAL no longer requires an explicit changedAfter: when it is empty the extract falls
    # back to the watermark saved by each mapping's last successful load, and fails with guidance
    # if no watermark exists yet. An explicit changedAfter still wins when present.
    if load.get("changedAfter"):
        try:
            parse_timestamp(load["changedAfter"])
        except (ValueError, TypeError) as error:
            raise ConfigurationError(
                f"load.changedAfter must be an ISO-8601 timestamp (example 2026-08-01T00:00:00Z), "
                f"got {load['changedAfter']!r}"
            ) from error
    _require_int_in_range(load, "loadConcurrency", 32, 1, 32)
    _require_int_in_range(load, "recordsPerObject", 500, 1, 10_000)
    # Defaults here must match _build_load, which is what actually populates these. They only apply
    # when a key is absent, so a mismatch was harmless -- but `failOnRecordError` said True here and
    # False there, which is exactly the kind of disagreement that gets read as the real default.
    for field, default in (
        ("dryRun", True),
        ("failOnRecordError", False),
        ("dumpExtractedRecords", True),
        ("allowReplayConfigurationDrift", False),
        ("matchSourceStatus", True),
    ):
        if not isinstance(load.get(field, default), bool):
            raise ConfigurationError(f"load.{field} must be a boolean")
    transform = settings.get("transform")
    if isinstance(transform, dict):
        # Imported here, not at module scope: the modes are owned by the guard that acts on them,
        # and this module is the configuration layer, which nothing else in it makes depend on a
        # control-plane client (or on boto3 being importable just to validate a config file).
        from .registry_api import DUPLICATE_NAME_MODES

        # Absent means the default, so a deployment made before this setting existed keeps working.
        # An unrecognised value must not be tolerated: the claim guard treats anything that is not
        # "suffix" as "fail", so a typo would silently give the stricter behaviour on a run whose
        # whole purpose was to migrate colliding records.
        duplicate_names = transform.get("duplicateNames", "fail")
        if duplicate_names not in DUPLICATE_NAME_MODES:
            raise ConfigurationError(
                f"transform.duplicateNames must be one of {', '.join(DUPLICATE_NAME_MODES)}, got {duplicate_names!r}"
            )
    replay_configuration_fingerprint(settings)

    seen: set[str] = set()
    for mapping in mappings:
        mapping_id = mapping.get("id")
        if not isinstance(mapping_id, str) or not mapping_id:
            raise ConfigurationError("Every registry mapping requires an id")
        if mapping_id in seen:
            raise ConfigurationError(f"Duplicate registry mapping id: {mapping_id}")
        seen.add(mapping_id)
        for side in ("source", "target"):
            endpoint = mapping.get(side)
            if not isinstance(endpoint, dict):
                raise ConfigurationError(f"Mapping {mapping_id} requires a {side} object")
            # roleArn is optional. When present the engine assumes it (cross-account). When
            # absent the engine uses its own Glue execution-role identity directly, which is
            # valid only when the endpoint is in the same account as the engine.
            for field in ("accountId", "region", "registryId"):
                if not endpoint.get(field):
                    raise ConfigurationError(f"Mapping {mapping_id}.{side} requires {field}")
