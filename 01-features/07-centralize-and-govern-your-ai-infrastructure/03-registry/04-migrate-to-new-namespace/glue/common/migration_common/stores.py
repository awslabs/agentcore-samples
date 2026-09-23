"""Pick the staging store for a run: an S3 bucket, or a local directory.

Kept in its own module so the two stores stay independent of each other and the jobs stay
independent of both: a job asks for "the store this run should use" and gets something with the
same methods either way.
"""

from __future__ import annotations

from typing import Any

from .local_store import LocalStore
from .settings import ConfigurationError, optional_argument, resolve_staging_bucket
from .storage import S3Store

LOCAL_DIRECTORY_ARGUMENT = "LOCAL_DIR"


def local_directory(arguments: dict[str, str]) -> str | None:
    """Return the local staging directory when one was requested, else ``None``."""
    return optional_argument(arguments, LOCAL_DIRECTORY_ARGUMENT)


def resolve_store(
    arguments: dict[str, str],
    settings: dict[str, Any],
    *,
    required: bool = True,
    boto3_module: Any = None,
) -> tuple[Any | None, str]:
    """Return ``(store, description)`` for this run.

    ``--local-dir`` selects a filesystem store and needs no bucket, no SSM and no deployment.
    Otherwise the S3 bucket is resolved as before -- explicit ``--staging-bucket`` first, then the
    one the deployment published. ``required=False`` lets configuration-only validation run before
    either exists.
    """
    directory = local_directory(arguments)
    if directory:
        bucket = optional_argument(arguments, "STAGING_BUCKET")
        if bucket:
            raise ConfigurationError(
                "Pass either --local-dir or --staging-bucket, not both: they name two different "
                f"places to stage the same run (--local-dir {directory}, --staging-bucket {bucket})."
            )
        store = LocalStore(directory)
        return store, f"local directory {store.location()}"

    bucket = resolve_staging_bucket(arguments, settings, required=required)
    if not bucket:
        return None, "no staging bucket"
    if boto3_module is None:
        # Imported on demand: a local run has no reason to pull in an AWS session for storage.
        # Callers that already hold the module pass it in, which is also the seam the tests use.
        import boto3 as boto3_module  # type: ignore[no-redef]

    return (
        S3Store.from_boto3(boto3_module, bucket, optional_argument(arguments, "REGION")),
        f"s3://{bucket}",
    )
