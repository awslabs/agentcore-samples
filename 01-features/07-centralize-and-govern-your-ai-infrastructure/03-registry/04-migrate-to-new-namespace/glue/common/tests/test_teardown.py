"""Tests for teardown, which backs `agent-registry-migration destroy`.

This is the one thing that deletes, so the tests are about restraint: the default must delete
nothing, `--yes` alone must not touch the data, and nothing anywhere may call a registry API. The
version-aware bucket emptying is covered too, because a versioned bucket that looks empty can still
hold thousands of billable versions.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import teardown

BUCKET = "staging-bucket"
STACK = "AgentRegistryMigrationEngine"


class FakePaginator:
    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs):
        return list(self._pages)


class FakeCloudFormation:
    def __init__(self, *, protection: bool = True, outputs: dict | None = None) -> None:
        self.protection = protection
        self.outputs = (
            outputs
            if outputs is not None
            else {
                "StagingBucketName": BUCKET,
                "ConfigurationParameterPrefix": "/agent-registry-migration/default",
            }
        )
        self.calls: list[str] = []

    def describe_stacks(self, StackName: str):
        self.calls.append(f"describe_stacks:{StackName}")
        return {
            "Stacks": [
                {
                    "StackName": StackName,
                    "StackStatus": "CREATE_COMPLETE",
                    "EnableTerminationProtection": self.protection,
                    "Outputs": [{"OutputKey": key, "OutputValue": value} for key, value in self.outputs.items()],
                }
            ]
        }

    def get_paginator(self, name: str):
        self.calls.append(f"get_paginator:{name}")
        return FakePaginator(
            [
                {
                    "StackResourceSummaries": [
                        {"ResourceType": "AWS::Glue::Job"},
                        {"ResourceType": "AWS::Glue::Job"},
                        {"ResourceType": "AWS::SSM::Parameter"},
                        {"ResourceType": "AWS::S3::Bucket"},
                    ]
                }
            ]
        )

    def update_termination_protection(self, **kwargs):
        self.calls.append("update_termination_protection")
        self.protection = kwargs["EnableTerminationProtection"]

    def delete_stack(self, StackName: str):
        self.calls.append(f"delete_stack:{StackName}")

    def get_waiter(self, name: str):
        self.calls.append(f"get_waiter:{name}")

        class _Waiter:
            def wait(self, **_kwargs):
                pass

        return _Waiter()


def versions(count: int, *, prefix: str = "runs/", size: int = 10) -> list[dict]:
    return [
        {"Key": f"{prefix}object-{index}", "VersionId": f"v{index}", "Size": size, "IsLatest": index == 0}
        for index in range(count)
    ]


class FakeS3:
    def __init__(self, pages: list[dict] | None = None) -> None:
        self.pages = (
            pages
            if pages is not None
            else [
                {
                    "Versions": versions(2, prefix="runs/", size=1024) + versions(1, prefix="reports/", size=2048),
                    "DeleteMarkers": [{"Key": "runs/gone", "VersionId": "v9", "Size": 0}],
                }
            ]
        )
        self.deleted: list[dict] = []
        self.deleted_buckets: list[str] = []

    def get_paginator(self, name: str):
        assert name == "list_object_versions", name
        return FakePaginator(self.pages)

    def delete_objects(self, Bucket: str, Delete: dict):
        self.deleted.extend(Delete["Objects"])
        return {}

    def delete_bucket(self, Bucket: str):
        self.deleted_buckets.append(Bucket)


class FakeSession:
    def __init__(self, cloudformation: FakeCloudFormation, s3: FakeS3) -> None:
        self._clients = {"cloudformation": cloudformation, "s3": s3}

    def client(self, name: str):
        return self._clients[name]


class PlanOnlyByDefault(unittest.TestCase):
    def setUp(self):
        self.cfn = FakeCloudFormation()
        self.s3 = FakeS3()
        self.session = FakeSession(self.cfn, self.s3)

    def test_default_run_deletes_nothing(self):
        exit_code = teardown.main(["teardown.py"], session=self.session)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("delete_stack:" + STACK, self.cfn.calls)
        self.assertEqual(self.s3.deleted, [])
        self.assertEqual(self.s3.deleted_buckets, [])
        self.assertEqual(self.cfn.protection, True, "termination protection must stay on")

    def test_plan_names_what_goes_and_what_survives(self):
        plan = teardown.build_plan(self.cfn, self.s3, STACK)
        text = teardown.render_plan(plan, _options())
        self.assertIn("WILL BE DELETED", text)
        self.assertIn("2 x AWS::Glue::Job", text)
        self.assertIn("WILL SURVIVE", text)
        self.assertIn("never deletes records", text)
        # The bucket is retained unless asked for, and the plan says so with its size.
        self.assertIn(f"s3://{BUCKET}", text)
        self.assertIn("--delete-data", text)
        # The retained bucket must not be listed as a deleted resource.
        deleted_section = text.split("WILL SURVIVE")[0]
        self.assertNotIn("AWS::S3::Bucket", deleted_section)

    def test_missing_stack_is_a_clear_error_not_a_traceback(self):
        class Missing(FakeCloudFormation):
            def describe_stacks(self, StackName: str):
                from botocore.exceptions import ClientError

                raise ClientError(
                    {"Error": {"Code": "ValidationError", "Message": f"Stack with id {StackName} does not exist"}},
                    "DescribeStacks",
                )

        session = FakeSession(Missing(), self.s3)
        exit_code = teardown.main(["teardown.py"], session=session)
        self.assertEqual(exit_code, 1)


class ConfirmedTeardown(unittest.TestCase):
    def setUp(self):
        self.cfn = FakeCloudFormation()
        self.s3 = FakeS3()
        self.session = FakeSession(self.cfn, self.s3)

    def test_yes_deletes_the_stack_but_keeps_the_data(self):
        teardown.main(["teardown.py", "--yes"], session=self.session)
        self.assertIn("update_termination_protection", self.cfn.calls)
        self.assertIn(f"delete_stack:{STACK}", self.cfn.calls)
        self.assertIn("get_waiter:stack_delete_complete", self.cfn.calls)
        self.assertEqual(self.s3.deleted, [], "--yes alone must not touch staged data")
        self.assertEqual(self.s3.deleted_buckets, [])

    def test_delete_data_empties_every_version_then_removes_the_bucket(self):
        teardown.main(["teardown.py", "--yes", "--delete-data"], session=self.session)
        # 3 versions + 1 delete marker.
        self.assertEqual(len(self.s3.deleted), 4)
        self.assertTrue(all("VersionId" in entry for entry in self.s3.deleted))
        self.assertEqual(self.s3.deleted_buckets, [BUCKET])

    def test_keep_reports_deletes_the_rest_and_leaves_the_bucket(self):
        teardown.main(["teardown.py", "--yes", "--delete-data", "--keep-reports"], session=self.session)
        deleted_keys = [entry["Key"] for entry in self.s3.deleted]
        self.assertTrue(all(not key.startswith("reports/") for key in deleted_keys), deleted_keys)
        self.assertTrue(any(key.startswith("runs/") for key in deleted_keys))
        self.assertEqual(self.s3.deleted_buckets, [], "the bucket must stay to hold reports/")

    def test_keep_reports_without_delete_data_is_rejected(self):
        exit_code = teardown.main(["teardown.py", "--yes", "--keep-reports"], session=self.session)
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.cfn.calls, [])

    def test_termination_protection_is_left_alone_when_already_off(self):
        cfn = FakeCloudFormation(protection=False)
        teardown.main(["teardown.py", "--yes"], session=FakeSession(cfn, self.s3))
        self.assertNotIn("update_termination_protection", cfn.calls)


class BucketInventory(unittest.TestCase):
    def test_counts_versions_and_bytes_per_prefix(self):
        s3 = FakeS3()
        report = teardown.inventory_bucket(s3, BUCKET)
        self.assertEqual(report["total"]["versions"], 4)
        self.assertEqual(report["byPrefix"]["runs/"]["versions"], 3)  # 2 versions + 1 delete marker
        self.assertEqual(report["byPrefix"]["reports/"]["versions"], 1)
        self.assertEqual(report["total"]["bytes"], 1024 * 2 + 2048)

    def test_batches_deletes_in_thousands(self):
        s3 = FakeS3(pages=[{"Versions": versions(2500), "DeleteMarkers": []}])
        removed = teardown.empty_bucket(s3, BUCKET)
        self.assertEqual(removed, 2500)
        self.assertEqual(len(s3.deleted), 2500)

    def test_a_delete_error_is_raised_not_ignored(self):
        class Failing(FakeS3):
            def delete_objects(self, Bucket: str, Delete: dict):
                return {"Errors": [{"Key": "runs/x", "Code": "AccessDenied", "Message": "nope"}]}

        with self.assertRaisesRegex(teardown.TeardownError, "AccessDenied"):
            teardown.empty_bucket(Failing(), BUCKET)


class NeverTouchesRegistries(unittest.TestCase):
    def test_no_registry_client_is_ever_created(self):
        cfn = FakeCloudFormation()
        s3 = FakeS3()

        class StrictSession(FakeSession):
            def client(self, name: str):
                if name not in {"cloudformation", "s3"}:
                    raise AssertionError(f"teardown must not talk to {name}")
                return super().client(name)

        teardown.main(["teardown.py", "--yes", "--delete-data"], session=StrictSession(cfn, s3))


def _options(**overrides) -> dict:
    options = {
        "stack_name": STACK,
        "region": None,
        "confirmed": False,
        "delete_data": False,
        "keep_reports": False,
        "help": False,
    }
    options.update(overrides)
    return options


if __name__ == "__main__":
    unittest.main()
