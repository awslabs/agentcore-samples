"""Tests for SSM-backed configuration loading.

Covers the grouped one-parameter-per-concern layout (<prefix>/config, <prefix>/registries,
<prefix>/adapter), the legacy per-knob / per-endpoint-field layout that older deployments still
have, knob type coercion, and validation failures.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.settings import (
    DEFAULT_CONFIG_PREFIX,
    ConfigurationError,
    _build_load,
    _parse_config_value,
    flag,
    load_configuration,
    load_configuration_from_file,
    optional_argument,
    parse_job_arguments,
    parse_key_value_document,
    parse_registry_document,
    replay_configuration_fingerprint,
    required_argument,
    resolve_configuration,
    resolve_run_id,
    resolve_staging_bucket,
    validate_runtime_configuration,
)

PREFIX = "/agent-registry-migration"

ADAPTER = {
    "schemaVersion": 1,
    "transform": {"namePrefix": "migrated", "allowedRecordTypes": ["CUSTOM"], "passthroughFields": []},
    "api": {
        "preview": {"serviceName": "bedrock-agentcore-control", "signingName": "bedrock-agentcore"},
        "target": {"serviceName": "agent-registry-control", "signingName": "agent-registry"},
    },
}

SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "src-1"}
TARGET = {"accountId": "111122223333", "region": "us-west-2", "registryId": "tgt-1"}


class ParameterNotFound(Exception):
    """Stand-in for botocore's ssm ParameterNotFound."""


class _Exceptions:
    ParameterNotFound = ParameterNotFound


class FakeSsm:
    """Minimal SSM stub backed by a name -> value dict."""

    exceptions = _Exceptions()

    def __init__(self, parameters: dict[str, str]):
        self._parameters = parameters

    def get_parameter(self, Name: str, WithDecryption: bool = False):
        if Name not in self._parameters:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self._parameters[Name]}}

    def get_parameters_by_path(self, **kwargs):
        base = kwargs["Path"].rstrip("/")
        recursive = kwargs.get("Recursive", False)
        found = []
        for name, value in self._parameters.items():
            if not name.startswith(f"{base}/"):
                continue
            relative = name[len(base) + 1 :]
            if not recursive and "/" in relative:
                continue
            found.append({"Name": name, "Value": value})
        return {"Parameters": found}


def grouped_parameters(*, prefix: str = PREFIX, adapter: dict | None = None, **overrides) -> dict[str, str]:
    """The three deployed parameters. Keyword overrides set run knobs in the config document."""
    config = {
        "loadMode": "FULL",
        "changedAfter": None,
        "dryRun": True,
        "failOnRecordError": True,
        "recordsPerObject": 500,
        "allowReplayConfigurationDrift": False,
    }
    config.update(overrides)
    return {
        f"{prefix}/config": json.dumps(config),
        f"{prefix}/registries": json.dumps([{"id": "map-a", "source": SOURCE, "target": TARGET}]),
        f"{prefix}/adapter": json.dumps(adapter if adapter is not None else ADAPTER),
    }


class GroupedLayout(unittest.TestCase):
    def test_reads_grouped_config_and_registries(self):
        settings, mappings = load_configuration(FakeSsm(grouped_parameters()), PREFIX)
        self.assertEqual(
            settings["load"],
            {
                "mode": "FULL",
                "changedAfter": None,
                "dryRun": True,
                "failOnRecordError": True,
                "recordsPerObject": 500,
                "loadConcurrency": 32,
                "dumpExtractedRecords": True,
                "allowReplayConfigurationDrift": False,
                "matchSourceStatus": True,
            },
        )
        self.assertEqual(settings["transform"], ADAPTER["transform"])
        self.assertEqual(settings["api"], ADAPTER["api"])
        self.assertEqual(mappings, [{"id": "map-a", "source": SOURCE, "target": TARGET}])

    def test_native_json_types_are_preserved(self):
        params = grouped_parameters(
            loadMode="INCREMENTAL",
            changedAfter="2026-08-01T00:00:00Z",
            dryRun=False,
            recordsPerObject=250,
        )
        settings, _ = load_configuration(FakeSsm(params), PREFIX)
        self.assertEqual(settings["load"]["mode"], "INCREMENTAL")
        self.assertEqual(settings["load"]["changedAfter"], "2026-08-01T00:00:00Z")
        self.assertIs(settings["load"]["dryRun"], False)
        self.assertEqual(settings["load"]["recordsPerObject"], 250)

    def test_registries_object_keyed_by_id_is_accepted(self):
        params = grouped_parameters()
        params[f"{PREFIX}/registries"] = json.dumps({"map-z": {"source": SOURCE, "target": TARGET}})
        _, mappings = load_configuration(FakeSsm(params), PREFIX)
        self.assertEqual([m["id"] for m in mappings], ["map-z"])

    def test_mappings_are_sorted_by_id(self):
        params = grouped_parameters()
        params[f"{PREFIX}/registries"] = json.dumps(
            [
                {"id": "m-b", "source": SOURCE, "target": TARGET},
                {"id": "m-a", "source": SOURCE, "target": TARGET},
            ]
        )
        _, mappings = load_configuration(FakeSsm(params), PREFIX)
        self.assertEqual([m["id"] for m in mappings], ["m-a", "m-b"])

    def test_invalid_json_reports_the_parameter_name(self):
        params = grouped_parameters()
        params[f"{PREFIX}/config"] = "{not json"
        with self.assertRaises(ConfigurationError) as ctx:
            load_configuration(FakeSsm(params), PREFIX)
        self.assertIn(f"{PREFIX}/config", str(ctx.exception))

    def test_missing_adapter_is_reported(self):
        params = grouped_parameters()
        del params[f"{PREFIX}/adapter"]
        with self.assertRaises(ConfigurationError) as ctx:
            load_configuration(FakeSsm(params), PREFIX)
        self.assertIn("adapter", str(ctx.exception))

    def test_nothing_deployed_yet_says_how_to_deploy_and_how_to_repoint(self):
        """The message a first `agent-registry-migration check --glue` hits with nothing deployed.

        It has to name both ways forward -- deploy the engine, or drop --glue and run without one --
        in the vocabulary the user has. Pointing at `npx cdk deploy` or `--config-prefix` would send
        them to surfaces the CLI deliberately hides.
        """
        params = grouped_parameters()
        del params[f"{PREFIX}/adapter"]
        with self.assertRaises(ConfigurationError) as ctx:
            load_configuration(FakeSsm(params), PREFIX)
        message = str(ctx.exception)
        self.assertIn(f"No migration deployment found at {PREFIX}", message)
        self.assertIn("agent-registry-migration deploy", message)
        # The other way out: a migration needs no deployment at all.
        self.assertIn("--glue", message)
        # Repointing is a configuration edit, named by the stack output that supplies the value.
        self.assertIn("engine.parameterPrefix", message)
        self.assertIn("ConfigurationParameterPrefix", message)
        self.assertNotIn("npx cdk", message)

    def test_incremental_without_changed_after_is_allowed(self):
        """The cutoff comes from each mapping's saved watermark, resolved at extract time."""
        params = grouped_parameters(loadMode="INCREMENTAL")
        settings, _ = load_configuration(FakeSsm(params), PREFIX)
        self.assertEqual(settings["load"]["mode"], "INCREMENTAL")
        self.assertIsNone(settings["load"]["changedAfter"])

    def test_invalid_changed_after_is_rejected(self):
        params = grouped_parameters(loadMode="INCREMENTAL", changedAfter="last tuesday")
        with self.assertRaises(ConfigurationError) as ctx:
            load_configuration(FakeSsm(params), PREFIX)
        self.assertIn("ISO-8601", str(ctx.exception))

    def test_bad_boolean_is_rejected(self):
        params = grouped_parameters(dryRun="flase")
        with self.assertRaises(ConfigurationError):
            load_configuration(FakeSsm(params), PREFIX)

    def test_endpoint_missing_required_field_is_rejected(self):
        params = grouped_parameters()
        params[f"{PREFIX}/registries"] = json.dumps(
            [{"id": "map-a", "source": {"accountId": "111122223333", "region": "us-east-1"}, "target": TARGET}]
        )
        with self.assertRaises(ConfigurationError):
            load_configuration(FakeSsm(params), PREFIX)

    def test_duplicate_mapping_id_is_rejected(self):
        params = grouped_parameters()
        params[f"{PREFIX}/registries"] = json.dumps(
            [
                {"id": "dup", "source": SOURCE, "target": TARGET},
                {"id": "dup", "source": SOURCE, "target": TARGET},
            ]
        )
        with self.assertRaises(ConfigurationError):
            load_configuration(FakeSsm(params), PREFIX)


CONFIG_DOCUMENT = """\
# Agent Registry migration -- run settings.
# Format: one "key = value" per line.

loadMode = FULL
changedAfter =
dryRun = true
failOnRecordError = true
recordsPerObject = 250
allowReplayConfigurationDrift = false
"""

REGISTRIES_DOCUMENT = """\
# One mapping per line.
#   <mappingId> = source=<accountId>/<region>/<registryId>, target=...

map-a = source=111122223333/us-east-1/src-1, target=111122223333/us-west-2/tgt-1
map-b = source=111122223333/eu-west-1/src-2, target=444455556666/eu-west-1/tgt-2, target.roleArn=arn:aws:iam::444455556666:role/Writer, target.externalId=ext-42
"""


class KeyValueDocumentLayout(unittest.TestCase):
    """The customer-facing format: editable key = value lines, one registry per line."""

    def _parameters(self, config_text=CONFIG_DOCUMENT, registries_text=REGISTRIES_DOCUMENT) -> dict[str, str]:
        return {
            f"{PREFIX}/config": config_text,
            f"{PREFIX}/registries": registries_text,
            f"{PREFIX}/adapter": json.dumps(ADAPTER),
        }

    def test_config_document_is_parsed(self):
        settings, _ = load_configuration(FakeSsm(self._parameters()), PREFIX)
        self.assertEqual(settings["load"]["mode"], "FULL")
        self.assertIsNone(settings["load"]["changedAfter"])
        self.assertIs(settings["load"]["dryRun"], True)
        self.assertEqual(settings["load"]["recordsPerObject"], 250)

    def test_multiple_registries_one_line_each(self):
        _, mappings = load_configuration(FakeSsm(self._parameters()), PREFIX)
        self.assertEqual([m["id"] for m in mappings], ["map-a", "map-b"])
        self.assertEqual(
            mappings[0],
            {
                "id": "map-a",
                "source": {"accountId": "111122223333", "region": "us-east-1", "registryId": "src-1"},
                "target": {"accountId": "111122223333", "region": "us-west-2", "registryId": "tgt-1"},
            },
        )

    def test_optional_cross_account_fields_are_parsed(self):
        _, mappings = load_configuration(FakeSsm(self._parameters()), PREFIX)
        target = mappings[1]["target"]
        self.assertEqual(target["accountId"], "444455556666")
        self.assertEqual(target["roleArn"], "arn:aws:iam::444455556666:role/Writer")
        self.assertEqual(target["externalId"], "ext-42")

    def test_adding_a_registry_line_adds_a_mapping(self):
        extended = (
            REGISTRIES_DOCUMENT + "map-c = source=111122223333/us-east-2/src-3, target=111122223333/us-east-2/tgt-3\n"
        )
        _, mappings = load_configuration(FakeSsm(self._parameters(registries_text=extended)), PREFIX)
        self.assertEqual([m["id"] for m in mappings], ["map-a", "map-b", "map-c"])

    def test_removing_a_registry_line_removes_a_mapping(self):
        only_first = "\n".join(line for line in REGISTRIES_DOCUMENT.splitlines() if not line.startswith("map-b"))
        _, mappings = load_configuration(FakeSsm(self._parameters(registries_text=only_first)), PREFIX)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])

    def test_comments_and_blank_lines_are_ignored(self):
        text = "# only comments\n\n   \n" + "loadMode = FULL\ndryRun = true\n"
        knobs = parse_key_value_document(text, "test")
        self.assertEqual(knobs, {"loadMode": "FULL", "dryRun": "true"})

    def test_inline_comment_is_trimmed(self):
        knobs = parse_key_value_document("dryRun = false # go live\n", "test")
        self.assertEqual(knobs["dryRun"], "false")

    def test_line_without_equals_is_rejected_with_line_number(self):
        with self.assertRaises(ConfigurationError) as ctx:
            parse_key_value_document("loadMode = FULL\noops\n", "myparam")
        self.assertIn("line 2", str(ctx.exception))

    def test_duplicate_key_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            parse_key_value_document("dryRun = true\ndryRun = false\n", "test")

    def test_malformed_endpoint_triple_is_rejected(self):
        with self.assertRaises(ConfigurationError) as ctx:
            parse_registry_document("map-a = source=111122223333/us-east-1, target=1/2/3", "myparam")
        self.assertIn("<accountId>/<region>/<registryId>", str(ctx.exception))

    def test_unknown_side_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            parse_registry_document("map-a = origin=1/2/3", "myparam")

    def test_json_value_is_still_accepted(self):
        """The previous JSON layout keeps working, so a deployment mid-upgrade is safe."""
        settings, mappings = load_configuration(FakeSsm(grouped_parameters()), PREFIX)
        self.assertEqual(settings["load"]["recordsPerObject"], 500)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])


class LegacyLayout(unittest.TestCase):
    """Deployments created before grouping keep working until they are redeployed."""

    def _legacy_parameters(self) -> dict[str, str]:
        return {
            f"{PREFIX}/config/loadMode": "FULL",
            f"{PREFIX}/config/dryRun": "false",
            f"{PREFIX}/config/failOnRecordError": "true",
            f"{PREFIX}/config/recordsPerObject": "250",
            f"{PREFIX}/config/allowReplayConfigurationDrift": "false",
            f"{PREFIX}/registries/map-a/source/accountId": SOURCE["accountId"],
            f"{PREFIX}/registries/map-a/source/region": SOURCE["region"],
            f"{PREFIX}/registries/map-a/source/registryId": SOURCE["registryId"],
            f"{PREFIX}/registries/map-a/target/accountId": TARGET["accountId"],
            f"{PREFIX}/registries/map-a/target/region": TARGET["region"],
            f"{PREFIX}/registries/map-a/target/registryId": TARGET["registryId"],
            f"{PREFIX}/adapter": json.dumps(ADAPTER),
        }

    def test_per_knob_and_per_field_parameters_still_load(self):
        settings, mappings = load_configuration(FakeSsm(self._legacy_parameters()), PREFIX)
        self.assertIs(settings["load"]["dryRun"], False)
        self.assertEqual(settings["load"]["recordsPerObject"], 250)
        self.assertEqual(mappings, [{"id": "map-a", "source": SOURCE, "target": TARGET}])

    def test_grouped_parameter_wins_when_both_exist(self):
        params = self._legacy_parameters()
        params.update(grouped_parameters(recordsPerObject=777))
        settings, mappings = load_configuration(FakeSsm(params), PREFIX)
        self.assertEqual(settings["load"]["recordsPerObject"], 777)
        self.assertIs(settings["load"]["dryRun"], True)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])


class ArgumentParsing(unittest.TestCase):
    def test_glue_and_kebab_styles_are_interchangeable(self):
        glue_style = parse_job_arguments(["--CONFIG_PREFIX", "/p", "--STAGING_BUCKET=b"])
        self.assertEqual(required_argument(glue_style, "CONFIG_PREFIX"), "/p")
        self.assertEqual(required_argument(glue_style, "STAGING_BUCKET"), "b")

        kebab_style = parse_job_arguments(["--config-prefix", "/p", "--staging-bucket=b"])
        # Looked up by the Glue-style name even though it was passed as kebab-case.
        self.assertEqual(required_argument(kebab_style, "CONFIG_PREFIX"), "/p")
        self.assertEqual(required_argument(kebab_style, "STAGING_BUCKET"), "b")

    def test_flag_without_value_becomes_true(self):
        self.assertEqual(parse_job_arguments(["--help"]).get("help"), "true")

    def test_environment_variable_fallback(self):
        os.environ["STAGING_BUCKET"] = "from-env"
        try:
            self.assertEqual(required_argument({}, "STAGING_BUCKET"), "from-env")
        finally:
            del os.environ["STAGING_BUCKET"]

    def test_missing_required_argument_lists_both_styles(self):
        with self.assertRaises(ConfigurationError) as ctx:
            required_argument({}, "CONFIG_PREFIX")
        message = str(ctx.exception)
        self.assertIn("--CONFIG_PREFIX", message)
        self.assertIn("--config-prefix", message)

    def test_optional_argument_returns_none_when_absent(self):
        self.assertIsNone(optional_argument({}, "CONFIG_FILE"))


class ArgumentDefaulting(unittest.TestCase):
    """The common case must need no arguments: the deployment publishes what a run needs."""

    def test_config_prefix_defaults_to_the_default_deployment(self):
        settings, mappings, source = resolve_configuration(
            {}, lambda: FakeSsm(grouped_parameters(prefix=DEFAULT_CONFIG_PREFIX))
        )
        self.assertEqual(source, f"SSM {DEFAULT_CONFIG_PREFIX}")
        self.assertEqual(len(mappings), 1)
        self.assertEqual(settings["load"]["mode"], "FULL")

    def test_explicit_config_prefix_wins(self):
        arguments = parse_job_arguments(["--config-prefix", "/other/wave-2"])
        _settings, _mappings, source = resolve_configuration(
            arguments, lambda: FakeSsm(grouped_parameters(prefix="/other/wave-2"))
        )
        self.assertEqual(source, "SSM /other/wave-2")

    def test_staging_bucket_comes_from_the_deployment(self):
        settings = {"engine": {"stagingBucket": "published-bucket"}}
        self.assertEqual(resolve_staging_bucket({}, settings), "published-bucket")

    def test_published_bucket_travels_from_ssm_into_settings(self):
        adapter = dict(ADAPTER, engine={"stagingBucket": "deployed-bucket", "deploymentId": "default"})
        settings, _mappings = load_configuration(FakeSsm(grouped_parameters(adapter=adapter)), PREFIX)
        self.assertEqual(settings["engine"]["stagingBucket"], "deployed-bucket")
        self.assertEqual(resolve_staging_bucket({}, settings), "deployed-bucket")

    def test_publishing_the_bucket_does_not_change_the_replay_fingerprint(self):
        # Otherwise redeploying to publish it would invalidate every extract already staged.
        without = dict(ADAPTER)
        with_engine = dict(ADAPTER, engine={"stagingBucket": "deployed-bucket"})
        base, _ = load_configuration(FakeSsm(grouped_parameters(adapter=without)), PREFIX)
        published, _ = load_configuration(FakeSsm(grouped_parameters(adapter=with_engine)), PREFIX)
        self.assertEqual(
            replay_configuration_fingerprint(base),
            replay_configuration_fingerprint(published),
        )

    def test_explicit_staging_bucket_wins(self):
        settings = {"engine": {"stagingBucket": "published-bucket"}}
        arguments = parse_job_arguments(["--staging-bucket", "other-bucket"])
        self.assertEqual(resolve_staging_bucket(arguments, settings), "other-bucket")

    def test_missing_staging_bucket_explains_both_ways_to_supply_it(self):
        with self.assertRaises(ConfigurationError) as ctx:
            resolve_staging_bucket({}, {"engine": {}})
        message = str(ctx.exception)
        self.assertIn("--staging-bucket", message)
        self.assertIn("StagingBucketName", message)

    def test_missing_staging_bucket_is_tolerated_when_not_required(self):
        # Configuration-only validation runs before any bucket exists.
        self.assertIsNone(resolve_staging_bucket({}, {}, required=False))

    def test_deployments_made_before_engine_was_published_still_load(self):
        # The adapter of an older deployment carries no `engine` section.
        adapter = {key: value for key, value in ADAPTER.items()}
        parameters = grouped_parameters()
        parameters[f"{PREFIX}/adapter"] = json.dumps(adapter)
        settings, _mappings = load_configuration(FakeSsm(parameters), PREFIX)
        self.assertEqual(settings["engine"], {})
        self.assertIsNone(resolve_staging_bucket({}, settings, required=False))


class CliDefaultPrefixMatchesTheStack(unittest.TestCase):
    """The CLI default and the deploy-time warning must agree about one literal.

    The commands default to a prefix, and the stack warns when a deployment publishes somewhere
    else. Those two live in different languages, so nothing but a test keeps them honest.
    """

    def test_the_stack_uses_the_same_default_prefix(self):
        stack_source = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "migration-engine-stack.ts"
        )
        if not os.path.isfile(stack_source):
            self.skipTest("CDK sources are not present (running from an installed wheel)")
        with open(stack_source, "r", encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r"CLI_DEFAULT_PARAMETER_PREFIX\s*=\s*'([^']+)'", text)
        self.assertIsNotNone(match, "CLI_DEFAULT_PARAMETER_PREFIX not found in the stack")
        self.assertEqual(match.group(1), DEFAULT_CONFIG_PREFIX)


class PublishedRunKnobsCoverEveryKnobTheJobReads(unittest.TestCase):
    """The stack must publish every run knob ``_build_load`` reads.

    This is a regression test for a real, silent divergence. ``matchSourceStatus`` was accepted and
    validated by lib/config.ts and read by ``_build_load``, but ``renderRunConfigDocument`` never
    emitted it -- so a customer who set it to ``false`` got that honoured by a local run and silently
    ignored by the deployed Glue run, which published records to their source status instead of
    leaving them in DRAFT for review.

    The TypeScript side is now typed so that a missing knob is a compile error
    (``Record<keyof LoadConfig, PublishedKnob>``). This closes the loop from the Python side, which
    is where the reading happens, and catches the reverse case too: a knob published under a name the
    job does not read is equally useless.
    """

    #: Read from `_build_load`, which is the only thing that turns the published document into the
    #: settings a job uses. Written out rather than introspected so the test states the contract.
    KNOBS_THE_JOB_READS: ClassVar[set[str]] = {
        "dryRun",
        "loadMode",
        "changedAfter",
        "failOnRecordError",
        "recordsPerObject",
        "loadConcurrency",
        "dumpExtractedRecords",
        "allowReplayConfigurationDrift",
        "matchSourceStatus",
    }

    def _stack_source(self) -> str:
        stack_source = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
            "..",
            "lib",
            "migration-engine-stack.ts",
        )
        if not os.path.isfile(stack_source):
            self.skipTest("CDK sources are not present (running from an installed wheel)")
        with open(stack_source, "r", encoding="utf-8") as handle:
            return handle.read()

    def _published_names(self) -> set[str]:
        """The knob names `publishedRunKnobs` emits, read out of the stack source."""
        text = self._stack_source()
        start = text.index("function publishedRunKnobs(")
        end = text.index("function renderRunConfigDocument(")
        return set(re.findall(r"name:\s*'([A-Za-z]+)'", text[start:end]))

    def test_the_job_reads_exactly_the_knobs_the_document_defines(self):
        """Guards against reading a knob nobody publishes, which was the actual bug."""
        knobs = _build_load({})
        # `_build_load` names the mode `mode` internally; the document calls it `loadMode`.
        read = {"loadMode" if key == "mode" else key for key in knobs}
        self.assertEqual(read, self.KNOBS_THE_JOB_READS)

    def test_the_stack_publishes_every_knob_the_job_reads(self):
        self.assertEqual(self._published_names(), self.KNOBS_THE_JOB_READS)

    def test_match_source_status_survives_the_round_trip(self):
        """The specific value that used to be dropped, through the real parser."""
        document = "# comment\ndryRun = true\nloadMode = FULL\nchangedAfter =\nmatchSourceStatus = false"
        load = _build_load(_parse_config_value(document, "ssm"))
        self.assertFalse(load["matchSourceStatus"])

    def test_an_absent_knob_still_falls_back_to_its_default(self):
        """A deployment made before a knob existed must keep working."""
        load = _build_load(_parse_config_value("dryRun = true", "ssm"))
        self.assertTrue(load["matchSourceStatus"])


class NumericKnobsRejectBooleans(unittest.TestCase):
    """``bool`` is an ``int`` in Python, so the range checks have to exclude it explicitly.

    ``recordsPerObject: true`` used to validate -- ``isinstance(True, int)`` is true and
    ``1 <= True <= 10000`` holds -- and then became 1, quietly staging one record per object.
    ``loadConcurrency`` already excluded bools; this pins both.
    """

    def _settings(self, **load):
        base = {
            "schemaVersion": 1,
            "load": {"mode": "FULL", "changedAfter": None, **load},
            "transform": {"namePrefix": "migrated"},
            "api": {"target": {}},
        }
        return base

    def test_a_boolean_records_per_object_is_refused(self):
        with self.assertRaises(ConfigurationError) as raised:
            validate_runtime_configuration(self._settings(recordsPerObject=True), [])
        self.assertIn("recordsPerObject", str(raised.exception))

    def test_a_boolean_load_concurrency_is_refused(self):
        with self.assertRaises(ConfigurationError):
            validate_runtime_configuration(self._settings(loadConcurrency=True), [])

    def test_out_of_range_values_are_refused(self):
        for load in ({"recordsPerObject": 0}, {"recordsPerObject": 10_001}, {"loadConcurrency": 33}):
            with self.assertRaises(ConfigurationError):
                validate_runtime_configuration(self._settings(**load), [])

    def test_valid_values_pass(self):
        validate_runtime_configuration(self._settings(recordsPerObject=500, loadConcurrency=8), [])


class DuplicateNameModes(unittest.TestCase):
    """``transform.duplicateNames`` decides what a target name collision does, so a typo must not pass.

    The claim guard treats any value that is not ``suffix`` as ``fail``, so an unrecognised spelling
    would silently give the stricter behaviour: a run that was asked to rename colliding records
    would refuse them instead, one at a time, with nothing pointing at the config as the cause.
    """

    def _settings(self, **transform):
        return {
            "schemaVersion": 1,
            "load": {"mode": "FULL", "changedAfter": None},
            "transform": {"namePrefix": "migrated", **transform},
            "api": {"target": {}},
        }

    def test_both_documented_modes_are_accepted(self):
        for mode in ("fail", "suffix"):
            with self.subTest(mode=mode):
                validate_runtime_configuration(self._settings(duplicateNames=mode), [])

    def test_an_absent_setting_is_the_default(self):
        """A deployment made before this setting existed still loads, on the strict default."""
        validate_runtime_configuration(self._settings(), [])

    def test_an_unrecognised_mode_is_refused_and_names_the_alternatives(self):
        with self.assertRaises(ConfigurationError) as raised:
            validate_runtime_configuration(self._settings(duplicateNames="Suffix"), [])
        self.assertIn("duplicateNames", str(raised.exception))
        self.assertIn("suffix", str(raised.exception))


class BooleanFlags(unittest.TestCase):
    """Flags come from the command line only, so the environment cannot change job behaviour."""

    def test_bare_flag_is_true_in_either_style(self):
        self.assertTrue(flag(parse_job_arguments(["--offline"]), "OFFLINE"))
        self.assertTrue(flag(parse_job_arguments(["--OFFLINE"]), "OFFLINE"))
        self.assertTrue(flag(parse_job_arguments(["--json"]), "JSON"))

    def test_absent_flag_is_false(self):
        self.assertFalse(flag(parse_job_arguments([]), "OFFLINE"))

    def test_explicit_false_is_respected(self):
        for value in ("false", "0", "no", "off", "FALSE"):
            with self.subTest(value=value):
                self.assertFalse(flag(parse_job_arguments([f"--json={value}"]), "JSON"))
        self.assertTrue(flag(parse_job_arguments(["--json=true"]), "JSON"))

    def test_environment_variable_cannot_set_a_flag(self):
        # A stray JSON=1 or OFFLINE=1 in a shell or CI environment must not change what a job does.
        for name in ("JSON", "OFFLINE"):
            os.environ[name] = "1"
            self.addCleanup(os.environ.pop, name, None)
        arguments = parse_job_arguments([])
        for name in ("JSON", "OFFLINE"):
            with self.subTest(name=name):
                self.assertFalse(flag(arguments, name))
                # ...while non-boolean arguments keep their documented env fallback.
                self.assertEqual(optional_argument(arguments, name), "1")


class FileBackedConfiguration(unittest.TestCase):
    """Standalone runs can read the same configuration from a local JSON document."""

    def _document(self) -> dict:
        return {
            "config": {"loadMode": "FULL", "dryRun": True, "recordsPerObject": 100},
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
            "adapter": ADAPTER,
        }

    def _write(self, document: dict) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(document, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_settings_and_mappings(self):
        settings, mappings = load_configuration_from_file(self._write(self._document()))
        self.assertEqual(settings["load"]["recordsPerObject"], 100)
        self.assertEqual(settings["transform"], ADAPTER["transform"])
        self.assertEqual([m["id"] for m in mappings], ["map-a"])

    def test_accepts_full_parameter_paths_as_keys(self):
        document = {
            f"{PREFIX}/config": self._document()["config"],
            f"{PREFIX}/registries": self._document()["registries"],
            f"{PREFIX}/adapter": ADAPTER,
        }
        settings, mappings = load_configuration_from_file(self._write(document))
        self.assertEqual(settings["load"]["recordsPerObject"], 100)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])

    def test_a_file_without_an_adapter_builds_one_from_the_bundled_contract(self):
        """A run with no deployment has nothing to export an adapter from, so it builds one.

        This is what makes a local run possible at all: the API contract ships in the package and
        the transform rules come from the same config file the CDK app reads.
        """
        document = self._document()
        del document["adapter"]
        settings, mappings = load_configuration_from_file(self._write(document))

        self.assertEqual([m["id"] for m in mappings], ["map-a"])
        self.assertEqual(settings["api"]["preview"]["serviceName"], "bedrock-agentcore-control")
        self.assertEqual(settings["api"]["target"]["serviceName"], "agent-registry-control")
        # The transform rules must be complete, including the implementation hash the replay
        # fingerprint depends on -- without it a local extract could not be safely re-loaded.
        self.assertEqual(settings["transform"]["namePrefix"], "migrated")
        # The strict default: a local run refuses the second source record to claim a target name
        # rather than renaming it, which is also what an unconfigured deployment does.
        self.assertEqual(settings["transform"]["duplicateNames"], "fail")
        self.assertRegex(settings["transform"]["implementationHash"], r"^[0-9a-f]{64}$")

    def test_a_deployment_config_file_is_accepted_as_is(self):
        """Point a local run at config/migration.json rather than a second document.

        The deployment config nests the run settings under `runtime.load`, which is the shape a
        user already maintains for the stack.
        """
        document = {
            "engine": {"account": "111122223333", "region": "us-west-2"},
            "runtime": {
                "load": {"loadMode": "FULL", "dryRun": True, "recordsPerObject": 100},
                "transform": {"namePrefix": "from-file"},
            },
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
        }
        settings, mappings = load_configuration_from_file(self._write(document))

        self.assertEqual(settings["load"]["recordsPerObject"], 100)
        self.assertTrue(settings["load"]["dryRun"])
        self.assertEqual(settings["transform"]["namePrefix"], "from-file")
        self.assertEqual([m["id"] for m in mappings], ["map-a"])

    def test_a_deployment_config_incremental_mode_is_not_silently_dropped(self):
        """The two shapes disagree on one key name, and getting it wrong is silent.

        A deployment config says `runtime.load.mode`; the run knobs say `loadMode`. Passing the
        block through untranslated left an INCREMENTAL run reading every record as a FULL load with
        nothing reported -- so this pins the translation.
        """
        document = {
            "engine": {"account": "111122223333", "region": "us-west-2"},
            "runtime": {"load": {"mode": "INCREMENTAL", "changedAfter": "2026-08-01T00:00:00Z"}},
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
        }
        settings, _mappings = load_configuration_from_file(self._write(document))
        self.assertEqual(settings["load"]["mode"], "INCREMENTAL")
        self.assertEqual(settings["load"]["changedAfter"], "2026-08-01T00:00:00Z")

    def test_the_knob_spelling_wins_when_a_file_carries_both(self):
        document = {
            "runtime": {"load": {"mode": "FULL", "loadMode": "INCREMENTAL"}},
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
        }
        settings, _mappings = load_configuration_from_file(self._write(document))
        self.assertEqual(settings["load"]["mode"], "INCREMENTAL")

    def test_the_other_load_knobs_survive_the_translation(self):
        document = {
            "runtime": {
                "load": {
                    "mode": "FULL",
                    "failOnRecordError": False,
                    "loadConcurrency": 2,
                    "recordsPerObject": 25,
                    "dumpExtractedRecords": False,
                    "allowReplayConfigurationDrift": True,
                }
            },
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
        }
        settings, _mappings = load_configuration_from_file(self._write(document))
        load = settings["load"]
        self.assertEqual(load["mode"], "FULL")
        self.assertFalse(load["failOnRecordError"])
        self.assertEqual(load["loadConcurrency"], 2)
        self.assertEqual(load["recordsPerObject"], 25)
        self.assertFalse(load["dumpExtractedRecords"])
        self.assertTrue(load["allowReplayConfigurationDrift"])

    def test_a_file_with_neither_adapter_nor_registries_says_what_is_missing(self):
        document = self._document()
        del document["adapter"]
        del document["registries"]
        with self.assertRaises(ConfigurationError) as ctx:
            load_configuration_from_file(self._write(document))
        message = str(ctx.exception)
        self.assertIn("aws ssm get-parameter", message)
        self.assertIn("registries", message)

    def test_missing_file_is_reported(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-here-12345.json")
        with self.assertRaises(ConfigurationError):
            load_configuration_from_file(missing)

    def test_resolve_configuration_prefers_the_file_and_needs_no_ssm(self):
        path = self._write(self._document())
        arguments = parse_job_arguments(["--config-file", path, "--staging-bucket", "b"])

        def exploding_factory():  # pragma: no cover - must never be called
            raise AssertionError("SSM must not be used when --config-file is supplied")

        settings, mappings, source = resolve_configuration(arguments, exploding_factory)
        self.assertEqual(settings["load"]["recordsPerObject"], 100)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])
        self.assertIn(path, source)

    def test_resolve_configuration_falls_back_to_ssm(self):
        arguments = parse_job_arguments(["--config-prefix", PREFIX])
        fake = FakeSsm(grouped_parameters())
        settings, mappings, source = resolve_configuration(arguments, lambda: fake)
        self.assertEqual(settings["load"]["recordsPerObject"], 500)
        self.assertEqual([m["id"] for m in mappings], ["map-a"])
        self.assertIn(PREFIX, source)


class RunIds(unittest.TestCase):
    def test_generated_run_id_is_timestamp_prefixed(self):
        run_id = resolve_run_id({}, allow_generate=True)
        self.assertRegex(run_id, r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

    def test_explicit_run_id_passes_through(self):
        self.assertEqual(resolve_run_id({"RUN_ID": "my-run"}, allow_generate=False), "my-run")

    def test_missing_run_id_without_generation_raises(self):
        with self.assertRaises(ConfigurationError):
            resolve_run_id({}, allow_generate=False)


if __name__ == "__main__":
    unittest.main()
