"""Pre-flight validation: catch bad configuration before a migration run starts.

A Glue run against a thousand records takes tens of minutes, so a typo in a registry id or a
missing permission should surface in seconds, not halfway through a load. These checks validate
everything that can be validated up front:

* the configuration parses and every value is in range;
* each mapping's account/region/registry ids are well formed, and no mapping migrates a registry
  onto itself or duplicates another mapping;
* an INCREMENTAL run actually has a cutoff (explicit ``changedAfter`` or a saved watermark);
* the staging bucket is readable and writable;
* every source and target registry exists and is reachable with the configured credentials.

Each check returns a :class:`CheckResult` with a status and, on failure, a concrete remedy. The
same function backs the standalone ``validate`` entrypoint and the fail-fast check the jobs run at
startup, so what an operator validates is exactly what the job enforces.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# Check outcomes. ``PASS`` trips Bandit's hardcoded-password heuristic, which matches any name
# containing "pass" against a string literal; this is a status label, not a credential.
PASS = "PASS"  # nosec B105
FAIL = "FAIL"
WARN = "WARN"

_ACCOUNT_PATTERN = re.compile(r"^\d{12}$")
_REGION_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_REGISTRY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-/:]+$")

# Where the bucket write probe lands. Inside state/, which the jobs already write to and which no
# lifecycle rule expires.
PROBE_KEY = "state/preflight/last-check.json"


@dataclass
class CheckResult:
    """One validation outcome."""

    name: str
    status: str
    detail: str
    remedy: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != FAIL

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.remedy:
            value["remedy"] = self.remedy
        return value


@dataclass
class PreflightReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [result for result in self.results if result.status == FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [result for result in self.results if result.status == WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": PASS if self.ok else FAIL,
            "checks": [result.as_dict() for result in self.results],
            "failureCount": len(self.failures),
            "warningCount": len(self.warnings),
        }

    def render(self) -> str:
        lines = []
        for result in self.results:
            lines.append(f"[{result.status:<4}] {result.name}: {result.detail}")
            if result.remedy and result.status != PASS:
                lines.append(f"         fix: {result.remedy}")
        verdict = (
            "Pre-flight validation PASSED"
            if self.ok
            else f"Pre-flight validation FAILED ({len(self.failures)} problem(s))"
        )
        if self.warnings:
            verdict += f", {len(self.warnings)} warning(s)"
        lines.append("")
        lines.append(verdict)
        return "\n".join(lines)


def check_mapping_shapes(mappings: list[dict[str, Any]]) -> list[CheckResult]:
    """Validate ids/regions and catch self-migrations and duplicate mappings."""
    results: list[CheckResult] = []
    if not mappings:
        return [
            CheckResult(
                name="registries.configured",
                status=FAIL,
                detail="No registry mappings are configured",
                remedy="Add a source/target pair to 'registries' in your configuration, or run "
                "'agent-registry-migration init' to create it",
            )
        ]

    seen_pairs: dict[tuple, str] = {}
    target_owners: dict[tuple, list[str]] = {}
    for mapping in mappings:
        mapping_id = str(mapping.get("id", "<unnamed>"))
        problems: list[str] = []
        for side in ("source", "target"):
            endpoint = mapping.get(side)
            if not isinstance(endpoint, dict):
                problems.append(f"{side} is missing")
                continue
            account = str(endpoint.get("accountId", ""))
            region = str(endpoint.get("region", ""))
            registry_id = str(endpoint.get("registryId", ""))
            if not _ACCOUNT_PATTERN.match(account):
                problems.append(f"{side}.accountId {account!r} is not a 12-digit account id")
            if not _REGION_PATTERN.match(region):
                problems.append(f"{side}.region {region!r} is not an AWS region (example us-east-1)")
            if not registry_id or not _REGISTRY_ID_PATTERN.match(registry_id):
                problems.append(f"{side}.registryId {registry_id!r} is empty or has unsupported characters")
            if endpoint.get("externalId") and not endpoint.get("roleArn"):
                problems.append(f"{side}.externalId is only meaningful together with {side}.roleArn")

        source = mapping.get("source") if isinstance(mapping.get("source"), dict) else {}
        target = mapping.get("target") if isinstance(mapping.get("target"), dict) else {}
        source_key = (source.get("accountId"), source.get("region"), source.get("registryId"))
        target_key = (target.get("accountId"), target.get("region"), target.get("registryId"))
        if all(source_key) and source_key == target_key:
            problems.append("source and target are the same registry, which would migrate it onto itself")

        if problems:
            results.append(
                CheckResult(
                    name=f"registries.{mapping_id}.shape",
                    status=FAIL,
                    detail="; ".join(problems),
                    remedy=f"Correct the {mapping_id!r} entry under 'registries' in your configuration",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"registries.{mapping_id}.shape",
                    status=PASS,
                    detail=f"{_describe(source)} -> {_describe(target)}",
                )
            )

        # A target registry is normally created alongside its preview registry, and record content can
        # carry region-bound ARNs (an OAuth credential provider, an iamCredentialProvider region)
        # that are copied across verbatim. Crossing regions is allowed -- some estates consolidate
        # deliberately -- but it is worth naming before a run rather than after.
        if source.get("region") and target.get("region") and source.get("region") != target.get("region"):
            results.append(
                CheckResult(
                    name=f"registries.{mapping_id}.crossRegion",
                    status=WARN,
                    detail=(f"source region {source.get('region')} differs from target region {target.get('region')}"),
                    remedy=(
                        "Deliberate consolidation is fine. Otherwise point the target at the same "
                        "region: record content is copied as-is, so any region-bound ARN in a "
                        "credential provider will still refer to the source region"
                    ),
                )
            )

        pair = (source_key, target_key)
        if pair in seen_pairs:
            results.append(
                CheckResult(
                    name=f"registries.{mapping_id}.duplicate",
                    status=FAIL,
                    detail=f"Duplicates mapping {seen_pairs[pair]!r}: same source and target",
                    remedy="Remove one of the two identical entries under 'registries'",
                )
            )
        else:
            seen_pairs[pair] = mapping_id
        target_owners.setdefault(target_key, []).append(mapping_id)

    for target_key, owners in target_owners.items():
        if len(owners) > 1:
            results.append(
                CheckResult(
                    name="registries.sharedTarget",
                    status=WARN,
                    detail=f"Mappings {', '.join(owners)} all load into {_describe_key(target_key)}",
                    remedy="Intentional consolidation is fine; otherwise correct the target of one mapping",
                )
            )
    return results


def check_load_settings(settings: dict[str, Any]) -> list[CheckResult]:
    """Report the run's mode and safety switches so an operator sees them before starting."""
    load = settings.get("load", {})
    results = [
        CheckResult(
            name="config.loadMode",
            status=PASS,
            detail=f"{load.get('mode')} (changedAfter={load.get('changedAfter') or 'unset'})",
        )
    ]
    if load.get("dryRun"):
        results.append(
            CheckResult(
                name="config.dryRun",
                status=PASS,
                detail="dryRun=true: transform/load will NOT write to any target registry",
            )
        )
    else:
        results.append(
            CheckResult(
                name="config.dryRun",
                status=WARN,
                detail="dryRun=false: transform/load WILL write to the target registries",
                remedy="Drop --live to transform and report without writing anything",
            )
        )
    if load.get("failOnRecordError", False):
        results.append(
            CheckResult(
                name="config.failOnRecordError",
                status=PASS,
                detail="failOnRecordError=true: the run stops (nonzero exit) the moment any record fails",
            )
        )
    else:
        results.append(
            CheckResult(
                name="config.failOnRecordError",
                status=PASS,
                detail="failOnRecordError=false: a failed record is skipped and listed in the report; "
                "every other staged record is still processed",
            )
        )
    return results


def check_incremental_readiness(
    settings: dict[str, Any],
    mappings: list[dict[str, Any]],
    watermark_reader: Callable[[str], dict[str, Any] | None] | None,
) -> list[CheckResult]:
    """An INCREMENTAL run needs a cutoff per mapping: explicit, or a saved watermark."""
    load = settings.get("load", {})
    if str(load.get("mode", "")).upper() != "INCREMENTAL":
        return []
    if load.get("changedAfter"):
        return [
            CheckResult(
                name="incremental.cutoff",
                status=PASS,
                detail=f"All mappings use the configured changedAfter {load['changedAfter']}",
            )
        ]
    if watermark_reader is None:
        return [
            CheckResult(
                name="incremental.cutoff",
                status=WARN,
                detail="Cannot verify saved watermarks without access to the staging bucket",
            )
        ]

    results: list[CheckResult] = []
    for mapping in mappings:
        mapping_id = str(mapping.get("id", "<unnamed>"))
        try:
            saved = watermark_reader(mapping_id)
        except Exception as error:  # noqa: BLE001 - surfaced as a check failure
            results.append(
                CheckResult(
                    name=f"incremental.{mapping_id}.watermark",
                    status=FAIL,
                    detail=f"Could not read the saved watermark: {error}",
                    remedy="Confirm the job role can read state/* in the staging bucket",
                )
            )
            continue
        boundary = (saved or {}).get("maxUpdatedAt") or (saved or {}).get("lastLoadedAt")
        if boundary:
            results.append(
                CheckResult(
                    name=f"incremental.{mapping_id}.watermark",
                    status=PASS,
                    detail=f"Resumes from {boundary} (last load {saved.get('lastLoadedAt', 'unknown')})",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"incremental.{mapping_id}.watermark",
                    status=FAIL,
                    detail="INCREMENTAL run with no changedAfter and no saved watermark for this mapping",
                    remedy="Run a full load once to establish the watermark, or name an explicit "
                    "cutoff: agent-registry-migration run --since <ISO-8601 timestamp>",
                )
            )
    return results


def check_staging_bucket(store: Any) -> list[CheckResult]:
    """Confirm the staging bucket accepts the writes the jobs depend on."""
    try:
        store.put_json(PROBE_KEY, {"check": "preflight", "bucket": store.bucket})
    except Exception as error:  # noqa: BLE001 - surfaced as a check failure
        return [
            CheckResult(
                name="staging.writable",
                status=FAIL,
                detail=f"Cannot write {store.location(PROBE_KEY)}: {error}",
                remedy="Grant the job role s3:PutObject on the staging bucket prefixes "
                "runs/*, reports/* and state/*, and confirm the bucket name",
            )
        ]
    results = [CheckResult(name="staging.writable", status=PASS, detail=f"{store.location()} accepts writes")]
    try:
        store.get_json(PROBE_KEY)
        results.append(CheckResult(name="staging.readable", status=PASS, detail=f"{store.location()} is readable"))
    except Exception as error:  # noqa: BLE001 - surfaced as a check failure
        results.append(
            CheckResult(
                name="staging.readable",
                status=FAIL,
                detail=f"Cannot read {store.location(PROBE_KEY)}: {error}",
                remedy="Grant the job role s3:GetObject on the staging bucket",
            )
        )
    return results


def check_registry_access(
    mappings: list[dict[str, Any]],
    *,
    side: str,
    prober: Callable[[dict[str, Any]], Any],
    label: str,
) -> list[CheckResult]:
    """Probe each mapping's registry with a 1-record list call to prove access and existence."""
    results: list[CheckResult] = []
    for mapping in mappings:
        mapping_id = str(mapping.get("id", "<unnamed>"))
        endpoint = mapping.get(side)
        if not isinstance(endpoint, dict):
            continue
        try:
            prober(endpoint)
        except Exception as error:  # noqa: BLE001 - surfaced as a check failure
            results.append(
                CheckResult(
                    name=f"{side}.{mapping_id}.reachable",
                    status=FAIL,
                    detail=f"{label} {_describe(endpoint)} is not reachable: {_short(error)}",
                    remedy=f"Check the registry id exists in that account/region, and that the job "
                    f"role (or {side}.roleArn) may list its records",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"{side}.{mapping_id}.reachable",
                    status=PASS,
                    detail=f"{label} {_describe(endpoint)} is reachable",
                )
            )
    return results


#: Service models the run cannot proceed without, and which side of the migration each one serves.
REQUIRED_SERVICE_MODELS = {
    "bedrock-agentcore-control": "read Preview records",
    "agent-registry-control": "write target records",
}

#: First botocore release carrying ``agent-registry-control``. Named in the remedy, not compared
#: against: see :func:`check_sdk_models`.
MINIMUM_BOTOCORE_VERSION = "1.43.66"


def check_sdk_models(available_services: Iterable[str] | None = None) -> list[CheckResult]:
    """Check the SDK on this worker models both control planes.

    This is the failure that used to arrive latest and cost most. The extract stage reads Preview
    with ``bedrock-agentcore-control`` and only the load stage writes the target registry with
    ``agent-registry-control``, so an SDK carrying one model and not the other stages a full run
    successfully and then dies on the first create with ``UnknownServiceError``. Asserting both
    up front turns that into a sentence before anything is read.

    Deliberately a capability check and not a version comparison: an operator who registered the
    model through ``AWS_DATA_PATH`` or ``~/.aws/models`` on an older botocore is equally able to run
    the migration, and a version floor would reject that working setup. The version only appears in
    the remedy, as the way to get the model if you do not have it.
    """
    if available_services is None:  # pragma: no cover - exercised by passing the list in
        import botocore.session

        available_services = botocore.session.get_session().get_available_services()
    present = set(available_services)
    missing = {name: purpose for name, purpose in REQUIRED_SERVICE_MODELS.items() if name not in present}
    if not missing:
        return [
            CheckResult(
                name="sdk.serviceModels",
                status=PASS,
                detail=f"the SDK models {_describe_key(sorted(REQUIRED_SERVICE_MODELS))}",
            )
        ]
    return [
        CheckResult(
            name="sdk.serviceModels",
            status=FAIL,
            detail=(
                "this SDK has no service model for "
                + ", ".join(f"{name} (needed to {purpose})" for name, purpose in sorted(missing.items()))
            ),
            remedy=(
                f"install boto3 and botocore {MINIMUM_BOTOCORE_VERSION} or newer, which requires "
                "Python 3.10 or newer. On AWS Glue this comes from --additional-python-modules, "
                "which the deployed jobs set; a job missing it is running an older deployment of "
                "this solution, so redeploy with `agent-registry-migration deploy`"
            ),
        )
    ]


def check_shadowed_target_model(model_root: str | None = None) -> list[CheckResult]:
    """Warn when a hand-installed target model shadows the SDK's own, hiding registry operations.

    ``~/.aws/models`` takes precedence over the model bundled with botocore. An interim
    ``agent-registry-control`` model copied there during the preview carries the six record
    operations and nothing else, so ``CreateRegistry`` disappears from a perfectly current SDK --
    which makes ``target-config --create`` fail, and makes the AWS CLI answer "Invalid choice:
    'create-registry'" for a service it otherwise knows.

    A warning rather than a failure: records still migrate with the shadowing model, since the load
    only ever calls record operations. What stops working is creating the registry, so the check
    names the file to delete rather than blocking the run.
    """
    root = model_root or os.path.join(os.path.expanduser("~"), ".aws", "models")
    override = os.path.join(root, "agent-registry-control")
    if not os.path.isdir(override):
        return []
    return [
        CheckResult(
            name="sdk.shadowedTargetModel",
            status=WARN,
            detail=(
                f"{override} overrides the SDK's own agent-registry-control model; if it predates "
                "the registry operations, creating a target registry fails while record migration "
                "still works"
            ),
            remedy=(
                f"delete {override} to use the model shipped with botocore "
                f"{MINIMUM_BOTOCORE_VERSION} or newer, unless you installed it deliberately"
            ),
        )
    ]


def run_checks(
    settings: dict[str, Any],
    mappings: list[dict[str, Any]],
    *,
    store: Any = None,
    watermark_reader: Callable[[str], dict[str, Any] | None] | None = None,
    source_prober: Callable[[dict[str, Any]], Any] | None = None,
    target_prober: Callable[[dict[str, Any]], Any] | None = None,
    workstation: bool = False,
) -> PreflightReport:
    """Run every applicable check and return the aggregated report.

    Probers and the store are optional so the pure configuration checks can run without AWS
    access (``validate --offline``), while a full run also proves connectivity.
    """
    results: list[CheckResult] = []
    # First, and with no arguments: every later check that touches a registry needs a client, and a
    # client needs the model. Reported before the configuration checks so the remedy an operator
    # reads first is the one that unblocks everything else.
    results.extend(check_sdk_models())
    # Only where a person is running commands. ``~/.aws/models`` cannot exist on a Glue worker, and
    # a job reporting on the operator's home directory would be reporting on the wrong machine.
    if workstation:
        results.extend(check_shadowed_target_model())
    results.extend(check_load_settings(settings))
    results.extend(check_mapping_shapes(mappings))
    if store is not None:
        results.extend(check_staging_bucket(store))
    results.extend(check_incremental_readiness(settings, mappings, watermark_reader))
    if source_prober is not None:
        results.extend(check_registry_access(mappings, side="source", prober=source_prober, label="Preview registry"))
    if target_prober is not None:
        results.extend(check_registry_access(mappings, side="target", prober=target_prober, label="target registry"))
    return PreflightReport(results=results)


def _describe(endpoint: dict[str, Any]) -> str:
    return f"{endpoint.get('accountId', '?')}/{endpoint.get('region', '?')}/{endpoint.get('registryId', '?')}"


def _describe_key(key: Iterable[Any]) -> str:
    return "/".join(str(part) for part in key)


def _short(error: Exception, limit: int = 200) -> str:
    text = str(error).replace("\n", " ")
    return text[:limit]
