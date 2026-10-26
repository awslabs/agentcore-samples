# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U7 walkthrough: the end-to-end demonstration of the arrival-signal sample,
split into a deploy phase and a demo phase so the one-command path runs on a
cold account.

It composes the entry points of the other units and adds no new unit interface.

The demo needs several values that exist only after the deploy (the publisher
role ARNs from U1's CfnOutputs, the orchestrator's A2A endpoint URL and log
group, the peer's card URL). The two phases capture them so the operator never
passes them by hand:

  - `walkthrough.py deploy` runs the deploy sequence, captures every
    post-deploy value, and writes them to a values file (default
    walkthrough-env.json). The operator supplies only external inputs
    (registry id/arn, region, project dirs, peer name, search query).
  - `walkthrough.py demo` reads the values file and runs the push and pull
    demos, writing walkthrough-result.json.
  - `walkthrough.py run` does deploy then demo in one invocation, with no
    operator-supplied post-deploy args.

Deploy order: the orchestrator deploys first. Its runtime ARN, id, and role ARN
are injected as CDK context (-c orchestratorAgentRuntimeArn=...) into
`cdk deploy` of U1's infra, because U1's stack imports the orchestrator runtime
via Runtime.from_agent_runtime_attributes and reads those values from
app.node.try_get_context (app.py). Then register_orchestrator.py runs (standing
from t0), then the peer deploys.

PUSH demo: U4 publish+approve -> APPROVED EventBridge event -> U1 rule -> SQS
-> adapter -> U5 on_arrival_event -> A2A greet. PULL demo: U6 search_and_invoke
-> the same greet via active discovery.

Composition, not a saga. Step failures raise WalkthroughError with no silent
continue, and the operator fixes the cause and re-runs. There is no
compensation or rollback. Each unit's own reliability (U5 two-phase dedupe, U4
fail-fast, SQS retry/DLQ) governs, and the teardown script cleans up.

The values file carries only non-secret identifiers (ARNs, URLs, ids, names),
never credentials. It is account-specific and gitignored.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile

logger = logging.getLogger("walkthrough")
logging.basicConfig(level=logging.INFO, format="%(message)s")

_DEFAULT_VALUES_FILE = "walkthrough-env.json"
_DEFAULT_RESULT_FILE = "walkthrough-result.json"


def _log(step: str, **fields: object) -> None:
    logger.info(json.dumps({"step": step, **fields}))


class WalkthroughError(RuntimeError):
    """A composed step failed. The walkthrough stops (no silent continue, no
    rollback) and the operator re-runs after fixing the cause."""


def _run(cmd: list[str], *, cwd: str | None = None) -> str:
    """Run a composed sub-step (agentcore / cdk / a unit script), streaming its
    identity to the log. A non-zero exit raises WalkthroughError. Returns
    captured stdout."""
    _log("run", cmd=cmd, cwd=cwd)
    # Audited-safe: `cmd` is always a static list built in this module (e.g.
    # ["agentcore","deploy","-y"], ["cdk","deploy",...]), never a shell string
    # or attacker-controlled input, and shell=False (list form). There is no
    # command-injection surface here.
    proc = subprocess.run(  # nosec B603 # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
        cmd, cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        _log("run_failed", cmd=cmd, returncode=proc.returncode, stderr=proc.stderr[-2000:])
        raise WalkthroughError(
            f"step failed (exit {proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


# Deploy phase (two passes with CDK-context injection) -------------


def _set_orchestrator_env_vars(project_dir: str, env: dict[str, str]) -> None:
    """Write deploy-time env vars into the orchestrator's agentcore.json
    runtimes[].envVars before `agentcore deploy`.

    The AgentCore CLI has no --env flag. Env vars are declarative config in
    agentcore.json (AgentEnvSpec.envVars). These values are deployment-specific
    (the registry id, and the idempotency table name derived from it), so the
    walkthrough templates them here rather than committing a fixed value.
    Merge by name so re-running updates in place without duplicating.
    """
    config_path = os.path.join(project_dir, "agentcore", "agentcore.json")
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    runtimes = config.get("runtimes") or []
    if not runtimes:
        raise WalkthroughError(f"no runtimes[] in {config_path} to set envVars on")
    existing = {e["name"]: e for e in runtimes[0].get("envVars", []) or []}
    for name, value in env.items():
        existing[name] = {"name": name, "value": value}
    runtimes[0]["envVars"] = list(existing.values())
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    _log("orchestrator_env_set", configPath=config_path, envVars=sorted(env))


def deploy_orchestrator(project_dir: str, region: str, registry_id: str) -> dict:
    """(a) `agentcore deploy` the U2 orchestrator, then (b) capture its
    runtime ARN, id, role ARN, A2A endpoint URL, and CloudWatch log group from
    `agentcore status --json`.

    Returns {"arn", "id", "roleArn", "a2aUrl", "logGroup"}. The arn, id, and
    roleArn are injected as CDK context for U1's deploy. a2aUrl and logGroup
    are captured so the demo phase needs no operator-supplied post-deploy
    args."""
    # Wire the orchestrator's deploy-time env vars before deploying. The
    # event path reads IDEMPOTENCY_TABLE_NAME (the dedupe table, named
    # deterministically from the registry id so it matches the table the infra
    # stack creates). AGENT_REGISTRY_ID configures the in-runtime search tool.
    from agent_registry_sample.naming import idempotency_table_name

    _set_orchestrator_env_vars(
        project_dir,
        {
            "IDEMPOTENCY_TABLE_NAME": idempotency_table_name(registry_id),
            "AGENT_REGISTRY_ID": registry_id,
        },
    )
    # -y is non-interactive mode (auto-confirm prompts, read credentials from env).
    # `agentcore deploy` refuses to run without it under a captured-output subprocess.
    _run(["agentcore", "deploy", "-y"], cwd=project_dir)
    status_json = _run(["agentcore", "status", "--json"], cwd=project_dir)
    status = json.loads(status_json)
    runtime = _extract_orchestrator_runtime(status)
    _log(
        "orchestrator_deployed",
        arn=runtime["arn"],
        id=runtime["id"],
        a2aUrl=runtime["a2aUrl"],
        logGroup=runtime["logGroup"],
    )
    return runtime


def _runtime_record(status: dict, agent_name: str) -> dict:
    """Return {arn, id, roleArn, invocationUrl} for a deployed agent from
    `agentcore status --json`.

    Schema as of @aws/agentcore 0.31.1:
      - runtimeArn / runtimeId / roleArn live under
        deployedState.targets.<target>.resources.runtimes.<agentName>.
      - the invocation URL lives in the top-level resources[] entry for the
        agent (key "invocationUrl"). It is the bedrock-agentcore
        InvokeAgentRuntime HTTP binding (SigV4-gated), not a plain public A2A
        host. A2A reachability goes through this invocations endpoint, which
        determines how the card is fetched and how the peer is called.
    """
    invocation_url = None
    for r in status.get("resources", []) or []:
        if r.get("name") == agent_name and r.get("resourceType") == "agent":
            invocation_url = r.get("invocationUrl")
            break
    rt = None
    for tgt in ((status.get("deployedState") or {}).get("targets") or {}).values():
        runtimes = (tgt.get("resources") or {}).get("runtimes") or {}
        if agent_name in runtimes:
            rt = runtimes[agent_name]
            break
    if not rt or not invocation_url:
        raise WalkthroughError(
            f"could not resolve the deployed '{agent_name}' runtime from "
            "`agentcore status --json` (looked under "
            "deployedState.targets.*.resources.runtimes and "
            "resources[].invocationUrl); inspect the status output and wire the "
            "values into the values file manually (all are non-secret identifiers)."
        )
    return {
        "arn": rt.get("runtimeArn"),
        "id": rt.get("runtimeId"),
        "roleArn": rt.get("roleArn"),
        "invocationUrl": invocation_url,
    }


def _extract_orchestrator_runtime(status: dict) -> dict:
    """Orchestrator runtime fields for the values file. a2aUrl is the
    invocation URL (the reachable endpoint). logGroup is derived by convention
    from the runtimeId because status output does not include it (check the
    console if the push-proof poll misses)."""
    rec = _runtime_record(status, "orchestrator")
    return {
        "arn": rec["arn"],
        "id": rec["id"],
        "roleArn": rec["roleArn"],
        "a2aUrl": rec["invocationUrl"],
        # The AgentCore runtime log group carries a "-DEFAULT" suffix for the
        # DEFAULT endpoint, as in /aws/bedrock-agentcore/runtimes/<id>-DEFAULT.
        # Without the suffix the push-proof poll gets ResourceNotFoundException.
        "logGroup": f"/aws/bedrock-agentcore/runtimes/{rec['id']}-DEFAULT",
    }


def deploy_infra(repo_root: str, runtime: dict, registry_arn: str) -> dict:
    """(c) `cdk deploy` U1 infra, injecting the orchestrator runtime attributes
    and the registry ARN as CDK context, and capturing the stack's CfnOutputs
    via `--outputs-file`.

    Returns {"submitterRoleArn", "approverRoleArn"} read from the outputs file
    (PublisherSubmitterRoleArn / PublisherApproverRoleArn), so the operator does
    not pass the role ARNs as args.

    `cdk deploy --outputs-file <tmp>.json` writes
    {<StackName>: {<OutputKey>: <value>}} to a temp file that is then read. This
    is used instead of a separate `cloudformation describe-stacks` call because
    it needs no extra IAM and no stack-name guessing, and the same deploy that
    creates the outputs writes the file."""
    fd, outputs_path = tempfile.mkstemp(prefix="cdk-outputs-", suffix=".json")
    os.close(fd)
    try:
        _run(
            [
                "cdk",
                "deploy",
                "--require-approval",
                "never",
                "--outputs-file",
                outputs_path,
                "-c",
                f"orchestratorAgentRuntimeArn={runtime['arn']}",
                "-c",
                f"orchestratorAgentRuntimeId={runtime['id']}",
                "-c",
                f"orchestratorAgentRuntimeRoleArn={runtime['roleArn']}",
                "-c",
                f"registryArn={registry_arn}",
            ],
            cwd=repo_root,
        )
        publisher = _extract_publisher_roles(outputs_path)
    finally:
        try:
            os.remove(outputs_path)
        except OSError:
            pass
    _log(
        "infra_deployed",
        submitterRoleArn=publisher["submitterRoleArn"],
        approverRoleArn=publisher["approverRoleArn"],
    )
    return publisher


def _extract_publisher_roles(outputs_path: str) -> dict:
    """Read PublisherSubmitterRoleArn / PublisherApproverRoleArn from the CDK
    `--outputs-file` JSON ({<StackName>: {<OutputKey>: <value>}}). Searches
    every stack (one is expected) rather than assuming the stack name."""
    with open(outputs_path, encoding="utf-8") as handle:
        outputs = json.load(handle)
    submitter = approver = None
    for stack_outputs in outputs.values():
        if not isinstance(stack_outputs, dict):
            continue
        submitter = submitter or stack_outputs.get("PublisherSubmitterRoleArn")
        approver = approver or stack_outputs.get("PublisherApproverRoleArn")
    if not submitter or not approver:
        raise WalkthroughError(
            "cdk outputs file did not carry PublisherSubmitterRoleArn / "
            f"PublisherApproverRoleArn (got keys: {list(outputs)}); the U1 stack "
            "must export both (see infrastructure/stack.py _create_outputs)."
        )
    return {"submitterRoleArn": submitter, "approverRoleArn": approver}


def register_orchestrator_record(registry_id: str, a2a_endpoint_url: str, region: str) -> str:
    """(d) run U2's register_orchestrator.py so the orchestrator is standing
    and discoverable from t0. Imports U2's entry point rather than
    re-implementing it."""
    import boto3

    from agent_registry_sample.orchestrator.register_orchestrator import register_orchestrator

    control_client = boto3.client("agent-registry-control", region_name=region)
    record_id = register_orchestrator(control_client, registry_id, a2a_endpoint_url)
    _log("orchestrator_registered", recordId=record_id)
    return record_id


def deploy_peer(peer_project_dir: str) -> dict:
    """(e) `agentcore deploy` the U3 peer, then capture its invocation URL AND
    its runtime ARN from `agentcore status --json`.

    Returns {"cardUrl", "arn"}. The cardUrl means the demo phase needs no
    --peer-card-url operator arg. The arn lets the deploy phase grant the
    consumer-group role invoke access on the peer (grant_consumer_access)."""
    # -y is non-interactive mode (same as the orchestrator deploy above).
    _run(["agentcore", "deploy", "-y"], cwd=peer_project_dir)
    status_json = _run(["agentcore", "status", "--json"], cwd=peer_project_dir)
    status = json.loads(status_json)
    rec = _runtime_record(status, "peer")
    _log("peer_deployed", cardUrl=rec["invocationUrl"], arn=rec["arn"])
    return {"cardUrl": rec["invocationUrl"], "arn": rec["arn"]}


def _operator_arn(region: str) -> str:
    """The operator's own principal ARN (who runs the walkthrough locally),
    added to the consumer-group set so the local pull demo can invoke the peer
    past the DenyOtherPrincipals boundary. It is a separate module function so
    unit tests can stub it without a live STS call. For an IAM user this equals
    aws:PrincipalArn. If the operator runs as an assumed role, the group entry
    should be the role ARN instead (documented in SECURITY.md)."""
    import boto3

    return boto3.client("sts", region_name=region).get_caller_identity()["Arn"]


def grant_consumer_access(
    peer_runtime_arn: str,
    consumer_role_arn: str,
    region: str,
    demo_operator_arn: str | None = None,
) -> dict:
    """(f) The newcomer opts into the consumer group by attaching resource-based
    policies that authorize the shared consumer-group role (here the
    orchestrator's execution role) to invoke it. This is the sample's
    invoke-authorization model.

    The caller side stays silent. The orchestrator's own identity policy grants
    no bedrock-agentcore:InvokeAgentRuntime. Instead each newcomer, at its own
    deploy time (when its own ARN is known), grants the consumer role on both
    its runtime and its DEFAULT endpoint.

    InvokeAgentRuntime is authorized hierarchically against the runtime and the
    endpoint resource. With the caller's identity policy silent, each resource
    must carry its own resource-based allow. A runtime-only grant is a silent
    deny (runtime-only gives AccessDenied, runtime plus DEFAULT endpoint gives
    200 OK). The DEFAULT endpoint ARN is deterministic
    ({runtimeArn}/runtime-endpoint/DEFAULT), so no ListAgentRuntimeEndpoints
    permission is needed.

    Same-account authorization with a silent identity policy and a resource
    allow is ALLOWED. No statement uses a wildcard resource. The registry
    approval gate and the orchestrator's in-code account check form the other
    layer of defense in depth.

    The consumer group is a named set of principals. In production the set is
    the orchestrator execution role(s). The walkthrough also runs the pull demo
    locally under the operator's own identity (a convenience. In production,
    pull runs inside the orchestrator as the orchestrator role), so
    demo_operator_arn is added to the set for the demo to pass the
    DenyOtherPrincipals boundary. Both the allow and the deny exception cover
    the whole set, so only named members can invoke and every other principal
    is denied."""
    import boto3

    endpoint_arn = f"{peer_runtime_arn}/runtime-endpoint/DEFAULT"
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    # The consumer group. The orchestrator role is always a member, and the demo
    # operator joins only when running the local pull demo (documented as demo-only in SECURITY.md).
    group_principals = [consumer_role_arn]
    if demo_operator_arn and demo_operator_arn != consumer_role_arn:
        group_principals.append(demo_operator_arn)

    def _put(resource_arn: str) -> None:
        # The Resource field must be the exact ARN the policy is attached to
        # (AgentCore rejects "Resource": "*" on a resource-based policy).
        # Two statements:
        #   1. Allow the consumer-group principals (the set).
        #   2. Deny every other principal. Without the deny, any other
        #      same-account principal holding an identity-based
        #      InvokeAgentRuntime allow could still invoke (IAM allow plus a
        #      silent resource policy is Allowed). The deny enforces the
        #      boundary, per AWS's "restrict SigV4 invocation" pattern.
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowConsumerGroupInvoke",
                    "Effect": "Allow",
                    "Principal": {"AWS": group_principals},
                    "Action": "bedrock-agentcore:InvokeAgentRuntime",
                    "Resource": resource_arn,
                },
                {
                    "Sid": "DenyOtherPrincipals",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "bedrock-agentcore:InvokeAgentRuntime",
                    "Resource": resource_arn,
                    "Condition": {
                        # Deny unless the caller is one of the consumer-group
                        # members. ArnNotEquals with a list is satisfied only
                        # when PrincipalArn matches none of them, so the deny
                        # applies to every principal outside the set.
                        "ArnNotEquals": {"aws:PrincipalArn": group_principals}
                    },
                },
            ],
        }
        control.put_resource_policy(resourceArn=resource_arn, policy=json.dumps(policy))

    _put(peer_runtime_arn)
    _put(endpoint_arn)
    _log(
        "consumer_access_granted",
        runtime=peer_runtime_arn,
        endpoint=endpoint_arn,
        consumerRole=consumer_role_arn,
    )
    return {"runtimeArn": peer_runtime_arn, "endpointArn": endpoint_arn}


def run_deploy(args) -> dict:
    """The full deploy sequence. Returns the complete values dict (also
    written to the values file by the `deploy`/`run` entry points)."""
    runtime = deploy_orchestrator(args.orchestrator_project_dir, args.region, args.registry_id)
    publisher = deploy_infra(args.repo_root, runtime, args.registry_arn)
    record_id = register_orchestrator_record(args.registry_id, runtime["a2aUrl"], args.region)
    peer = deploy_peer(args.peer_project_dir)
    # (f) the peer opts into the consumer group: grant the orchestrator's
    # execution role invoke access on the peer's runtime + DEFAULT endpoint.
    # The orchestrator's identity policy stays silent on invoke.
    # The local pull demo invokes as the operator's own identity, so the
    # operator is added to the consumer-group set for the demo (in production
    # the group is the orchestrator role, see SECURITY.md). Everyone else
    # stays denied.
    grant = grant_consumer_access(
        peer["arn"],
        runtime["roleArn"],
        args.region,
        demo_operator_arn=_operator_arn(args.region),
    )

    return {
        "registryId": args.registry_id,
        "registryArn": args.registry_arn,
        "region": args.region,
        "repoRoot": args.repo_root,
        "orchestratorProjectDir": args.orchestrator_project_dir,
        "peerProjectDir": args.peer_project_dir,
        "orchestrator": {
            "arn": runtime["arn"],
            "id": runtime["id"],
            "roleArn": runtime["roleArn"],
            "a2aUrl": runtime["a2aUrl"],
            "logGroup": runtime["logGroup"],
            "recordId": record_id,
        },
        "peer": {
            "cardUrl": peer["cardUrl"],
            "name": args.peer_name,
            "arn": peer["arn"],
            "endpointArn": grant["endpointArn"],
        },
        "publisher": {
            "submitterRoleArn": publisher["submitterRoleArn"],
            "approverRoleArn": publisher["approverRoleArn"],
        },
        "searchQuery": args.search_query,
    }


# Demo phase: PUSH ----------------------------------------------------------


def run_push_demo(
    *,
    registry_id: str,
    peer_name: str,
    submitter_role_arn: str,
    approver_role_arn: str,
    peer_card_url: str,
    region: str,
    orchestrator_log_group: str,
) -> dict:
    """U4 publish+approve -> APPROVED event -> ... -> U5 on_arrival_event ->
    A2A greet. Calls U4's publish_and_approve() entry point, then reads the
    greet proof from the orchestrator runtime's log tail.

    Returns {"recordId", "greetProof"}. The greet proof is the "hello {name}"
    text the consumer path produced (surfaced via the orchestrator's
    structured log line {"step":"completed","greet":"hello ..."})."""
    # Re-run idempotency: the peer record name is deterministic
    # (peer_name, default 'peer-greeter'), so a prior run leaves a record that
    # collides at create_registry_record. The orchestrator is standing and
    # reused (see register_orchestrator), but the peer is the newcomer whose
    # fresh DRAFT->PENDING->APPROVED transition fires the push arrival event
    # under test, so it is not reused. Delete any prior record, then publish
    # fresh. Only the registry record is removed. The peer runtime (agentcore
    # deploy) is untouched, and the fresh record points at the same live card
    # URL. The sample's tolerant deprecate-then-delete lets an already-gone
    # record converge cleanly.
    import boto3

    from agent_registry_sample.publisher.card_source import load_peer_card_json
    from agent_registry_sample.publisher.publish_and_approve import publish_and_approve
    from agent_registry_sample.sdk_reconciliation.reconciliation import find_record_id_by_name
    from agent_registry_sample.walkthrough.teardown import deprecate_then_delete_record

    control_client = boto3.client("agent-registry-control", region_name=region)
    prior_id = find_record_id_by_name(control_client, registry_id, peer_name)
    if prior_id is not None:
        _log("peer_record_preexisting", recordId=prior_id, name=peer_name)
        deprecate_then_delete_record(control_client, registry_id, prior_id)

    card_json = load_peer_card_json(url=peer_card_url, file_path=None)
    record_id = publish_and_approve(
        registry_id=registry_id,
        name=peer_name,
        card_json=card_json,
        submitter_role_arn=submitter_role_arn,
        approver_role_arn=approver_role_arn,
        status_reason="Approved by the arrival-signal sample walkthrough (push demo).",
        region=region,
    )
    _log("push_published_approved", recordId=record_id)

    # The Approved event -> SQS -> adapter -> orchestrator is asynchronous.
    # Surface the proof by tailing the orchestrator runtime log for the
    # "completed" greet line (polled, and the operator sees it live via
    # `agentcore logs` too). This reads the log the consumer path emits rather
    # than re-implementing the path.
    greet = _poll_orchestrator_log_for_greet(orchestrator_log_group, record_id, region)
    _log("push_greet_proof", recordId=record_id, greet=greet)
    return {"recordId": record_id, "greetProof": greet}


def _poll_orchestrator_log_for_greet(log_group: str, record_id: str, region: str) -> str | None:
    """Poll CloudWatch Logs for the orchestrator's {"step":"completed",
    "recordId":...,"greet":"hello ..."} line produced by U5's event path.
    Returns the greet text, or None if not observed within the poll window
    (the operator then inspects `agentcore logs` / the DLQ directly)."""
    import time

    import boto3

    logs = boto3.client("logs", region_name=region)
    poll_start_ms = int((time.time() - 300) * 1000)  # look back 5 min to catch an early completion
    deadline = time.time() + 180  # 3-minute poll window for the async push path
    while time.time() < deadline:
        try:
            # AgentCore/OTel prefixes each log line with a timestamp, logger, and
            # trace context before the JSON payload. A CloudWatch JSON metric
            # filter ({ $.step = "completed" }) never matches such a line, and a
            # quoted-string filterPattern is brittle to escape. So pull by time
            # window with no server-side filter and match in Python. Each
            # message is sliced from its first "{" and parsed.
            resp = logs.filter_log_events(
                logGroupName=log_group,
                startTime=poll_start_ms,
            )
        except Exception as exc:  # noqa: BLE001 - log group may not exist yet
            _log("push_poll_wait", reason=type(exc).__name__)
            time.sleep(10)
            continue
        for event in resp.get("events", []):
            message = event.get("message", "")
            brace = message.find("{")
            if brace == -1:
                continue
            try:
                payload = json.loads(message[brace:])
            except ValueError:
                continue
            if payload.get("recordId") == record_id and payload.get("greet"):
                return payload["greet"]
        time.sleep(10)
    return None


# Demo phase: PULL ----------------------------------------------------------


def run_pull_demo(*, query: str, registry_id: str, region: str) -> str | None:
    """U6 search_and_invoke -> the same greet proof via active discovery.

    Calls U6's search path. Returns the greet text directly (the pull path is
    synchronous from the caller's view, so the result is the proof), or None
    on the clean "no peer found" empty result."""
    from agent_registry_sample.orchestrator.runtime_wiring import (
        build_search_path_collaborators,
    )
    from agent_registry_sample.orchestrator.search_path import search_and_invoke_impl

    # The pull demo runs search_and_invoke's collaborators with the same wiring
    # the deployed tool uses. AGENT_REGISTRY_ID, PEER_ALLOWED_ACCOUNTS, and region
    # come from the environment the operator exports before the walkthrough.
    os.environ.setdefault("AGENT_REGISTRY_ID", registry_id)
    collaborators = build_search_path_collaborators()
    greet = search_and_invoke_impl(query, **collaborators)
    _log("pull_greet_proof", query=query, greet=greet)
    return greet


def run_demo(values: dict, result_file: str) -> dict:
    """Run push + pull from a captured values dict. Writes result_file."""
    orch = values["orchestrator"]
    push = run_push_demo(
        registry_id=values["registryId"],
        peer_name=values["peer"]["name"],
        submitter_role_arn=values["publisher"]["submitterRoleArn"],
        approver_role_arn=values["publisher"]["approverRoleArn"],
        peer_card_url=values["peer"]["cardUrl"],
        region=values["region"],
        orchestrator_log_group=orch["logGroup"],
    )
    pull_greet = run_pull_demo(
        query=values.get("searchQuery", "greeter"),
        registry_id=values["registryId"],
        region=values["region"],
    )

    result = {
        "pushProof": push["greetProof"],
        "pushRecordId": push["recordId"],
        "pullProof": pull_greet,
        "bothPathsProven": bool(push["greetProof"]) and bool(pull_greet),
    }
    with open(result_file, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    _log("walkthrough_complete", **result, resultFile=result_file)
    print(json.dumps(result, indent=2))
    return result


# Values file ---------------------------------------------------------------

# The required shape of the values file. Used to fail cleanly (without a
# traceback) when `demo` runs against a missing or incomplete file.
_REQUIRED_VALUES = {
    "registryId": [],
    "region": [],
    "orchestrator": ["logGroup"],
    "peer": ["cardUrl", "name"],
    "publisher": ["submitterRoleArn", "approverRoleArn"],
}


def _write_values(values: dict, values_file: str) -> None:
    with open(values_file, "w", encoding="utf-8") as handle:
        json.dump(values, handle, indent=2)
    _log("values_written", valuesFile=values_file)


def _load_values(values_file: str) -> dict:
    """Read and validate the values file. Raises WalkthroughError with an
    operator-actionable message (instead of a traceback) when it is missing or
    incomplete."""
    if not os.path.exists(values_file):
        raise WalkthroughError(
            f"run `walkthrough.py deploy` first; values file {values_file!r} not found."
        )
    try:
        with open(values_file, encoding="utf-8") as handle:
            values = json.load(handle)
    except (OSError, ValueError) as exc:
        raise WalkthroughError(
            f"values file {values_file!r} could not be read ({exc}); re-run "
            "`walkthrough.py deploy` to regenerate it."
        ) from exc
    missing = _missing_value_keys(values)
    if missing:
        raise WalkthroughError(
            f"values file {values_file!r} is incomplete (missing: {', '.join(missing)}); "
            "re-run `walkthrough.py deploy` to regenerate it."
        )
    return values


def _missing_value_keys(values: dict) -> list[str]:
    missing: list[str] = []
    for top, subkeys in _REQUIRED_VALUES.items():
        if not subkeys:
            if not values.get(top):
                missing.append(top)
            continue
        section = values.get(top)
        if not isinstance(section, dict):
            # Whole section absent: name each required sub-key so the operator
            # sees what is missing (e.g. publisher.submitterRoleArn).
            missing.extend(f"{top}.{sub}" for sub in subkeys)
            continue
        for sub in subkeys:
            if not section.get(sub):
                missing.append(f"{top}.{sub}")
    return missing


# Entry point: three subcommands on one script ------------------------------


def _add_deploy_inputs(parser: argparse.ArgumentParser) -> None:
    """The operator-supplied inputs, all known before any deploy. The five
    post-deploy values (role ARNs, orchestrator a2aUrl/logGroup, peer cardUrl)
    are captured by the deploy phase."""
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--registry-arn", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--orchestrator-project-dir", default="orchestrator-host")
    parser.add_argument("--peer-project-dir", default="peer-host")
    parser.add_argument("--peer-name", default="peer-greeter")
    parser.add_argument("--search-query", default="greeter")
    parser.add_argument("--values-file", default=_DEFAULT_VALUES_FILE)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="End-to-end walkthrough (deploy + prove both greet paths)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_deploy = sub.add_parser("deploy", help="Deploy and capture values to the values file.")
    _add_deploy_inputs(p_deploy)

    p_demo = sub.add_parser("demo", help="Read the values file and prove both greet paths.")
    p_demo.add_argument("--values-file", default=_DEFAULT_VALUES_FILE)
    p_demo.add_argument("--result-file", default=_DEFAULT_RESULT_FILE)

    p_run = sub.add_parser("run", help="Deploy then demo in one command (the honest path).")
    _add_deploy_inputs(p_run)
    p_run.add_argument("--result-file", default=_DEFAULT_RESULT_FILE)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "deploy":
            values = run_deploy(args)
            _write_values(values, args.values_file)
            print(f"Deploy complete. Values written to {args.values_file}")
            return 0

        if args.command == "demo":
            values = _load_values(args.values_file)
            result = run_demo(values, args.result_file)
            return 0 if result["bothPathsProven"] else 1

        values = run_deploy(args)
        _write_values(values, args.values_file)
        result = run_demo(values, args.result_file)
        return 0 if result["bothPathsProven"] else 1
    except WalkthroughError as exc:
        # Operator-actionable exit instead of a traceback.
        _log("walkthrough_failed", error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
