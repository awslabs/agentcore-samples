"""AgentCore Runtime benchmark helper.

A "compute unit" here is a runtimeSessionId. AgentCore provisions a dedicated
microVM per session, so:
  - the first request to a session is cold (fresh microVM),
  - reusing the same session id keeps hitting the same warm microVM.

setup(n) pre-generates n session ids; invoke(unit, i) fires against a given
session. There is nothing to provision up front (sessions are created lazily on
first invoke) and teardown stops the sessions to free their microVMs.

Prod only: the public regional endpoint, resolved by boto3 from `region`. An
explicit `endpoint_url` can still override it (e.g. a personal VPC endpoint),
but there is no separate stage switch.
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import boto3
from botocore.config import Config
from common import Acquired, Result, classify_error, find_cold


class AgentCoreHelper:
    def __init__(self, arn: str, region: str, qualifier: str, prompt: str,
                 concurrency: int, cold_field: str | None = None,
                 stop_on_teardown: bool = True,
                 endpoint_url: str | None = None):
        if not arn:
            raise ValueError("AGENTCORE_ARN is required")
        self.arn = arn
        self.qualifier = qualifier
        self.prompt = prompt
        self.cold_field = cold_field
        self.stop_on_teardown = stop_on_teardown
        self.units: list[str] = []
        # None keeps boto3's normal regional resolution (the public endpoint).
        self.endpoint_url = endpoint_url
        self.label = f"AgentCore ({arn.split('/')[-1]})"
        cfg = Config(
            region_name=region,
            max_pool_connections=max(concurrency * 2, 10),
            retries={"mode": "standard", "max_attempts": 1},
            connect_timeout=10,
            read_timeout=180,
        )
        # Low-level clients are thread-safe; share one across worker threads.
        # endpoint_url=None keeps boto3's normal regional resolution (prod).
        self.client = boto3.session.Session().client(
            "bedrock-agentcore", config=cfg, endpoint_url=self.endpoint_url)

    def setup(self, n: int) -> None:
        # A session id must be 33+ chars. Pre-generate n of them; the microVM is
        # provisioned lazily on the first invoke against each id.
        self.units = [f"bench-{uuid.uuid4().hex}{uuid.uuid4().hex}"[:48]
                      for _ in range(n)]

    def invoke(self, unit: str, index: int) -> Result:
        payload = json.dumps({"input": {"prompt": self.prompt}}).encode("utf-8")
        t0 = time.perf_counter()
        try:
            resp = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.arn,
                runtimeSessionId=unit,
                payload=payload,
                qualifier=self.qualifier,
            )
            body = resp["response"].read()
            latency = (time.perf_counter() - t0) * 1000.0
            cold = None
            try:
                cold = find_cold(json.loads(body), self.cold_field)
            except (ValueError, TypeError):
                pass
            return Result(index=index, ok=True, latency_ms=latency,
                          status=200, cold=cold, unit=unit)
        except Exception as exc:  # noqa: BLE001 - record any failure
            latency = (time.perf_counter() - t0) * 1000.0
            return Result(index=index, ok=False, latency_ms=latency,
                          unit=unit, error=f"{type(exc).__name__}: {exc}")

    def invoke_cold(self, index: int) -> Result:
        """Full cold lifecycle for one request: a brand-new session (fresh
        microVM) is used and then stopped. Latency includes provisioning, since
        AgentCore provisions the session's microVM inside the invoke."""
        sid = f"bench-{uuid.uuid4().hex}{uuid.uuid4().hex}"[:48]
        payload = json.dumps({"input": {"prompt": self.prompt}}).encode("utf-8")
        t0 = time.perf_counter()
        try:
            resp = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.arn,
                runtimeSessionId=sid,
                payload=payload,
                qualifier=self.qualifier,
            )
            resp["response"].read()
            latency = (time.perf_counter() - t0) * 1000.0
            return Result(index=index, ok=True, latency_ms=latency,
                          status=200, cold=True, unit=sid)
        except Exception as exc:  # noqa: BLE001
            latency = (time.perf_counter() - t0) * 1000.0
            return Result(index=index, ok=False, latency_ms=latency,
                          cold=True, unit=sid, error=f"{type(exc).__name__}: {exc}")
        finally:
            # Stop the session (terminate its microVM) so the next request can't
            # reuse it. Best-effort; not part of the measured latency.
            try:
                self.client.stop_runtime_session(
                    agentRuntimeArn=self.arn, runtimeSessionId=sid,
                    qualifier=self.qualifier)
            except Exception as exc:  # noqa: BLE001 - best-effort, not part of the measured latency
                print(f"[agentcore] stop_runtime_session({sid}) failed: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)

    def teardown(self) -> None:
        if not self.stop_on_teardown:
            return
        for unit in self.units:
            try:
                self.client.stop_runtime_session(
                    agentRuntimeArn=self.arn,
                    runtimeSessionId=unit,
                    qualifier=self.qualifier,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                print(f"[agentcore] teardown stop_runtime_session({unit}) failed: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)

    # -- ramp interface (see common.Acquired) -------------------------------- #
    # The ramp needs create-and-KEEP: sessions must accumulate to reach the
    # 5,000-active ceiling. invoke_cold() cannot express that — it stops the
    # session in its finally block.

    def acquire(self, index: int) -> Acquired:
        """Provision one session and leave it ACTIVE.

        AgentCore provisions the session's microVM inside the first invoke, so
        one cold invoke against a fresh id is both the provisioning and the
        measurement. No stop follows, so the session stays active (until the
        idle timeout) and counts against the concurrent-session ceiling.

        provision_ms stays None: there is no separate provisioning call to time,
        the cost is already inside result.latency_ms.

        The returned ``unit`` is None when no session can exist, so the ramp does
        not take ownership of it. That distinction matters at scale: a throttle
        is rejected at the front door before any session is created, and on a
        heavily-throttled leg thousands of such ids would otherwise each draw a
        pointless stop_runtime_session during teardown, every one of them
        failing and drowning the genuine release errors. Any OTHER failure
        (timeout, 5xx) is treated as owned — the session may well have been
        created before the response was lost, and an untracked session bills
        until its idle timeout.
        """
        sid = f"ramp-{uuid.uuid4().hex}{uuid.uuid4().hex}"[:48]
        res = self.invoke(sid, index)
        owned = res.ok or classify_error(res.error) not in ("throttle", "capacity")
        return Acquired(unit=sid if owned else None, result=res)

    # StopRuntimeSession quota: 200/s per agent, per account.
    release_rps = 200

    def release(self, unit: str) -> None:
        """Stop one session, freeing its microVM and its slot in the pool.

        Errors propagate: the ramp's teardown phase reports them, since a
        session that fails to stop keeps holding a slot until the idle timeout.
        """
        self.client.stop_runtime_session(
            agentRuntimeArn=self.arn, runtimeSessionId=unit,
            qualifier=self.qualifier)
