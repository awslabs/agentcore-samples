"""Shared plumbing for the deploy scripts: .env state, Okta admin client, guards.

Every script reads and writes the same .env, so the chain is resumable and each
step is idempotent. Shell exports win over .env values, which makes one-off
overrides easy without editing the file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SAMPLE_ROOT = Path(__file__).resolve().parent.parent

# CONSENT_ENV_FILE-style override so two tenants can be driven side by side.
_override = os.environ.get("XAA_ENV_FILE", "").strip()
ENV_PATH = (
    (Path(_override) if Path(_override).is_absolute() else SAMPLE_ROOT / _override)
    if _override
    else SAMPLE_ROOT / ".env"
)

# First boto3 whose bedrock-agentcore-control model carries the gateway
# interceptor and policy-engine operations this sample needs.
MIN_BOTO3 = (1, 43, 91)


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env() -> None:
    """Load .env into os.environ without clobbering real shell exports."""
    if not ENV_PATH.exists():
        die(f"{ENV_PATH.name} not found. Run: cp config.example.env .env")
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def save_env(**updates: str) -> None:
    """Rewrite keys in place, preserving comments, ordering and blank lines.

    Keys already present are edited where they sit; genuinely new keys are
    appended. Writing in place is what keeps the annotated template readable
    after a dozen script runs.
    """
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(raw)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    # .env holds client secrets and an Okta admin token in clear text. That is
    # unavoidable here -- the deploy chain has to persist state between steps -- so
    # restrict it to the owner rather than leaving it world-readable. The README says
    # plainly that this is a sandbox pattern, not a production one.
    ENV_PATH.chmod(0o600)
    for key, value in updates.items():
        os.environ[key] = value


def must_env(key: str, hint: str = "") -> str:
    value = os.environ.get(key, "").strip()
    if not value or value.startswith("<"):
        die(f"{key} is not set in {ENV_PATH.name}." + (f" {hint}" if hint else ""))
    return value


def env(key: str, default: str = "") -> str:
    """Like os.environ.get but treats an empty-but-present key as absent.

    A key left blank in the template would otherwise defeat `get(key, default)`
    and yield "" instead of the intended default.
    """
    return os.environ.get(key, "").strip() or default


def check_boto3() -> None:
    try:
        import boto3
    except ImportError:
        die("boto3 is not installed. Run: pip install -r requirements.txt")
    parts = tuple(int(p) for p in boto3.__version__.split(".")[:3] if p.isdigit())
    if parts < MIN_BOTO3:
        want = ".".join(str(p) for p in MIN_BOTO3)
        die(
            f"boto3 {boto3.__version__} is too old; need >= {want}.\n"
            "Older models lack the gateway interceptor and policy-engine shapes, "
            "which fails as a confusing AttributeError rather than a clear error."
        )


def okta_org_url() -> str:
    """The app-facing Okta host, normalised.

    Accepts a pasted admin host or trailing slash. OIDC discovery and the token
    endpoint are only served from the app-facing host, and this value doubles as
    the ORG authorization server -- the only server that mints an ID-JAG.
    """
    raw = must_env("OKTA_ORG_URL", "e.g. https://your-tenant.okta.com").strip()
    host = raw.removeprefix("https://").removeprefix("http://").rstrip("/")
    if "-admin." in host:
        host = host.replace("-admin.", ".")
    if "/oauth2" in host:
        host = host.split("/oauth2", 1)[0]
    return f"https://{host}"


def discovery_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


class OktaAdmin:
    """Minimal Okta Management API client (SSWS token, stdlib only).

    Setup-time only. Nothing in the runtime path uses the admin token.
    """

    def __init__(self, org_url: str, token: str) -> None:
        self.org = org_url.rstrip("/")
        self.base = f"{self.org}/api/v1"
        self._token = token

    def _call(self, method: str, path: str, body: dict | None = None):
        url = path if path.startswith("http") else f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"SSWS {self._token}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:500]
            if exc.code == 401:
                die(
                    "Okta returned 401 for the admin token (E0000011 territory).\n"
                    "Okta expires API tokens after 30 days of inactivity -- mint a "
                    "new one at Security -> API -> Tokens and update OKTA_API_TOKEN."
                )
            raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, body: dict):
        return self._call("POST", path, body)

    def put(self, path: str, body: dict):
        return self._call("PUT", path, body)

    def delete(self, path: str):
        return self._call("DELETE", path)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


# ── Derived resource names (override in .env only if you must) ───────────────


def gateway_name() -> str:
    return env("GATEWAY_NAME", "xaa-todo-gw")


def gateway_role_name() -> str:
    return env("GATEWAY_ROLE_NAME", "XaaTodoGatewayRole")


def interceptor_name() -> str:
    return env("INTERCEPTOR_LAMBDA_NAME", "xaa-todo-idjag-interceptor")


def interceptor_role_name() -> str:
    return env("INTERCEPTOR_ROLE_NAME", "XaaTodoInterceptorRole")


def resource_lambda_name() -> str:
    return env("RESOURCE_LAMBDA_NAME", "xaa-todo-resource")


def resource_role_name() -> str:
    return env("RESOURCE_ROLE_NAME", "XaaTodoResourceRole")


def obo_provider_name() -> str:
    return env("AGENT_OBO_PROVIDER_NAME", "xaa-agent-obo-provider")


def policy_engine_name() -> str:
    # Policy engine AND policy names allow NO hyphens: the API enforces
    # ^[A-Za-z][A-Za-z0-9_]*$ (max 48). Gateway and target names do allow them, which
    # makes this easy to get wrong.
    return env("POLICY_ENGINE_NAME", "xaaTodoPolicies")


# ── AWS helpers shared by the deploy scripts ─────────────────────────────────


def region() -> str:
    return env("AWS_REGION", "us-west-2")


def clients() -> dict:
    import boto3

    r = region()
    return {
        "iam": boto3.client("iam", region_name=r),
        "lam": boto3.client("lambda", region_name=r),
        "apigw": boto3.client("apigatewayv2", region_name=r),
        "acc": boto3.client("bedrock-agentcore-control", region_name=r),
        "sm": boto3.client("secretsmanager", region_name=r),
        "logs": boto3.client("logs", region_name=r),
        "sts": boto3.client("sts", region_name=r),
    }


def account_id() -> str:
    import boto3

    return boto3.client("sts", region_name=region()).get_caller_identity()["Account"]


def zip_files(paths: dict[str, Path]) -> bytes:
    """Zip {name_in_archive: source_path} with predictable permissions."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, src in paths.items():
            info = zipfile.ZipInfo(arcname)
            info.external_attr = 0o644 << 16
            z.writestr(info, src.read_text())
    return buf.getvalue()


def ensure_role(iam, name: str, service: str, inline: dict | None, managed: str | None) -> str:
    """Create-or-update a service role. IAM is eventually consistent, so callers that
    immediately hand the role to another service should allow for propagation."""
    import json as _json
    import time as _time

    from botocore.exceptions import ClientError

    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}],
    }
    if service == "bedrock-agentcore.amazonaws.com":
        # Confused-deputy guard: only this account's AgentCore may assume it.
        trust["Statement"][0]["Condition"] = {"StringEquals": {"aws:SourceAccount": account_id()}}
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=_json.dumps(trust))["Role"]["Arn"]
        if managed:
            iam.attach_role_policy(RoleName=name, PolicyArn=managed)
        if inline:
            iam.put_role_policy(RoleName=name, PolicyName="inline", PolicyDocument=_json.dumps(inline))
        print(f"  ✓ created role {name}; waiting 12s for propagation")
        _time.sleep(12)
        return arn
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        if inline:
            iam.put_role_policy(RoleName=name, PolicyName="inline", PolicyDocument=_json.dumps(inline))
        print(f"  • role {name} exists (policy refreshed)")
        return iam.get_role(RoleName=name)["Role"]["Arn"]


def ensure_lambda(lam, name: str, code: bytes, role_arn: str, handler: str, envvars: dict, timeout: int = 30) -> str:
    from botocore.exceptions import ClientError

    try:
        arn = lam.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler=handler,
            Code={"ZipFile": code},
            Timeout=timeout,
            MemorySize=512,
            Environment={"Variables": envvars},
        )["FunctionArn"]
        print(f"  ✓ created lambda {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=name, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        lam.update_function_configuration(FunctionName=name, Environment={"Variables": envvars}, Timeout=timeout)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        arn = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
        print(f"  ✓ updated lambda {name}")
    lam.get_waiter("function_active_v2").wait(FunctionName=name)
    return arn


def set_log_retention(logs, function_name: str) -> None:
    """Explicit retention on the function's log group; unset means keep forever."""
    from botocore.exceptions import ClientError

    days = int(env("LOG_RETENTION_DAYS", "14"))
    group = f"/aws/lambda/{function_name}"
    try:
        logs.create_log_group(logGroupName=group)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
    logs.put_retention_policy(logGroupName=group, retentionInDays=days)


def wait_status(fetch, ok=("READY", "ACTIVE"), bad=("FAILED", "CREATE_FAILED", "UPDATE_UNSUCCESSFUL"), tries=40):
    """Poll a get_* call until its status settles. Returns the final response."""
    import time as _time

    for _ in range(tries):
        detail = fetch()
        if detail.get("status") in (*ok, *bad):
            return detail
        _time.sleep(5)
    return detail
