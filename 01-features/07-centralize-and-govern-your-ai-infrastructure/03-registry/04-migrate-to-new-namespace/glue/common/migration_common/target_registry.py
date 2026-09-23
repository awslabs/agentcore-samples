"""Derive each target registry's configuration from the Preview registry it will replace, and create it.

This tool migrates *records*, and the registry that holds them has to exist first. This module
removes both halves of that job. It reads a source registry (read-only) and returns the equivalent
target ``CreateRegistry`` input with the preview shape already translated:

* top-level ``authorizerType`` / ``authorizerConfiguration`` nested under ``discoveryConfiguration``
* ``approvalConfiguration.autoApproval: true`` becomes ``autoApprovalRules: ["APPROVE_ALL"]``

Then, on request, it applies that input: ``CreateRegistry`` on the target control plane, followed by
``GetRegistry`` until the registry settles, and hands back the generated registry id for the caller
to write into the configuration.

Deriving reads; creating writes, and only when asked. ``target-config`` derives by default and
creates only under ``--create``, so the payload can always be reviewed before anything exists --
the ``discoveryConfiguration`` decides who may read the registry, which is worth one look.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

from .aws_auth import invoker_for_endpoint
from .registry_api import PreviewRegistryClient, target_endpoint_url
from .registry_client import build_control_plane_client
from .transform import transform_registry_configuration

LOGGER = logging.getLogger("agent-registry-migration.target-registry")

#: Endpoint template for the command an operator runs with a derived payload.
TARGET_CONTROL_ENDPOINT = "https://agent-registry-control.{region}.api.aws"

#: Poll budget for a new registry to leave ``CREATING``. Its own budget rather than the record poll
#: settings in the API adapter: those statuses are record statuses (``DRAFT``, ``APPROVED``, ...),
#: and a registry create provisions a workload identity, so it is slower than a record write and
#: settles once rather than per record.
REGISTRY_POLL_ATTEMPTS = 60
REGISTRY_POLL_INTERVAL_SECONDS = 5.0

#: Registry lifecycle states, from the target service model's ``RegistryStatus``.
REGISTRY_IN_PROGRESS_STATUSES = frozenset({"CREATING", "UPDATING"})
REGISTRY_READY_STATUS = "READY"
REGISTRY_FAILURE_STATUSES = frozenset({"CREATE_FAILED", "UPDATE_FAILED", "DELETING", "DELETE_FAILED"})

# Never set; waiting on it is a bounded, interruptible pause between poll attempts. Same reasoning
# as the record poll loop in registry_api -- see _POLL_WAIT there.
_POLL_WAIT = threading.Event()


def derive_create_registry_inputs(
    settings: dict[str, Any],
    mappings: list[dict[str, Any]],
    *,
    mapping_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return one entry per mapping describing the target registry to create.

    Each entry carries ``mappingId``, the ``source`` endpoint it was derived from, the target
    ``region`` the registry belongs in, and either a ``payload`` (the ``CreateRegistry`` input) or
    an ``error`` explaining why that mapping could not be described. Failures are per mapping so
    one unreachable registry does not hide the others.
    """
    api_config = settings["api"]["preview"]
    selected = [mapping for mapping in mappings if not mapping_ids or str(mapping.get("id")) in set(mapping_ids)]
    results: list[dict[str, Any]] = []
    for mapping in selected:
        mapping_id = str(mapping.get("id"))
        source = mapping["source"]
        target = mapping.get("target") or {}
        entry: dict[str, Any] = {
            "mappingId": mapping_id,
            "source": {
                "accountId": source.get("accountId"),
                "region": source.get("region"),
                "registryId": source.get("registryId"),
            },
            # Where the target registry has to be created for this mapping to load into it.
            "region": target.get("region") or source.get("region"),
            "payload": None,
            # What about this payload needs a decision before it is applied -- a preview-only
            # authorizer field that had to be dropped, or an audience naming the old registry.
            "warnings": [],
            "error": None,
        }
        try:
            invoker = invoker_for_endpoint(source, run_id=None, purpose="target-config")
            client = PreviewRegistryClient(invoker, api_config, str(source["region"]))
            preview_registry = client.describe_registry(registry_id=str(source["registryId"]))
            warnings: list[str] = []
            entry["payload"] = transform_registry_configuration(
                preview_registry,
                warnings=warnings,
                source_registry_id=str(source["registryId"]),
            )
            entry["warnings"] = warnings
        except Exception as error:
            entry["error"] = str(error)
            # The message goes in the report; the traceback goes to the log. Without it an
            # unexpected failure here is a one-line string with no way to find out what raised it.
            LOGGER.debug(
                "Could not derive the target registry configuration for mapping %s",
                mapping_id,
                exc_info=True,
            )
        results.append(entry)
    return results


def create_registry_command(entry: dict[str, Any], payload_path: str) -> str:
    """Return the single AWS CLI command that creates the registry described by ``entry``."""
    return (
        "aws agent-registry-control create-registry"
        f" --cli-input-json file://{payload_path}"
        f" --endpoint-url {TARGET_CONTROL_ENDPOINT.format(region=entry.get('region'))}"
        " --query registryArn --output text"
    )


def create_registry_prerequisite() -> str:
    """What to know before running the command above by hand instead of using ``--create``.

    The command needs an AWS CLI whose bundled model carries ``agent-registry-control``; an older
    one answers ``Invalid choice: 'agent-registry-control'``. ``--create`` has no such requirement,
    because it calls the same operation through this tool's own pinned SDK.

    Printed with the command rather than left in the documentation, because the command is meant to
    be copied and run, and a copied command that fails on the CLI's age is worse than no command.
    """
    return (
        "If the command above answers \"Invalid choice: 'agent-registry-control'\", the AWS CLI in\n"
        "this shell predates the new Registry service model. Either update it, or let this tool make\n"
        "the same call with its own pinned SDK:\n"
        "\n"
        "  agent-registry-migration target-config --create\n"
        "\n"
        "which creates each registry, waits for it to become READY, and writes the generated id into\n"
        "your configuration."
    )


def create_target_registries(
    settings: dict[str, Any],
    mappings: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create the registry each derived ``entry`` describes, and wait for it to become ``READY``.

    Mutates and returns ``entries``, adding ``registryId``/``registryArn``/``status`` for each
    registry created and ``createError`` for each that could not be. Failures stay per mapping, for
    the same reason deriving does: one registry that cannot be created must not hide the others, and
    the ids of those that succeeded still have to reach the caller so they are not created twice.
    """
    api_config = settings["api"]["target"]
    mapping_by_id = {str(mapping.get("id")): mapping for mapping in mappings}
    for entry in entries:
        if entry.get("error") or not entry.get("payload"):
            continue
        mapping_id = str(entry["mappingId"])
        mapping = mapping_by_id.get(mapping_id) or {}
        target = mapping.get("target") or {}
        region = str(entry["region"])
        try:
            invoker = invoker_for_endpoint(target, run_id=None, purpose="create-registry")
            client = build_control_plane_client(
                session=invoker.session(),
                service_name=_required_service_name(api_config),
                region=region,
                endpoint_url=target_endpoint_url(api_config, region),
            )
            payload = dict(entry["payload"])
            payload["clientToken"] = client_token(mapping_id, payload)
            LOGGER.info("Creating the target registry for mapping %s in %s", mapping_id, region)
            registry_arn = str(client.create_registry(**payload)["registryArn"])
            registry_id = registry_arn.rsplit("/", 1)[-1]
            entry["registryArn"] = registry_arn
            entry["registryId"] = registry_id
            # Recorded before the wait, so an id that exists is reported even if waiting for it to
            # settle is what fails. Without it a timeout leaves a real registry the caller cannot
            # name, and the next run creates a second one.
            entry["status"] = wait_for_registry(client, registry_id)
        except Exception as error:
            entry["createError"] = str(error)
            LOGGER.debug("Could not create the target registry for mapping %s", mapping_id, exc_info=True)
    return entries


def wait_for_registry(client: Any, registry_id: str) -> str:
    """Poll ``GetRegistry`` until the registry settles, and return the status it settled in.

    Raises on a failure status or on running out of attempts. A failure carries the service's own
    ``statusReason`` -- the one users actually hit is "Unable to create workload identity because
    access was denied", which names a missing permission and is worth quoting verbatim rather than
    reporting as a generic timeout.
    """
    status = ""
    for _ in range(REGISTRY_POLL_ATTEMPTS):
        registry = client.get_registry(registryId=registry_id)
        status = str(registry.get("status") or "")
        if status == REGISTRY_READY_STATUS:
            return status
        if status in REGISTRY_FAILURE_STATUSES:
            reason = str(registry.get("statusReason") or "no reason given")
            raise RuntimeError(f"Target registry {registry_id} is in status {status}: {reason}")
        if status not in REGISTRY_IN_PROGRESS_STATUSES:
            # An unmodeled status. Returning it beats waiting out the budget on a state this
            # version does not know about, and the caller prints it.
            return status
        _POLL_WAIT.wait(REGISTRY_POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"Target registry {registry_id} was still in status {status or 'CREATING'} after "
        f"{REGISTRY_POLL_ATTEMPTS} attempt(s). It exists -- put its id into your configuration as "
        "target.registryId once it reaches READY."
    )


def client_token(mapping_id: str, payload: dict[str, Any]) -> str:
    """Return a token making a retried create idempotent rather than duplicating a registry.

    Derived from the mapping and the payload, so re-running after an interrupted create returns the
    registry the first attempt made, while a genuinely different configuration is a different
    request. Deterministic on purpose: a random token would make the retry create a second registry,
    which is the failure this exists to prevent.
    """
    material = json.dumps({"mappingId": mapping_id, "payload": payload}, sort_keys=True, default=str)
    return "arm-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _required_service_name(api_config: dict[str, Any]) -> str:
    """Read the target service name from configuration, failing with the field's name if it is unset."""
    service_name = str(api_config.get("serviceName") or "").strip()
    if not service_name:
        raise RuntimeError("Target API configuration is missing serviceName")
    return service_name


def unknown_mapping_ids(
    mappings: list[dict[str, Any]],
    mapping_ids: list[str] | None,
) -> list[str]:
    """Return the requested mapping ids that do not exist, so a typo is named rather than ignored."""
    if not mapping_ids:
        return []
    known = {str(mapping.get("id")) for mapping in mappings}
    return sorted(set(mapping_ids) - known)
