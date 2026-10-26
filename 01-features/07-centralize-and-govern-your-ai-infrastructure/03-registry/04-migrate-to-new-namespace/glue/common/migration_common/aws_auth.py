"""Credential/session provider for the migration jobs.

Wraps the two credential sources the engine uses -- the Glue execution role directly, or a
cross-account role assumed via STS -- behind one object that hands out a ready-to-use
``boto3.Session``. The registry clients build modeled ``boto3`` clients from this session
(see ``registry_client.py``), so all signing is handled by botocore rather than hand-rolled
SigV4.

Assumed-role sessions use botocore ``RefreshableCredentials`` so a single long-running job
transparently re-assumes the role before the temporary credentials expire; the ambient
execution-role session is already self-refreshing.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any

import boto3
from botocore.config import Config
from botocore.credentials import RefreshableCredentials
from botocore.session import Session as BotocoreSession

from .util import USER_AGENT_EXTRA


class AwsApiInvoker:
    """Provides a boto3 session backed by refreshable direct or assumed-role credentials."""

    def __init__(
        self,
        *,
        role_arn: str | None,
        external_id: str | None,
        session_name: str,
    ) -> None:
        self._role_arn = role_arn
        self._external_id = external_id
        self._session_name = _session_name(session_name)
        self._session: boto3.Session | None = None
        # Guards the one-time session build. The load stage shares an invoker across worker threads,
        # and building a session for an assumed role performs an AssumeRole -- so without this two
        # threads arriving together each issue one and one of the two sessions is then discarded.
        self._session_lock = threading.Lock()
        self._config = Config(
            retries={"max_attempts": 10, "mode": "adaptive"},
            user_agent_extra=USER_AGENT_EXTRA,
        )

    def session(self) -> boto3.Session:
        """Return a boto3 session, building it once and reusing it thereafter.

        For an assumed role the returned session carries refreshable credentials, so it stays
        valid for the whole job even if the run outlives a single STS session.
        """
        if self._session is not None:
            return self._session
        with self._session_lock:
            if self._session is None:
                self._session = self._build_session()
            return self._session

    def _build_session(self) -> boto3.Session:
        if not self._role_arn:
            # No role to assume: use the ambient Glue execution-role credentials, which boto3
            # refreshes on its own.
            return boto3.Session()

        sts = boto3.client("sts", config=self._config)

        def refresh() -> dict[str, Any]:
            request: dict[str, Any] = {
                "RoleArn": self._role_arn,
                "RoleSessionName": self._session_name,
            }
            if self._external_id:
                request["ExternalId"] = self._external_id
            credentials = sts.assume_role(**request)["Credentials"]
            expiration = credentials["Expiration"]
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=dt.timezone.utc)
            return {
                "access_key": credentials["AccessKeyId"],
                "secret_key": credentials["SecretAccessKey"],
                "token": credentials["SessionToken"],
                "expiry_time": expiration.astimezone(dt.timezone.utc).isoformat(),
            }

        # ``create_from_metadata`` seeds the credentials with one assume-role now (so an
        # un-assumable role fails fast) and re-invokes ``refresh`` automatically near expiry.
        refreshable = RefreshableCredentials.create_from_metadata(
            metadata=refresh(),
            refresh_using=refresh,
            method="sts-assume-role",
        )
        botocore_session = BotocoreSession()
        botocore_session._credentials = refreshable
        return boto3.Session(botocore_session=botocore_session)


def invoker_for_endpoint(
    endpoint: dict[str, Any],
    run_id: str | None,
    purpose: str,
) -> AwsApiInvoker:
    """Build an invoker for a mapping endpoint, assuming its ``roleArn`` when present.

    ``run_id`` is optional because not every caller has one: pre-flight validation and the
    ``target-config`` lookup are not part of a run. They used to pass their purpose in this slot and
    a literal in the next, which produced a usable session name by accident while reading as though
    the arguments were the wrong way round. Passing ``None`` leaves the run segment off instead.
    """
    return AwsApiInvoker(
        role_arn=endpoint.get("roleArn"),
        external_id=endpoint.get("externalId"),
        session_name="-".join(part for part in ("registry-migration", purpose, run_id) if part),
    )


def _session_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9+=,.@_-]", "-", value)
    return cleaned[:64] or "registry-migration"
