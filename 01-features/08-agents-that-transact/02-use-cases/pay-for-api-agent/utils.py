"""
utils.py — shared helpers for the pay-for-api notebook.

Small wrappers around boto3 for pretty-printing responses, assuming IAM
roles, polling for status transitions, and tolerating idempotent
create calls.
"""

import json
import time

import boto3
import botocore.exceptions


def pp(label: str, response: dict) -> None:
    """Pretty-print an API response, stripping ResponseMetadata."""
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(json.dumps(data, indent=2, default=str))


def assume_role(
    session: boto3.Session,
    role_arn: str,
    session_name: str = "tutorial-session",
) -> boto3.Session:
    """Assume an IAM role and return a boto3 Session with auto-refreshing credentials.

    Uses botocore's ``RefreshableCredentials`` under the hood so sessions
    stay valid past the default 1-hour STS expiry without the caller
    having to rebuild clients. This matters for the notebook, where a
    user can leave §5.1's session sitting for hours before coming back
    to §7 / §9.

    Immediately verifies the assumed identity by calling
    get_caller_identity(); raises if the assumption fails outright.
    """
    from botocore.credentials import RefreshableCredentials
    from botocore.session import Session as BotocoreSession

    sts = session.client("sts")

    def _refresh() -> dict:
        creds = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
        )["Credentials"]
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }

    refreshable_creds = RefreshableCredentials.create_from_metadata(
        metadata=_refresh(),
        refresh_using=_refresh,
        method="sts-assume-role",
    )

    botocore_session = BotocoreSession()
    botocore_session._credentials = refreshable_creds
    botocore_session.set_config_variable("region", session.region_name)

    new_session = boto3.Session(botocore_session=botocore_session)

    assumed_arn = new_session.client("sts").get_caller_identity()["Arn"]
    print(f"  Assumed: {assumed_arn}")
    return new_session


def wait_for_status(
    client_fn,
    expected_status: str,
    poll_interval: int = 5,
    timeout: int = 120,
    **kwargs,
) -> dict:
    """Poll a Get* API until the resource reaches expected_status.

    Resolves status from these response shapes (checked in order):
    - Top-level ``status`` field (Manager, Connector responses)
    - ``paymentInstrument.status`` (GetPaymentInstrument response)

    Raises TimeoutError if the resource has not reached expected_status
    within ``timeout`` seconds.
    Raises RuntimeError immediately if the resource enters a terminal
    failure state (any status ending in ``_FAILED``).
    """
    deadline = time.time() + timeout
    while True:
        resp = client_fn(**kwargs)
        status = resp.get("status") or resp.get("paymentInstrument", {}).get("status")
        print(f"   Status: {status}")
        if isinstance(status, str) and status.endswith("_FAILED"):
            raise RuntimeError(f"Resource reached failure state: '{status}'")
        if status == expected_status:
            return resp
        if time.time() >= deadline:
            raise TimeoutError(f"Resource still in '{status}' after {timeout}s — check the console for errors")
        time.sleep(poll_interval)


def idempotent_create(create_fn, conflict_msg: str = "Resource already exists", **kwargs) -> dict | None:
    """Call create_fn; handle ConflictException gracefully.

    Returns the API response on success, or None if the resource already exists.
    Re-raises any other ClientError.
    """
    try:
        return create_fn(**kwargs)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "ConflictException":
            print(f"  ⚠️  {conflict_msg} — skipping create")
            return None
        raise


def write_env_updates(updates: dict, env_path: str = ".env") -> None:
    """Upsert key=value pairs into a dotenv file, preserving other lines.

    Updates in-place — matching keys are replaced, new keys are appended,
    comments and blank lines are preserved. Values are written verbatim
    (no quoting), matching the existing .env style in this tutorial.

    Used only for non-secret values written by the notebook at runtime
    (USER_ID, role ARNs, manager IDs, instrument IDs, session IDs,
    wallet addresses). Wallet-provider secrets (Coinbase / Privy keys,
    Privy authorization private key) are pasted into ``.env`` by the
    user manually and never flow through this function. After §4 of
    the notebook calls ``CreatePaymentCredentialProvider``, AgentCore
    Identity stores those secrets in AWS Secrets Manager under KMS
    encryption and only the credential-provider ARN remains in
    ``.env`` for runtime use. The ``.env`` file itself is gitignored
    from use-case creation.
    """
    import pathlib

    path = pathlib.Path(env_path)
    existing = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in existing:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
