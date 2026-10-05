#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3==1.43.108", "botocore==1.43.108"]   # exact, tested versions (1.43.104+ has EFS on runtimes)
# ///
"""Deploy the remote dev box: one AgentCore Runtime microVM box per person (in the VPC, with their own
folder on one EFS file system at /mnt/workspace), their egress firewall, the web-search gateway and the
static edge (CloudFront in front of a Lambda).

  uv run deploy/devbox.py check                       read-only: prerequisites, and what deploy would change
  uv run deploy/devbox.py deploy                      create or update everything (safe to re-run)
  uv run deploy/devbox.py network allowlist           egress: apply templates/egress-allowlist.txt, list what was blocked
  uv run deploy/devbox.py network pause|resume        delete / recreate the firewall, NAT and its IP (the hourly cost)
  uv run deploy/devbox.py status                      what is deployed (read-only)
  uv run deploy/devbox.py reset-box <user>            move a person's box to a new session (a fresh microVM; files stay)
  uv run deploy/devbox.py retire-instances            delete the old Instances boxes (capacity providers, volumes, roles)
  uv run deploy/devbox.py undeploy [--delete-volumes] remove it; the EFS file system stays unless --delete-volumes

Settings: deploy/devbox.env. What was made: deploy/.state.json.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import boto3
import botocore.loaders
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, WaiterError

HERE = Path(__file__).resolve().parent
REMOTE = HERE.parent
TEMPLATES = HERE / "templates"
STATE_FILE = HERE / ".state.json"
ENV_FILE = HERE / "devbox.env"

# ----------------------------------------------------------------------------- constants
REGION = "us-east-1"
COMMIT = "072586267e68ece9a47aa43f8c108e0dcbf44622"
SERVER_ROOT = f"/stable-{COMMIT}"
MOUNT_PATH = "/mnt/workspace"  # exactly one level under /mnt (AgentCore's mountPath rule)
COMPUTE = "microvm"  # DEVBOX_COMPUTE: the only compute type deploy makes boxes on
VM_MAX_LIFETIME = 28800  # 8 h: the most a microVM session lives (idle and max are 60..28800 s)
INSTANCES_MAX_LIFETIME = 1209600  # 14 days: the old Instances boxes (only for DEVBOX_IDLE_SECONDS' range now)
# AgentCore VPC mode's supported Availability Zone IDs (docs: agentcore-vpc.html › Supported Availability Zones)
MICROVM_AZ_IDS = {"us-east-1": ("use1-az1", "use1-az2", "use1-az4")}
HEADER_ALLOWLIST = ["Authorization", "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath"]
OKTA_SCOPES = "openid profile email offline_access devbox"
DEVBOX_SCOPE = "devbox"
SESSION_PREFIX = "dbx-"

VPC_CIDR, BOX_CIDR, FIREWALL_CIDR, PUBLIC_CIDR = "10.40.0.0/16", "10.40.1.0/24", "10.40.2.0/28", "10.40.3.0/24"
RESOLVER = "10.40.0.2/32"  # the VPC's Amazon DNS (base + 2)

TAG_KEY, TAG_VALUE = "devbox", "remote-dev-box"  # on everything devbox.py makes
USER_TAG_KEY = "devbox-user"  # and, on a box's own resources, whose box it is
PAUSED_TAG = "devbox-network-paused"  # on the VPC while the firewall and NAT are deleted

ECR_BOX, ECR_EDGE = "devbox-box", "devbox-edge"
EXEC_ROLE_PREFIX = "devbox-exec-"  # + the box name: one execution role per person (their EFS folder only)
EDGE_ROLE = "devbox-edge-lambda"
GATEWAY_ROLE = "devbox-tools-gateway"
VM_RUNTIME_PREFIX = "devbox_vm_"  # + the box name: the microVM runtime (a runtime's compute type can't change)

# The old Instances boxes: kept only so `status` can list them and `retire-instances` can delete them.
LEGACY_NAME = re.compile(
    r"^devbox_[a-z][a-z0-9]{0,39}$"
)  # capacity provider and runtime devbox_<name> (never devbox_vm_<name>)
OPERATOR_ROLE = "devbox-cp-operator"
INSTANCE_ROLE = "AmazonBedrockAgentCoreCapacityProviderDefaultInstanceRole-devbox"
LEGACY_EXEC_ROLE = "devbox-exec"  # the Instances runtimes' shared execution role

# EFS: one encrypted file system, a mount target in the box subnet, and an access point (a folder) per person
EFS_NAME = EFS_TOKEN = "devbox"  # the Name tag and the CreationToken (CreateFileSystem is idempotent on it)
EFS_SG = "devbox-efs"  # the mount target's security group: NFS in from devbox-box only
EFS_ROOT = "/devbox"  # an access point's root is /devbox/<name>
POSIX_ID = 1000  # the box's `dev` user: every file operation through an access point runs as 1000:1000
EFS_DIR_MODE = "0750"
NFS_PORT = 2049
WORKSPACE_FSTYPE = "nfs"  # DEVBOX_WORKSPACE_FSTYPE: the box sets up /mnt/workspace only once it's this (nfs4)
EFS_CLIENT_ACTIONS = [
    "elasticfilesystem:ClientMount",
    "elasticfilesystem:ClientWrite",
]  # exactly what AgentCore's doc asks for
# Not in the doc, but CreateAgentRuntime refuses an execution role without them ("Execution role is missing required
# filesystem permissions"). Scoped to the person's own file system and access point.
EFS_DESCRIBE_ACTIONS = ["elasticfilesystem:DescribeAccessPoints", "elasticfilesystem:DescribeMountTargets"]
S3_ENDPOINT_NAME = "devbox-s3"  # the free S3 gateway endpoint: ECR's image layers only

GATEWAY_NAME, GATEWAY_TARGET = "devbox-tools", "WebSearch"
POLICY_ENGINE, CEDAR_RULE = "DevboxToolsPolicies", "AllowClaudeCodeWebSearch"

VPC_NAME, SG_NAME, IGW_NAME, NAT_NAME = "devbox", "devbox-box", "devbox-igw", "devbox-nat"
SUBNETS = {"devbox-box": BOX_CIDR, "devbox-firewall": FIREWALL_CIDR, "devbox-public": PUBLIC_CIDR}
ROUTE_TABLES = {
    "devbox-rt-box": "devbox-box",
    "devbox-rt-firewall": "devbox-firewall",
    "devbox-rt-public": "devbox-public",
}
FIREWALL, FIREWALL_POLICY = "devbox-fw", "devbox-egress"
RG_ALLOW, RG_ALLOW_CAPACITY = "devbox-allowlist", 1000
FIREWALL_LOG_GROUP = "/devbox/network-firewall"

# Route 53 Resolver DNS Firewall: the VPC resolver is on the local route, so DNS never passes Network Firewall.
DNS_ALLOW_LIST, DNS_ANY_LIST = "devbox-dns-allow", "devbox-dns-any"  # the allowlist as DNS names; "*"
DNS_RULE_GROUP, DNS_QUERY_LOG = "devbox-dns", "devbox-dns-queries"
DNS_RULE_ALLOW, DNS_RULE_ANY = "devbox-dns-allowlist", "devbox-dns-everything-else"
DNS_LOG_GROUP = "/devbox/dns-queries"
DNS_ASSOCIATION_PRIORITY = 1000  # 100–9900; Firewall Manager takes the ends
DNS_DOMAINS_MAX = 1000  # UpdateFirewallDomains takes 1000 per request

EDGE_FUNCTION, EDGE_LOG_GROUP, OAC_NAME = "devbox-edge", "/aws/lambda/devbox-edge", "devbox-edge"
EDGE_MEMORY_MB, EDGE_TIMEOUT_S = 1024, 30
EDGE_INVOKE_MODE = "BUFFERED"  # edge/src/handler.mjs returns buffered responses only (no streamifyResponse)
SITES = ("workbench", "webview")
SITE_HEADER = "x-devbox-site"
RUNTIME_LOG_PREFIX = "/aws/bedrock-agentcore/runtimes/devbox_"

# Group-driven boxes: nobody is named in devbox.env. Whoever is in DEVBOX_OKTA_GROUP and in exactly one tier
# group gets a box the first time they open the workbench: the page asks POST /api/box, API Gateway checks their Okta
# token, and the provisioner Lambda makes the box (advance_box, the same code deploy uses). devbox-boxes remembers it.
BOX_TABLE = "devbox-boxes"  # key = hex(sha256(uid)); the box's name, tier, generation and resources
PROVISIONER_FUNCTION = PROVISIONER_ROLE = "devbox-provisioner"
PROVISIONER_LOG_GROUP = "/aws/lambda/devbox-provisioner"
PROVISIONER_MEMORY_MB, PROVISIONER_TIMEOUT_S, PROVISIONER_CONCURRENCY = 512, 28, 5
PROVISIONER_RUNTIME, PROVISIONER_HANDLER = "python3.13", "provisioner.handler"
EXEC_BOUNDARY = "devbox-exec-boundary"  # the permissions boundary on every execution role the provisioner makes
API_NAME, API_ORIGIN_ID, API_STAGE = "devbox-api", "devbox-api", "$default"
PROVISION_ROUTE, PROVISION_PATH, API_PATH = "POST /api/box", "/api/box", "/api/*"
TOKEN_HEADER = "X-Devbox-Token"  # the Okta access token for /api/box (CloudFront passes a custom header as is)
API_THROTTLE = {"ThrottlingBurstLimit": 20, "ThrottlingRateLimit": 10.0}
ROLE_SETTLE_S = 10  # a new execution role, before AgentCore is asked to use it
ROLE_PROPAGATION_S = 120  # how long AgentCore refusing a new role still counts as "not usable yet"
DEFAULT_TIER_GROUPS = "Power=ai-claude-power Standard=ai-claude-standard"  # the groups that grant ClaudeCode-<tier>

# CloudFront managed policies (ids are the same in every account)
CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
SECURITY_HEADERS = "67f7725c-6f97-4210-82d7-5512b31e9d03"
STATIC_PATH = "/stable-*/static/*"

RUNTIME_ALLOWED_ACTIONS = [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream",
    "bedrock-agentcore:InvokeAgentRuntimeCommandShell",
]  # the owner's terminal (the authorizer pins the owner)
RUNTIME_DENIED_ACTIONS = [
    "bedrock-agentcore:InvokeAgentRuntimeCommand",
    "bedrock-agentcore:StopRuntimeSession",
    "bedrock-agentcore:InvokeAgentRuntimeForUser",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser",
]
TIER_MODELS = {"Power": ("opus", "sonnet", "haiku"), "Standard": ("sonnet", "haiku")}
OKTA_UID = re.compile(r"^00u[0-9A-Za-z]{17}$")
BOX_NAME = re.compile(r"^[a-z][a-z0-9]{0,37}$")  # devbox_vm_<name> must fit AgentCore's 48-character names

IMAGES = {  # image → (ECR repository, build context, what to run in the context first); the Dockerfile is at its root
    "box": (ECR_BOX, REMOTE / "box", None),
    "edge": (ECR_EDGE, REMOTE / "edge", ["build/build.sh"]),  # the static bundle (edge/dist/) the image copies
}

IMAGE_LIMIT = 2_000_000_000  # AgentCore Runtime's image size quota (compressed or not is undocumented: spike item 5)

SLEEP = time.sleep  # tests replace it


# ============================================================================= output
class Report:
    problems = 0
    changes = 0

    @classmethod
    def reset(cls) -> None:
        cls.problems = cls.changes = 0


def _c(code: str) -> str:
    return f"\033[{code}m" if sys.stdout.isatty() else ""


def say(text: str = "") -> None:
    print(text, flush=True)


def section(text: str) -> None:
    say()
    say(f"{_c('1;37')}{text}{_c('0')}")


def ok(text: str) -> None:
    say(f"  {_c('32')}✓{_c('0')} {text}")


def did(text: str) -> None:
    say(f"  {_c('36')}+{_c('0')} {text}")


def todo(text: str) -> None:
    say(f"  {_c('33')}→{_c('0')} {text}")


def warn(text: str) -> None:
    say(f"  {_c('33')}!{_c('0')} {text}")


def bad(text: str) -> None:
    say(f"  {_c('31')}✗{_c('0')} {text}")
    Report.problems += 1


class Stop(Exception):
    """A fatal problem: already reported, stop the command."""


def die(text: str):
    say(f"  {_c('31')}✗ {text}{_c('0')}")
    raise Stop(text)


def err_code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def err_text(e: ClientError) -> str:
    x = e.response.get("Error", {})
    return f"{x.get('Code', '?')}: {x.get('Message', '')}".strip()


def is_missing(e: ClientError) -> bool:
    c = err_code(e)
    return (
        c.endswith(("NotFound", "NotFoundException"))
        or ".NotFound" in c
        or c in {"NoSuchEntity", "NoSuchDistribution", "NoSuchOriginAccessControl", "ResourceNotFound"}
    )


# ============================================================================= settings
@dataclass(frozen=True)
class User:
    name: str
    tier: str
    email: str

    @property
    def runtime_name(self) -> str:  # the microVM runtime
        return f"{VM_RUNTIME_PREFIX}{self.name}"

    @property
    def legacy_name(self) -> str:  # the old Instances capacity provider and runtime (retire-instances)
        return f"devbox_{self.name}"

    @property
    def exec_role(self) -> str:
        return f"{EXEC_ROLE_PREFIX}{self.name}"

    @property
    def efs_root(self) -> str:  # their access point's root directory on the file system
        return f"{EFS_ROOT}/{self.name}"


@dataclass
class Settings:
    org_profile: str
    ai_profile: str
    region: str
    az: str
    compute: str
    idle_seconds: int
    tier_groups: dict[str, str]  # tier → the Okta group (pushed to Identity Center) whose members get that tier
    okta_domain: str
    okta_auth_server: str
    okta_audience: str
    okta_group: str
    okta_client_id: str
    idc_region: str
    idc_start_url: str
    geo: str
    models: dict[str, str]  # alias → model id (without the GEO prefix)
    enforce_defaults: list[str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def vm_idle_seconds(self) -> int:
        """A microVM session can't idle longer than it can live (8 h)."""
        return min(self.idle_seconds, VM_MAX_LIFETIME)

    @property
    def okta_issuer(self) -> str:
        return f"https://{self.okta_domain}/oauth2/{self.okta_auth_server}"

    @property
    def discovery_url(self) -> str:
        return f"{self.okta_issuer}/.well-known/openid-configuration"


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=VALUE lines, # comments, optional single or double quotes (a quoted value keeps its #)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].strip()
        out[key] = value
    return out


def load_settings(env: dict[str, str]) -> Settings:
    errors: list[str] = []
    g = lambda k, d="": env.get(k, d).strip()

    def num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(g(key, str(default)) or default)
        except ValueError:
            errors.append(f"{key} must be a number")
            return default
        if not lo <= v <= hi:
            errors.append(f"{key}={v} must be between {lo} and {hi}")
        return v

    tier_groups: dict[str, str] = {}
    for entry in (g("DEVBOX_TIER_GROUPS") or DEFAULT_TIER_GROUPS).split():
        tier, _, group = entry.partition("=")
        if tier not in TIER_MODELS:
            errors.append(f"DEVBOX_TIER_GROUPS: {tier!r} isn't a tier ({', '.join(TIER_MODELS)})")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", group):
            errors.append(f"DEVBOX_TIER_GROUPS: {entry!r} must be <tier>=<Okta group name>")
        elif tier in tier_groups or group in tier_groups.values():
            errors.append(f"DEVBOX_TIER_GROUPS names {tier} or {group} twice")
        else:
            tier_groups[tier] = group
    if not tier_groups:
        errors.append("DEVBOX_TIER_GROUPS names no tier")
    warnings: list[str] = []
    if g("DEVBOX_USERS") or any(k.startswith("DEVBOX_UID_") and v.strip() for k, v in env.items()):
        warnings.append(
            "DEVBOX_USERS and DEVBOX_UID_* aren't used any more: a box is made for each member of the Okta groups "
            "on their first visit. Remove the lines"
        )

    s = Settings(
        org_profile=g("ORG_ADMIN_PROFILE"),
        ai_profile=g("AI_ADMIN_PROFILE"),
        region=g("REGION", REGION),
        az=g("DEVBOX_AZ", f"{REGION}a"),
        compute=g("DEVBOX_COMPUTE", COMPUTE).lower(),
        idle_seconds=num("DEVBOX_IDLE_SECONDS", 3600, 60, INSTANCES_MAX_LIFETIME),
        tier_groups=tier_groups,
        okta_domain=g("OKTA_DOMAIN"),
        okta_auth_server=g("OKTA_AUTH_SERVER", "default"),
        okta_audience=g("OKTA_AUDIENCE", "api://default"),
        okta_group=g("DEVBOX_OKTA_GROUP", "devbox-users"),
        okta_client_id=g("DEVBOX_OKTA_CLIENT_ID"),
        idc_region=g("IDC_REGION", REGION),
        idc_start_url=g("IDC_START_URL"),
        geo=g("GEO", "us"),
        models={"opus": g("OPUS_MODEL"), "sonnet": g("SONNET_MODEL"), "haiku": g("HAIKU_MODEL")},
        enforce_defaults=g("FIREWALL_ENFORCE_DEFAULTS", "aws:drop_established aws:alert_established").split(),
        errors=errors,
        warnings=warnings,
    )
    for key, val in (
        ("ORG_ADMIN_PROFILE", s.org_profile),
        ("AI_ADMIN_PROFILE", s.ai_profile),
        ("OKTA_DOMAIN", s.okta_domain),
    ):
        if not val:
            errors.append(f"{key} is empty")
    if s.compute == "instances":
        errors.append(
            "DEVBOX_COMPUTE=instances: deploy no longer makes Instances boxes. On capacity providers AgentCore refuses "
            "the /ws upgrade (424) and the terminal (InvokeAgentRuntimeCommandShell isn't supported). "
            "Use microvm; `retire-instances` deletes the old boxes"
        )
    elif s.compute != COMPUTE:
        errors.append(f"DEVBOX_COMPUTE={s.compute} must be {COMPUTE}")
    if s.region != REGION:
        errors.append(f"REGION={s.region}: the box, its allowlist and the browser config are built for {REGION}")
    if not s.az.startswith(s.region):
        errors.append(f"DEVBOX_AZ={s.az} isn't in {s.region}")
    if g("EDGE_INVOKE_MODE") not in ("", EDGE_INVOKE_MODE):
        errors.append(
            f"EDGE_INVOKE_MODE={g('EDGE_INVOKE_MODE')}: the edge handler only returns {EDGE_INVOKE_MODE} responses "
            "(edge/src/handler.mjs), so the function URL is always BUFFERED; remove the line"
        )
    if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", s.idc_region):
        errors.append(f"IDC_REGION={s.idc_region} isn't a region name")
    for d in s.enforce_defaults:
        if not re.fullmatch(r"aws:(drop|alert)_(strict|established|established_app_layer)", d):
            errors.append(f"FIREWALL_ENFORCE_DEFAULTS: {d} isn't a strict-order default action")
    for tier in tier_groups:
        for alias in TIER_MODELS[tier]:
            if not s.models.get(alias):
                errors.append(f"{alias.upper()}_MODEL is empty ({tier} tier uses it)")
    if s.okta_group in tier_groups.values():
        errors.append(f"DEVBOX_OKTA_GROUP ({s.okta_group}) can't also be a tier group")
    if s.okta_client_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", s.okta_client_id):
        errors.append("DEVBOX_OKTA_CLIENT_ID doesn't look like an Okta client id")
    if s.okta_group and not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", s.okta_group):
        errors.append("DEVBOX_OKTA_GROUP may only use letters, digits and _ . - (an AgentCore claim match value)")
    return s


# ============================================================================= pure builders
# Every AWS request below is built by a function with no side effects, so the tests can check each
# one against the botocore service model and the design's invariants without credentials.


def render(text: str, values: dict[str, str]) -> str:
    """Replace {{NAME}}; a placeholder with no value is an error, so a half-rendered file never ships."""
    names = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", text))
    missing = sorted(n for n in names if not values.get(n))
    if missing:
        raise ValueError(f"no value for {', '.join(missing)}")
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: str(values[m.group(1)]), text)


def template(name: str, values: dict[str, str]) -> str:
    return render((TEMPLATES / name).read_text(), values)


def policy(name: str, values: dict[str, str]) -> dict:
    return json.loads(template(name, values))


def canonical(doc) -> object:
    """A policy in a form where "x" == ["x"] and list order doesn't matter (the way IAM reads it)."""
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError:
            return doc
    if isinstance(doc, dict):
        return {k: canonical(v) for k, v in doc.items()}
    if isinstance(doc, list):
        items = [canonical(v) for v in doc]
        return items[0] if len(items) == 1 else sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
    return doc


def same_policy(a, b) -> bool:
    return canonical(a) == canonical(b)


def covers(have, want) -> bool:
    """True if `have` (a describe result) has everything `want` sets; fields the service adds are ignored."""
    if isinstance(want, list) and not want:
        return not have  # an empty list and a field the service leaves out are the same
    if isinstance(want, dict):
        return isinstance(have, dict) and all(covers(have.get(k), v) for k, v in want.items())
    if isinstance(want, list):
        return (
            isinstance(have, list)
            and len(have) == len(want)
            and all(covers(h, w) for h, w in zip(have, want, strict=True))
        )
    return have == want


def uid_key(uid: str) -> str:
    """The browser config's key for a person's box: hex(sha256(uid))."""
    return hashlib.sha256(uid.encode()).hexdigest()


def session_id(uid: str, generation: int) -> str:
    """The session id: "dbx-" + hex(sha256(uid + ":" + generation)), 68 characters."""
    return SESSION_PREFIX + hashlib.sha256(f"{uid}:{generation}".encode()).hexdigest()


def recover_generation(uid: str, sid: str, limit: int = 1000) -> int | None:
    for g in range(1, limit + 1):
        if session_id(uid, g) == sid:
            return g
    return None


def okta_uid_from_external_ids(external_ids: list[dict]) -> str | None:
    for x in external_ids or []:
        if OKTA_UID.match(x.get("Id", "")):
            return x["Id"]
    return None


def tags_map(**extra: str) -> dict[str, str]:
    return {TAG_KEY: TAG_VALUE, **extra}


def tag_list(name: str | None = None, key: str = "Key", value: str = "Value") -> list[dict]:
    t = [{key: TAG_KEY, value: TAG_VALUE}]
    if name:
        t.append({key: "Name", value: name})
    return t


def tag_spec(resource_type: str, name: str) -> list[dict]:
    return [{"ResourceType": resource_type, "Tags": tag_list(name)}]


# ---- IAM
def role_request(name: str, trust: dict, description: str) -> dict:
    return {
        "RoleName": name,
        "AssumeRolePolicyDocument": json.dumps(trust),
        "Description": description,
        "MaxSessionDuration": 3600,
        "Tags": tag_list(),
    }


def iam_roles(account: str, region: str) -> dict[str, dict]:
    """The shared roles devbox.py owns: trust, AWS managed policies and inline policies. Each person's
    execution role is exec_role_spec() (it names their EFS access point, so it's made with the storage)."""
    v = {"ACCOUNT_ID": account, "REGION": region}
    return {
        EDGE_ROLE: {
            "trust": policy("iam/edge-lambda-trust.json", v),
            "managed": [],
            "inline": {"own-logs-only": policy("iam/edge-lambda-policy.json", v)},
            "description": "Dev box: the static edge Lambda (CloudWatch Logs only)",
        },
        GATEWAY_ROLE: {
            "trust": policy("iam/gateway-trust.json", v),
            "managed": [],
            "inline": {"devbox-tools-gateway": policy("iam/gateway-policy.json", v)},
            "description": "Dev box: the devbox-tools gateway (web search, Cedar policy)",
        },
    }


EXEC_INLINE_POLICY = "pull-image-log-mount-own-folder"


def exec_role_spec(account: str, region: str, user: User, *, file_system_arn: str, access_point_arn: str) -> dict:
    """One person's execution role (the person in the box can read its credentials): pull devbox-box, write their
    own runtime's logs, and mount the file system only through their own access point. No Bedrock, no
    GetWorkloadAccessToken*."""
    v = {
        "ACCOUNT_ID": account,
        "REGION": region,
        "RUNTIME_NAME": user.runtime_name,
        "FILE_SYSTEM_ARN": file_system_arn,
        "ACCESS_POINT_ARN": access_point_arn,
    }
    return {
        "trust": policy("iam/exec-trust.json", v),
        "managed": [],
        "inline": {EXEC_INLINE_POLICY: policy("iam/exec-policy.json", v)},
        "description": f"Dev box: {user.name}'s box; readable inside the box, so no Bedrock; EFS only via their own access point",
    }


def ecr_repository_request(name: str) -> dict:
    return {
        "repositoryName": name,
        "imageTagMutability": "IMMUTABLE",
        "imageScanningConfiguration": {"scanOnPush": True},
        "encryptionConfiguration": {"encryptionType": "AES256"},
        "tags": tag_list(),
    }


def docker_auth_config(registry_host: str, token: str) -> dict:
    """A throwaway Docker config for one push (the token is base64 "AWS:<password>" already)."""
    return {"auths": {registry_host: {"auth": token}}}


# ---- the per-person box
def tier_models(s: Settings, tier: str) -> dict[str, str]:
    return {alias: f"{s.geo}.{s.models[alias]}" for alias in TIER_MODELS[tier]}


def runtime_env(
    s: Settings, user: User, *, uid: str, generation: int, account: str, start_url: str, gateway_url: str
) -> dict[str, str]:
    """The runtime env. DEVBOX_MODELS maps each alias the tier may use to its inference profile."""
    return {
        "DEVBOX_OWNER": user.name,
        "DEVBOX_OWNER_UID": uid,
        "DEVBOX_SESSION_ID": session_id(uid, generation),
        "DEVBOX_TIER": user.tier,
        "DEVBOX_SSO_ROLE": f"ClaudeCode-{user.tier}",
        "DEVBOX_ACCOUNT_ID": account,
        "DEVBOX_SSO_START_URL": start_url,
        "DEVBOX_SSO_REGION": s.idc_region,
        "DEVBOX_MODELS": json.dumps(tier_models(s, user.tier), separators=(",", ":")),
        "DEVBOX_TOOLS_GATEWAY_URL": gateway_url,
        # A microVM starts the container with a placeholder at /mnt/workspace and mounts the EFS access
        # point over it at the first invocation (live 2026-09-29): the box waits for the NFS mount.
        "DEVBOX_WORKSPACE_FSTYPE": WORKSPACE_FSTYPE,
    }


def authorizer(s: Settings, uid: str) -> dict:
    """Only this person's Okta token for the Dev Box app, with the devbox scope and group."""
    return {
        "customJWTAuthorizer": {
            "discoveryUrl": s.discovery_url,
            "allowedAudience": [s.okta_audience],
            "allowedClients": [s.okta_client_id],
            "allowedScopes": [DEVBOX_SCOPE],
            "customClaims": [
                {
                    "inboundTokenClaimName": "groups",
                    "inboundTokenClaimValueType": "STRING_ARRAY",
                    "authorizingClaimMatchValue": {
                        "claimMatchValue": {"matchValueStringList": [s.okta_group]},
                        "claimMatchOperator": "CONTAINS_ANY",
                    },
                },
                {
                    "inboundTokenClaimName": "uid",
                    "inboundTokenClaimValueType": "STRING",
                    "authorizingClaimMatchValue": {
                        "claimMatchValue": {"matchValueString": uid},
                        "claimMatchOperator": "EQUALS",
                    },
                },
            ],
        }
    }


def vm_lifecycle(s: Settings) -> dict:
    """A microVM session: idle stop after DEVBOX_IDLE_SECONDS (at most 8 h), and never older than 8 h."""
    return {"idleRuntimeSessionTimeout": s.vm_idle_seconds, "maxLifetime": VM_MAX_LIFETIME}


def runtime_request(
    s: Settings,
    user: User,
    *,
    uid: str,
    image_uri: str,
    exec_role_arn: str,
    access_point_arn: str,
    subnet_id: str,
    security_group_id: str,
    env: dict[str, str],
) -> dict:
    """CreateAgentRuntime (microVM): in the box subnet behind the egress firewall, with the person's
    EFS access point at /mnt/workspace. No capacityProviderConfiguration: that would make it an Instances runtime."""
    return {
        "agentRuntimeName": user.runtime_name,
        "description": f"Dev box for {user.name} (microVM; files on EFS {user.efs_root})",
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image_uri}},
        "roleArn": exec_role_arn,
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {"subnets": [subnet_id], "securityGroups": [security_group_id]},
        },
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "filesystemConfigurations": [{"efsAccessPoint": {"accessPointArn": access_point_arn, "mountPath": MOUNT_PATH}}],
        "authorizerConfiguration": authorizer(s, uid),
        "requestHeaderConfiguration": {"requestHeaderAllowlist": list(HEADER_ALLOWLIST)},
        "lifecycleConfiguration": vm_lifecycle(s),
        "environmentVariables": env,
        "tags": tags_map(**{USER_TAG_KEY: user.name}),
    }


RUNTIME_FIELDS = (
    "agentRuntimeArtifact",
    "roleArn",
    "networkConfiguration",
    "protocolConfiguration",
    "capacityProviderConfiguration",
    "filesystemConfigurations",
    "authorizerConfiguration",
    "requestHeaderConfiguration",
    "lifecycleConfiguration",
    "environmentVariables",
    "description",
)


def runtime_update_request(runtime_id: str, create: dict, *, require_mmdsv2: bool = True) -> dict:
    """UpdateAgentRuntime replaces the whole configuration, so it carries every field of the create."""
    req = {k: create[k] for k in RUNTIME_FIELDS if k in create}
    req["agentRuntimeId"] = runtime_id
    if require_mmdsv2:
        req["metadataConfiguration"] = {"requireMMDSV2": True}
    return req


def runtime_update_from_current(current: dict, env: dict[str, str]) -> dict:
    """An update that keeps a runtime exactly as it is except its environment (reset-box). What
    GetAgentRuntime adds on its own is left out: requireServiceS3Endpoint in the VPC config can't be
    sent back for a runtime created after 2026-06-11 (live: ValidationException "cannot modify
    requireServiceS3Endpoint")."""
    req = {k: copy.deepcopy(current[k]) for k in RUNTIME_FIELDS if k in current and k != "environmentVariables"}
    (req.get("networkConfiguration", {}).get("networkModeConfig") or {}).pop("requireServiceS3Endpoint", None)
    req["environmentVariables"] = env
    req["agentRuntimeId"] = current["agentRuntimeId"]
    if current.get("metadataConfiguration"):
        req["metadataConfiguration"] = current["metadataConfiguration"]
    return req


def runtime_drift(current: dict, desired: dict) -> list[str]:
    """Fields GetAgentRuntime shows differently from what deploy would send. Fields the service adds
    are ignored, except in the environment, where a leftover variable matters."""

    def norm(v):
        return json.loads(json.dumps(v, sort_keys=True, default=str))

    out = []
    for k in RUNTIME_FIELDS:
        if k not in desired:
            continue
        have, want = norm(current.get(k)), norm(desired[k])
        if not (have == want if k in ("environmentVariables", "roleArn", "description") else covers(have, want)):
            out.append(k)
    return out


def mmdsv2_needed(current: dict) -> bool:
    return not (current.get("metadataConfiguration") or {}).get("requireMMDSV2")


def runtime_resource_policy(runtime_arn: str) -> dict:
    return policy("iam/runtime-resource-policy.json", {"RUNTIME_ARN": runtime_arn})


# ---- group-driven boxes: what a box shares with every other, a new person's name and tier, the record
@dataclass(frozen=True)
class BoxPlan:
    """What every box shares, from the deploy. The provisioner gets it as DEVBOX_PLAN (nothing in it is secret)."""

    account: str
    file_system_id: str
    subnet_id: str
    security_group_id: str
    image_uri: str
    gateway_url: str
    start_url: str
    boundary_arn: str

    @property
    def file_system_arn(self) -> str:
        return file_system_arn(self.account, REGION, self.file_system_id)


def settings_env(s: Settings) -> dict[str, str]:
    """The devbox.env values the provisioner needs (no profiles: it runs as its own role), as load_settings() reads them."""
    return {
        "ORG_ADMIN_PROFILE": "-",
        "AI_ADMIN_PROFILE": "-",
        "REGION": s.region,
        "DEVBOX_AZ": s.az,
        "DEVBOX_COMPUTE": s.compute,
        "DEVBOX_IDLE_SECONDS": str(s.idle_seconds),
        "OKTA_DOMAIN": s.okta_domain,
        "OKTA_AUTH_SERVER": s.okta_auth_server,
        "OKTA_AUDIENCE": s.okta_audience,
        "DEVBOX_OKTA_GROUP": s.okta_group,
        "DEVBOX_OKTA_CLIENT_ID": s.okta_client_id,
        "IDC_REGION": s.idc_region,
        "GEO": s.geo,
        "OPUS_MODEL": s.models.get("opus", ""),
        "SONNET_MODEL": s.models.get("sonnet", ""),
        "HAIKU_MODEL": s.models.get("haiku", ""),
        "DEVBOX_TIER_GROUPS": " ".join(f"{t}={g}" for t, g in sorted(s.tier_groups.items())),
    }


def box_name_for(login: str, uid: str) -> str:
    """A new person's box name: the letters and digits of their sign-in name (at most 24), then 6 characters of
    hex(sha256(uid)), so two people with the same name never share a box or a folder. It fits BOX_NAME."""
    base = re.sub(r"[^a-z0-9]", "", (login or "").split("@", 1)[0].lower())[:24]
    if not base[:1].isalpha():
        base = ("u" + base)[:24]
    return base + uid_key(uid)[:6]


def tier_for(s: Settings, groups: list[str]) -> tuple[str | None, str]:
    """(tier, why not): a box needs exactly one tier group. In two, nobody guesses which: an admin keeps one."""
    mine = sorted(t for t, grp in s.tier_groups.items() if grp in groups)
    if len(mine) == 1:
        return mine[0], ""
    if not mine:
        return None, (
            f"your sign-in names none of the tier groups ({', '.join(sorted(s.tier_groups.values()))}): ask an admin to "
            "add you to one, or, if you're in one, to add them to the Dev Box app's groups claim in Okta"
        )
    return None, (
        f"you're in more than one tier group ({', '.join(s.tier_groups[t] for t in mine)}): "
        "ask an admin to keep you in one"
    )


def new_box_record(uid: str, login: str, tier: str, now: int) -> dict:
    return {
        "key": uid_key(uid),
        "uid": uid,
        "name": box_name_for(login, uid),
        "login": login,
        "tier": tier,
        "generation": 1,
        "createdAt": now,
    }


def box_user(rec: dict) -> User:
    return User(rec["name"], rec["tier"], rec.get("login", ""))


def browser_box(rec: dict) -> dict:
    """What the page needs to open a box: the same fields the browser config's boxes map had."""
    return {
        "name": rec["name"],
        "runtimeArn": rec["runtimeArn"],
        "generation": int(rec.get("generation") or 1),
        "compute": COMPUTE,
        "terminal": True,
    }


def state_box(rec: dict) -> dict:
    """The record as .state.json keeps it (status, reset-box and the explainer read it)."""
    keep = (
        "name",
        "uid",
        "tier",
        "generation",
        "accessPointId",
        "accessPointArn",
        "execRoleArn",
        "runtimeArn",
        "runtimeId",
        "sessionId",
        "mmdsv2Rejected",
    )
    return {k: rec[k] for k in keep if rec.get(k) not in (None, "")} | {"compute": COMPUTE}


# DynamoDB, without boto3's resource layer (the record's numbers come back as int, not Decimal)
def ddb_item(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if v is None or v == "":
            continue
        out[k] = {"N": str(v)} if isinstance(v, (int, float)) and not isinstance(v, bool) else {"S": str(v)}
    return out


def ddb_rec(item: dict) -> dict:
    out = {}
    for k, v in (item or {}).items():
        if "N" in v:
            n = float(v["N"])
            out[k] = int(n) if n.is_integer() else n
        elif "S" in v:
            out[k] = v["S"]
    return out


def get_box(ddb, key: str) -> dict | None:
    item = ddb.get_item(TableName=BOX_TABLE, Key={"key": {"S": key}}, ConsistentRead=True).get("Item")
    return ddb_rec(item) if item else None


def put_box(ddb, rec: dict, *, new: bool = False) -> None:
    """Save a record. new=True only if nobody saved it first (two tabs on a first visit)."""
    extra = (
        {"ConditionExpression": "attribute_not_exists(#k)", "ExpressionAttributeNames": {"#k": "key"}} if new else {}
    )
    ddb.put_item(TableName=BOX_TABLE, Item=ddb_item({k: v for k, v in rec.items() if k != "lockUntil"}), **extra)


def box_records(ddb) -> list[dict]:
    out, start = [], None
    while True:
        r = ddb.scan(TableName=BOX_TABLE, **({"ExclusiveStartKey": start} if start else {}))
        out += [ddb_rec(i) for i in r.get("Items") or []]
        start = r.get("LastEvaluatedKey")
        if not start:
            return sorted(out, key=lambda x: x.get("name", ""))


ROLE_NOT_READY = re.compile(r"(?i)role|assum|trust|principal")


def role_not_ready(e: ClientError, role_made_at: int, now: float) -> bool:
    """AgentCore refusing a role IAM is still making: only just after we made it, and never a refusal of the caller
    itself (its message names the caller's role, "assumed-role/…", so the pattern alone would match it forever)."""
    text = err_text(e)
    return (
        err_code(e) in ("ValidationException", "AccessDeniedException")
        and bool(ROLE_NOT_READY.search(text))
        and "is not authorized to perform" not in text
        and now - role_made_at < ROLE_PROPAGATION_S
    )


def advance_box(cl: dict, s: Settings, plan: BoxPlan, rec: dict, *, act=None, now=time.time) -> dict:
    """Take one person's box as far towards ready as it can go right now, and say where it got to. It never waits:
    what's still being made (the folder, the role, the runtime) is looked at again on the next call. The provisioner
    calls it once per page poll; deploy calls it until the box is ready. `rec` (the devbox-boxes record) is updated in
    place. cl: the efs, iam and acc (bedrock-agentcore-control) clients. act(what, fn, **kw) makes each change
    (deploy passes its own, which only reports in check mode and returns None).
    Returns {"state": ready | working | pending | failed, "step": …, "message": …}."""
    act = act or (lambda what, fn, **kw: fn(**kw))
    efs, iam, acc = cl["efs"], cl["iam"], cl["acc"]
    user, uid, gen = box_user(rec), rec["uid"], int(rec.get("generation") or 1)

    def out(state: str, step: str, message: str) -> dict:
        rec["step"], rec["message"] = step, message
        return {"state": state, "step": step, "message": message}

    # 1 · Their folder: an access point rooted at /devbox/<name>; every file operation through it runs as 1000:1000.
    if not rec.get("accessPointId"):
        r = act(
            f"create access point {user.efs_root}",
            efs.create_access_point,
            **access_point_request(plan.file_system_id, user, rec["key"]),
        )
        if not r:
            return out("pending", "folder", f"would create the folder {user.efs_root}")
        rec.update(accessPointId=r["AccessPointId"], accessPointArn=r["AccessPointArn"])
    try:
        aps = efs.describe_access_points(AccessPointId=rec["accessPointId"]).get("AccessPoints") or []
    except ClientError as e:
        if not is_missing(e):
            raise
        aps = []
    ap_state = aps[0].get("LifeCycleState") if aps else None
    if ap_state is None:
        rec.pop("accessPointId", None), rec.pop("accessPointArn", None)
        return out("working", "folder", "making your folder again")
    if ap_state == "error":
        return out(
            "failed", "folder", f"your folder (access point {rec['accessPointId']}) is in the error state: ask an admin"
        )
    if ap_state != "available":
        return out("working", "folder", "making your folder on the shared file system")

    # 2 · Its execution role: pull the image, write its own logs, mount only this folder. Made with the boundary.
    try:
        role = iam.get_role(RoleName=user.exec_role)["Role"]
    except ClientError as e:
        if not is_missing(e):
            raise
        role = None
    if role is None:
        spec = exec_role_spec(
            plan.account, s.region, user, file_system_arn=plan.file_system_arn, access_point_arn=rec["accessPointArn"]
        )
        r = act(
            f"create role {user.exec_role} (boundary {EXEC_BOUNDARY})",
            iam.create_role,
            **role_request(user.exec_role, spec["trust"], spec["description"]),
            PermissionsBoundary=plan.boundary_arn,
        )
        if not r:
            return out("pending", "role", f"would create role {user.exec_role}")
        act(
            f"  set {EXEC_INLINE_POLICY}",
            iam.put_role_policy,
            RoleName=user.exec_role,
            PolicyName=EXEC_INLINE_POLICY,
            PolicyDocument=json.dumps(spec["inline"][EXEC_INLINE_POLICY]),
        )
        rec.update(execRoleArn=r["Role"]["Arn"], roleMadeAt=int(now()))
        return out("working", "role", "making your box's role")
    rec["execRoleArn"] = role["Arn"]

    # 3 · The runtime: a microVM in the box subnet whose authorizer takes only their Okta uid, their folder at /mnt/workspace.
    env = runtime_env(
        s, user, uid=uid, generation=gen, account=plan.account, start_url=plan.start_url, gateway_url=plan.gateway_url
    )
    want = runtime_request(
        s,
        user,
        uid=uid,
        image_uri=plan.image_uri,
        exec_role_arn=rec["execRoleArn"],
        access_point_arn=rec["accessPointArn"],
        subnet_id=plan.subnet_id,
        security_group_id=plan.security_group_id,
        env=env,
    )
    if not rec.get("runtimeId"):
        found = next(
            (r for r in paged(acc.list_agent_runtimes, "agentRuntimes") if r["agentRuntimeName"] == user.runtime_name),
            None,
        )
        if found:
            rec.update(runtimeId=found["agentRuntimeId"], runtimeArn=found["agentRuntimeArn"])
        else:
            if now() - int(rec.get("roleMadeAt") or 0) < ROLE_SETTLE_S:
                return out("working", "role", "waiting for the new role to be usable")
            try:
                r = act(
                    f"create runtime {user.runtime_name} (microVM, {MOUNT_PATH} = EFS {user.efs_root}, owner uid {uid})",
                    acc.create_agent_runtime,
                    **want,
                )
            except ClientError as e:
                if role_not_ready(e, int(rec.get("roleMadeAt") or 0), now()):
                    return out("working", "role", "waiting for the new role to be usable")
                if err_code(e) == "ConflictException":
                    return out("working", "runtime", "starting your box")
                raise
            if not r:
                return out("pending", "runtime", f"would create runtime {user.runtime_name}")
            rec.update(runtimeId=r["agentRuntimeId"], runtimeArn=r["agentRuntimeArn"])
            return out("working", "runtime", "creating your box (the first time takes a few minutes)")
    try:
        cur = acc.get_agent_runtime(agentRuntimeId=rec["runtimeId"])
    except ClientError as e:
        if not is_missing(e):
            raise
        rec.pop("runtimeId", None), rec.pop("runtimeArn", None)
        return out("working", "runtime", "making your box again")
    status = cur.get("status") or ""
    if status in ("CREATING", "UPDATING"):
        return out("working", "runtime", "creating your box" if status == "CREATING" else "updating your box")
    if failed_status(status):
        return out(
            "failed", "runtime", f"your box's runtime is {status}: {cur.get('failureReason') or 'no reason given'}"
        )
    if cur.get("capacityProviderConfiguration"):
        return out("failed", "runtime", f"runtime {user.runtime_name} is an Instances runtime: an admin must delete it")
    drift = runtime_drift(cur, want)
    if drift:
        r = act(
            f"update runtime {user.runtime_name} ({', '.join(drift)})",
            acc.update_agent_runtime,
            **runtime_update_request(rec["runtimeId"], want, require_mmdsv2=not mmdsv2_needed(cur)),
        )
        if r is None:
            return out("pending", "runtime", f"would update runtime {user.runtime_name} ({', '.join(drift)})")
        return out("working", "runtime", "updating your box (a new image, or your tier changed)")

    # 4 · The resource policy: their browser may use the box and its terminal; no command API, stop or act-as-user.
    want_rbp = runtime_resource_policy(rec["runtimeArn"])
    try:
        have = acc.get_resource_policy(resourceArn=rec["runtimeArn"]).get("policy")
    except ClientError as e:
        if not is_missing(e):
            raise
        have = None
    if not (have and same_policy(have, want_rbp)) and (
        act(
            "set the resource policy (the owner's workbench and terminal only)",
            acc.put_resource_policy,
            resourceArn=rec["runtimeArn"],
            policy=json.dumps(want_rbp),
        )
        is None
    ):
        return out("pending", "access", "would set the resource policy")

    # 5 · MMDSv2 (spike item 7): asked once; a refusal is remembered, not retried forever.
    if mmdsv2_needed(cur) and not rec.get("mmdsv2Rejected"):
        try:
            r = act(
                "require MMDSv2 on the runtime",
                acc.update_agent_runtime,
                **runtime_update_request(rec["runtimeId"], want, require_mmdsv2=True),
            )
        except ClientError as e:
            if mmdsv2_refused(e):
                rec["mmdsv2Rejected"] = err_text(e)[:200]
            elif err_code(e) == "ConflictException":
                return out("working", "metadata", "finishing your box's settings")
            else:
                raise
        else:
            if r is None:
                return out("pending", "metadata", "would require MMDSv2")
            return out("working", "metadata", "finishing your box's settings")
    rec["sessionId"] = session_id(uid, gen)
    return out("ready", "ready", "ready")


# ---- EFS (one file system, one access point per person)
def file_system_request() -> dict:
    return {
        "CreationToken": EFS_TOKEN,
        "PerformanceMode": "generalPurpose",
        "Encrypted": True,
        "ThroughputMode": "elastic",
        "Tags": tag_list(EFS_NAME),
    }


def mount_target_request(file_system_id: str, subnet_id: str, security_group_id: str) -> dict:
    return {"FileSystemId": file_system_id, "SubnetId": subnet_id, "SecurityGroups": [security_group_id]}


def access_point_request(file_system_id: str, user: User, client_token: str) -> dict:
    """The person's folder: rooted at /devbox/<name> (made 1000:1000 0750 on first use), and every file operation
    through it runs as 1000:1000, whatever uid the box's process has."""
    return {
        "ClientToken": client_token[:64],
        "FileSystemId": file_system_id,
        "PosixUser": {"Uid": POSIX_ID, "Gid": POSIX_ID},
        "RootDirectory": {
            "Path": user.efs_root,
            "CreationInfo": {"OwnerUid": POSIX_ID, "OwnerGid": POSIX_ID, "Permissions": EFS_DIR_MODE},
        },
        "Tags": tag_list(f"devbox-{user.name}") + [{"Key": USER_TAG_KEY, "Value": user.name}],
    }


def access_point_drift(ap: dict, user: User) -> list[str]:
    """Access points can't be changed: what one has that deploy wouldn't make (reported, never changed)."""
    out = []
    pu, rd = ap.get("PosixUser") or {}, ap.get("RootDirectory") or {}
    if (pu.get("Uid"), pu.get("Gid")) != (POSIX_ID, POSIX_ID) or pu.get("SecondaryGids"):
        out.append(f"POSIX user {pu.get('Uid')}:{pu.get('Gid')}")
    ci = rd.get("CreationInfo") or {}
    if (ci.get("OwnerUid"), ci.get("OwnerGid"), ci.get("Permissions")) != (POSIX_ID, POSIX_ID, EFS_DIR_MODE):
        out.append(f"creation info {ci.get('OwnerUid')}:{ci.get('OwnerGid')} {ci.get('Permissions')}")
    return out


def file_system_policy(file_system_arn: str, grants: dict[str, tuple[str, str]]) -> dict:
    """grants: box name → (execution role ARN, access point ARN). Without a file system policy EFS lets any NFS
    client that reaches the mount target in, with no IAM at all (a userspace NFS client can claim any uid). This
    one grants mount and write only to each person's role, only through their own access point, only over TLS:
    anonymous clients, the file system's root and another person's folder are all refused."""
    statements = [
        {
            "Sid": "TlsOnly",
            "Effect": "Deny",
            "Principal": {"AWS": "*"},
            "Action": [*EFS_CLIENT_ACTIONS, "elasticfilesystem:ClientRootAccess"],
            "Resource": file_system_arn,
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }
    ]
    for name, (role_arn, ap_arn) in sorted(grants.items()):
        statements.append(
            {
                "Sid": f"OwnFolderOnly{name.capitalize()}",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": list(EFS_CLIENT_ACTIONS),
                "Resource": file_system_arn,
                "Condition": {"ArnEquals": {"elasticfilesystem:AccessPointArn": ap_arn}},
            }
        )
    return {"Version": "2012-10-17", "Id": "devbox-one-folder-per-person", "Statement": statements}


def file_system_arn(account: str, region: str, file_system_id: str) -> str:
    return f"arn:aws:elasticfilesystem:{region}:{account}:file-system/{file_system_id}"


# ---- the S3 gateway endpoint (free): the box image's layers without the NAT or the firewall
def s3_endpoint_policy(region: str) -> dict:
    return policy("iam/s3-endpoint-policy.json", {"REGION": region})


def s3_endpoint_request(vpc_id: str, route_table_id: str, region: str) -> dict:
    return {
        "VpcEndpointType": "Gateway",
        "VpcId": vpc_id,
        "ServiceName": f"com.amazonaws.{region}.s3",
        "RouteTableIds": [route_table_id],
        "PolicyDocument": json.dumps(s3_endpoint_policy(region)),
        "TagSpecifications": tag_spec("vpc-endpoint", S3_ENDPOINT_NAME),
    }


# ---- .state.json
def is_legacy_record(rec: dict) -> bool:
    """A box record from the Instances deploy (capacity provider devbox_<name>, runtime devbox_<name>)."""
    return rec.get("compute") != COMPUTE and any(rec.get(k) for k in ("cpId", "cpArn", "runtimeArn", "runtimeId"))


def migrate_state(state: dict) -> dict:
    """Instances records move to state["instances"] (retire-instances deletes what they name); each person gets a
    fresh microVM record that keeps only their uid, so the new runtime starts at generation 1."""
    boxes = state.get("boxes") or {}
    for name, rec in list(boxes.items()):
        if is_legacy_record(rec):
            state.setdefault("instances", {}).setdefault(name, rec)
            boxes[name] = {"name": name, **({"uid": rec["uid"]} if rec.get("uid") else {})}
    return state


# ---- network
def subnet_request(vpc_id: str, name: str, az: str) -> dict:
    return {
        "VpcId": vpc_id,
        "CidrBlock": SUBNETS[name],
        "AvailabilityZone": az,
        "TagSpecifications": tag_spec("subnet", name),
    }


def sg_egress_wanted(efs_sg_id: str | None = None) -> list[dict]:
    """HTTPS out (Network Firewall decides where). No plain HTTP: a Host header is just a string anyone can
    set, and nothing on the allowlist needs port 80. The two DNS rules are documentation: security groups don't
    filter traffic to the Amazon DNS server at all. What limits DNS is the DNS Firewall on the VPC resolver (and
    no other port 53 leaves). NFS goes only to the EFS mount target's security group (it stays in the box subnet)."""
    nfs = [
        {
            "IpProtocol": "tcp",
            "FromPort": NFS_PORT,
            "ToPort": NFS_PORT,
            "UserIdGroupPairs": [
                {"GroupId": efs_sg_id, "Description": "NFS to the EFS mount target (devbox-efs) only"}
            ],
        }
    ]
    return [
        {
            "IpProtocol": "tcp",
            "FromPort": 443,
            "ToPort": 443,
            "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS, through the firewall"}],
        },
        {
            "IpProtocol": "udp",
            "FromPort": 53,
            "ToPort": 53,
            "IpRanges": [{"CidrIp": RESOLVER, "Description": "DNS, VPC resolver (DNS Firewall)"}],
        },
        {
            "IpProtocol": "tcp",
            "FromPort": 53,
            "ToPort": 53,
            "IpRanges": [{"CidrIp": RESOLVER, "Description": "DNS, VPC resolver (DNS Firewall)"}],
        },
        *(nfs if efs_sg_id else []),
    ]


DEFAULT_EGRESS = [
    {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
]  # what every new security group starts with


def efs_sg_ingress_wanted(box_sg_id: str) -> list[dict]:
    """The mount target answers NFS from the boxes' security group, nothing else (no outbound: it never calls out)."""
    return [
        {
            "IpProtocol": "tcp",
            "FromPort": NFS_PORT,
            "ToPort": NFS_PORT,
            "UserIdGroupPairs": [{"GroupId": box_sg_id, "Description": "NFS from the dev boxes (devbox-box) only"}],
        }
    ]


def rule_key(rule: dict) -> tuple:
    """One comparable key per security group rule, from either an IpPermission or a SecurityGroupRule."""
    if "IsEgress" in rule:  # SecurityGroupRule (describe_security_group_rules)
        peer = (
            rule.get("CidrIpv4")
            or (rule.get("ReferencedGroupInfo") or {}).get("GroupId")
            or rule.get("CidrIpv6")
            or rule.get("PrefixListId")
        )
        return (str(rule["IpProtocol"]), rule.get("FromPort", -1), rule.get("ToPort", -1), peer)
    peer = (rule.get("IpRanges") or [{}])[0].get("CidrIp") or (rule.get("UserIdGroupPairs") or [{}])[0].get("GroupId")
    return (str(rule["IpProtocol"]), rule.get("FromPort", -1), rule.get("ToPort", -1), peer)


def route_plan(ids: dict[str, str | None]) -> dict[str, list[tuple[str, str, str | None]]]:
    """Routes per table: (destination, target kind, target id): box → firewall → NAT → IGW,
    and the return path from the NAT to the box goes back through the firewall."""
    fw, nat, igw = ids.get("firewall_endpoint"), ids.get("nat"), ids.get("igw")
    return {
        "devbox-rt-box": [("0.0.0.0/0", "VpcEndpointId", fw)],
        "devbox-rt-firewall": [("0.0.0.0/0", "NatGatewayId", nat)],
        "devbox-rt-public": [("0.0.0.0/0", "GatewayId", igw), (BOX_CIDR, "VpcEndpointId", fw)],
    }


def route_target(route: dict) -> str | None:
    for k in (
        "GatewayId",
        "NatGatewayId",
        "NetworkInterfaceId",
        "TransitGatewayId",
        "VpcPeeringConnectionId",
        "InstanceId",
    ):
        if route.get(k):
            return route[k]
    return None


def parse_allowlist(text: str, values: dict[str, str]) -> list[str]:
    """The domains of templates/egress-allowlist.txt, placeholders filled, comments dropped, no duplicates."""
    out: list[str] = []
    for line in render(text, values).splitlines():
        name = line.split("#", 1)[0].strip().lower()
        if not name:
            continue
        if not re.fullmatch(r"\.?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", name):
            raise ValueError(f"not a domain name: {name!r}")
        if name not in out:
            out.append(name)
    return out


def domain_allowed(name: str, allowlist: list[str]) -> bool:
    """Network Firewall's domain list semantics: exact names, and .example.com for the domain and its subdomains."""
    name = name.lower().rstrip(".")
    for d in allowlist:
        if d.startswith("."):
            if name == d[1:] or name.endswith(d):
                return True
        elif name == d:
            return True
    return False


def unlisted(seen: list[str], allowlist: list[str]) -> list[str]:
    return sorted({n.lower() for n in seen if n and not domain_allowed(n, allowlist)})


def home_net() -> dict:
    return {"IPSets": {"HOME_NET": {"Definition": [VPC_CIDR]}}}


def allowlist_rule_group(domains: list[str]) -> dict:
    return {
        "RuleVariables": home_net(),
        "RulesSource": {
            "RulesSourceList": {
                "Targets": domains,
                "TargetTypes": ["TLS_SNI", "HTTP_HOST"],
                "GeneratedRulesType": "ALLOWLIST",
            }
        },
        "StatefulRuleOptions": {"RuleOrder": "STRICT_ORDER"},
    }


def allowlist_rule_group_request(domains: list[str]) -> dict:
    if len(domains) * 2 > RG_ALLOW_CAPACITY:
        raise ValueError(f"{len(domains)} domains don't fit the rule group's capacity ({RG_ALLOW_CAPACITY // 2})")
    return {
        "RuleGroupName": RG_ALLOW,
        "Type": "STATEFUL",
        "Capacity": RG_ALLOW_CAPACITY,
        "Description": "Dev box egress allowlist (templates/egress-allowlist.txt)",
        "RuleGroup": allowlist_rule_group(domains),
        "Tags": tag_list(RG_ALLOW),
    }


def firewall_policy_doc(allow_arn: str, default_actions: list[str]) -> dict:
    """Strict order: the allowlist, then drop (and alert on) everything else."""
    return {
        "StatelessDefaultActions": ["aws:forward_to_sfe"],
        "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
        "StatefulRuleGroupReferences": [{"ResourceArn": allow_arn, "Priority": 1}],
        "StatefulDefaultActions": list(default_actions),
        "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"},
    }


def firewall_policy_request(doc: dict) -> dict:
    return {
        "FirewallPolicyName": FIREWALL_POLICY,
        "FirewallPolicy": doc,
        "Description": "Dev box egress: the domain allowlist, then drop everything else",
        "Tags": tag_list(FIREWALL_POLICY),
    }


def policy_uses(doc: dict, rule_group_arn: str | None) -> bool:
    return bool(rule_group_arn) and rule_group_arn in {
        r.get("ResourceArn") for r in doc.get("StatefulRuleGroupReferences", [])
    }


def firewall_request(policy_arn: str, vpc_id: str, subnet_id: str) -> dict:
    return {
        "FirewallName": FIREWALL,
        "FirewallPolicyArn": policy_arn,
        "VpcId": vpc_id,
        "SubnetMappings": [{"SubnetId": subnet_id}],
        "DeleteProtection": False,
        "SubnetChangeProtection": False,
        "FirewallPolicyChangeProtection": False,
        "Description": "Dev box egress firewall (devbox.py network pause deletes it)",
        "Tags": tag_list(FIREWALL),
    }


def firewall_logging_wanted() -> list[dict]:
    return [
        {"LogType": t, "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": FIREWALL_LOG_GROUP}}
        for t in ("ALERT", "FLOW")
    ]


def logging_steps(current: list[dict]) -> list[list[dict]]:
    """UpdateLoggingConfiguration takes one added destination per call: the configurations to send, in order."""
    have = [
        c
        for c in current
        if c.get("LogDestinationType") == "CloudWatchLogs"
        and c.get("LogDestination", {}).get("logGroup") == FIREWALL_LOG_GROUP
    ]
    steps, now = [], list(current)
    for want in firewall_logging_wanted():
        if any(c.get("LogType") == want["LogType"] for c in have):
            continue
        now = [c for c in now if c.get("LogType") != want["LogType"]] + [want]
        steps.append(list(now))
    return steps


def seen_names_query() -> str:
    """Logs Insights: every TLS SNI and HTTP Host the firewall dropped and alerted on (http_host: plain HTTP)."""
    return (
        "fields coalesce(event.tls.sni, event.http.hostname) as name, event.http.hostname as http_host"
        " | filter event.event_type = 'alert' and ispresent(name)"
        " | stats count(*) as hits by name, http_host | sort hits desc | limit 500"
    )


# ---- DNS Firewall (Route 53 Resolver)
def dns_domains(allowlist: list[str]) -> list[str]:
    """The allowlist in DNS Firewall's syntax: `.example.com` (the domain and every subdomain) becomes
    example.com and *.example.com; an exact name stays exact."""
    out: list[str] = []
    for d in allowlist:
        for n in [d[1:], "*" + d] if d.startswith(".") else [d]:
            if n not in out:
                out.append(n)
    if len(out) > DNS_DOMAINS_MAX:
        raise ValueError(f"{len(out)} DNS names don't fit one DNS Firewall update ({DNS_DOMAINS_MAX})")
    return out


def dns_rules_wanted(allow_list_id: str | None, any_list_id: str | None) -> list[dict]:
    """Rule 100 answers the allowlist (and trusts the CNAME chain from there, so AWS's aliases resolve).
    Rule 200 answers NXDOMAIN for every other name."""
    return [
        {
            "FirewallDomainListId": allow_list_id,
            "Name": DNS_RULE_ALLOW,
            "Priority": 100,
            "Action": "ALLOW",
            "FirewallDomainRedirectionAction": "TRUST_REDIRECTION_DOMAIN",
        },
        {
            "FirewallDomainListId": any_list_id,
            "Name": DNS_RULE_ANY,
            "Priority": 200,
            "Action": "BLOCK",
            "BlockResponse": "NXDOMAIN",
        },
    ]


def dns_rule_matches(have: dict, want: dict) -> bool:
    if any(have.get(k) != want[k] for k in ("Name", "Priority", "Action")):
        return False
    if want["Action"] == "BLOCK" and have.get("BlockResponse") != want["BlockResponse"]:
        return False
    return not (
        "FirewallDomainRedirectionAction" in want
        and have.get("FirewallDomainRedirectionAction", "INSPECT_REDIRECTION_DOMAIN")
        != want["FirewallDomainRedirectionAction"]
    )


def dns_blocks_the_rest(rules: list[dict], any_list_id: str | None) -> bool:
    rule = next((r for r in rules if any_list_id and r.get("FirewallDomainListId") == any_list_id), None)
    return (rule or {}).get("Action") == "BLOCK"


def dns_query_log_destination(account: str, region: str) -> str:
    return f"arn:aws:logs:{region}:{account}:log-group:{DNS_LOG_GROUP}:*"


def dns_seen_query() -> str:
    """Logs Insights over the Resolver query log: every name DNS Firewall blocked."""
    return "filter firewall_rule_action = 'BLOCK' | stats count(*) as hits by query_name | sort hits desc | limit 500"


def request_id(what: str) -> str:
    """Route 53 Resolver's CreatorRequestId (an idempotency token for one create)."""
    return f"devbox-{what}-{time.time_ns()}"


# ---- tools gateway
def gateway_request(role_arn: str) -> dict:
    return {
        "name": GATEWAY_NAME,
        "description": "Dev box web search (AWS IAM: the person's ClaudeCode-<tier> session)",
        "roleArn": role_arn,
        "protocolType": "MCP",
        "authorizerType": "AWS_IAM",
        "tags": tags_map(),
    }


def gateway_target_request(gateway_id: str) -> dict:
    return {
        "gatewayIdentifier": gateway_id,
        "name": GATEWAY_TARGET,
        "targetConfiguration": json.loads((TEMPLATES / "agentcore/websearch-target.json").read_text()),
        "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    }


def cedar_statement(account: str, region: str, gateway_id: str) -> str:
    return template(
        "agentcore/allow-websearch.cedar", {"ACCOUNT_ID": account, "REGION": region, "GATEWAY_ID": gateway_id}
    )


def cedar_policy_request(engine_id: str, statement: str) -> dict:
    return {
        "policyEngineId": engine_id,
        "name": CEDAR_RULE,
        "definition": {"cedar": {"statement": statement}},
        "description": "ClaudeCode-<tier> sessions may search the web",
        "validationMode": "IGNORE_ALL_FINDINGS",
    }


def policy_engine_request() -> dict:
    return {"name": POLICY_ENGINE, "description": "Dev box tools gateway: who may use which tool", "tags": tags_map()}


def attach_policy_engine_request(gateway_id: str, role_arn: str, engine_arn: str) -> dict:
    return {
        "gatewayIdentifier": gateway_id,
        "name": GATEWAY_NAME,
        "roleArn": role_arn,
        "protocolType": "MCP",
        "authorizerType": "AWS_IAM",
        "description": gateway_request(role_arn)["description"],
        "policyEngineConfiguration": {"arn": engine_arn, "mode": "ENFORCE"},
    }


def gateway_resource_policy(gateway_arn: str, account: str) -> dict:
    return policy("iam/gateway-resource-policy.json", {"GATEWAY_ARN": gateway_arn, "ACCOUNT_ID": account})


def gateway_host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or ""


# ---- edge
def origin_domain(function_url: str) -> str:
    return urllib.parse.urlparse(function_url).hostname or ""


def lambda_create_request(role_arn: str, image_uri: str, env: dict[str, str]) -> dict:
    return {
        "FunctionName": EDGE_FUNCTION,
        "Role": role_arn,
        "PackageType": "Image",
        "Code": {"ImageUri": image_uri},
        "Architectures": ["arm64"],
        "MemorySize": EDGE_MEMORY_MB,
        "Timeout": EDGE_TIMEOUT_S,
        "Description": "Dev box static edge: loader, scripts and the pinned VS Code web assets (no tokens, no user data)",
        "Environment": {"Variables": env},
        "LoggingConfig": {"LogFormat": "Text", "LogGroup": EDGE_LOG_GROUP},
        "Tags": tags_map(),
    }


def function_url_request() -> dict:
    return {"FunctionName": EDGE_FUNCTION, "AuthType": "AWS_IAM", "InvokeMode": EDGE_INVOKE_MODE}


def oac_request() -> dict:
    return {
        "OriginAccessControlConfig": {
            "Name": OAC_NAME,
            "Description": "Dev box: CloudFront signs every request to the edge Lambda",
            "OriginAccessControlOriginType": "lambda",
            "SigningBehavior": "always",
            "SigningProtocol": "sigv4",
        }
    }


def _behavior(site: str, *, static: bool) -> dict:
    b = {
        "TargetOriginId": EDGE_FUNCTION,
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": {
            "Quantity": 2,
            "Items": ["GET", "HEAD"],
            "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
        },
        "Compress": static,
        "CachePolicyId": CACHING_OPTIMIZED if static else CACHING_DISABLED,
        "SmoothStreaming": False,
        "FieldLevelEncryptionId": "",
        "LambdaFunctionAssociations": {"Quantity": 0},
        "FunctionAssociations": {"Quantity": 0},
        "TrustedSigners": {"Enabled": False, "Quantity": 0},
        "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
    }
    if not static:
        b["OriginRequestPolicyId"] = ALL_VIEWER_EXCEPT_HOST
    if site == "workbench":
        b["ResponseHeadersPolicyId"] = SECURITY_HEADERS
    if static:
        b["PathPattern"] = STATIC_PATH
    return b


def distribution_comment(site: str) -> str:
    return f"devbox {site}"


def api_behavior() -> dict:
    """/api/* on the workbench distribution: the provisioner's HTTP API, never cached; every viewer header but Host
    goes on (the token is in X-Devbox-Token; API Gateway's JWT authorizer checks it)."""
    methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    return {
        "PathPattern": API_PATH,
        "TargetOriginId": API_ORIGIN_ID,
        "ViewerProtocolPolicy": "https-only",
        "AllowedMethods": {"Quantity": 7, "Items": methods, "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
        "Compress": False,
        "CachePolicyId": CACHING_DISABLED,
        "OriginRequestPolicyId": ALL_VIEWER_EXCEPT_HOST,
        "ResponseHeadersPolicyId": SECURITY_HEADERS,
        "SmoothStreaming": False,
        "FieldLevelEncryptionId": "",
        "LambdaFunctionAssociations": {"Quantity": 0},
        "FunctionAssociations": {"Quantity": 0},
        "TrustedSigners": {"Enabled": False, "Quantity": 0},
        "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
    }


def api_origin(api_domain: str) -> dict:
    return {
        "Id": API_ORIGIN_ID,
        "DomainName": api_domain,
        "OriginPath": "",
        "OriginAccessControlId": "",
        "CustomHeaders": {"Quantity": 0},
        "CustomOriginConfig": {
            "HTTPPort": 80,
            "HTTPSPort": 443,
            "OriginProtocolPolicy": "https-only",
            "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            "OriginReadTimeout": 30,
            "OriginKeepaliveTimeout": 5,
        },
    }


def distribution_config(
    site: str, origin: str, oac_id: str, caller_reference: str | None = None, api_domain: str = ""
) -> dict:
    """The static path cached (CachingOptimized, compressed), everything else uncached; the origin
    custom header tells the Lambda which site it's serving. Standard logs off (they'd record query strings).
    The workbench also sends /api/* to the provisioner's HTTP API, once it exists."""
    with_api = site == "workbench" and bool(api_domain)
    origins = [
        {
            "Id": EDGE_FUNCTION,
            "DomainName": origin,
            "OriginPath": "",
            "OriginAccessControlId": oac_id,
            "CustomHeaders": {"Quantity": 1, "Items": [{"HeaderName": SITE_HEADER, "HeaderValue": site}]},
            "CustomOriginConfig": {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "https-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                "OriginReadTimeout": 30,
                "OriginKeepaliveTimeout": 5,
            },
        }
    ] + ([api_origin(api_domain)] if with_api else [])
    behaviors = [_behavior(site, static=True)] + ([api_behavior()] if with_api else [])
    return {
        "CallerReference": caller_reference or f"devbox-{site}",
        "Comment": distribution_comment(site),
        "Enabled": True,
        "DefaultRootObject": "",
        "PriceClass": "PriceClass_100",
        "HttpVersion": "http2and3",
        "IsIPV6Enabled": True,
        "Origins": {"Quantity": len(origins), "Items": origins},
        "DefaultCacheBehavior": {k: v for k, v in _behavior(site, static=False).items() if k != "PathPattern"},
        "CacheBehaviors": {"Quantity": len(behaviors), "Items": behaviors},
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        "Restrictions": {"GeoRestriction": {"RestrictionType": "none", "Quantity": 0}},
        "Logging": {"Enabled": False, "IncludeCookies": False, "Bucket": "", "Prefix": ""},
    }


def distribution_drift(current: dict, desired: dict) -> list[str]:
    out = []
    have = {o["Id"]: o for o in (current.get("Origins") or {}).get("Items") or []}
    for o_w in desired["Origins"]["Items"]:
        o_c = have.pop(o_w["Id"], None)
        if o_c is None:
            out.append(f"origin {o_w['Id']}")
            continue
        for k in ("DomainName", "OriginAccessControlId", "CustomHeaders"):
            norm = lambda v: json.dumps(
                {"Quantity": 0} if v in (None, {"Quantity": 0, "Items": []}) else v, sort_keys=True
            )
            if norm(o_c.get(k)) != norm(o_w.get(k)):
                out.append(f"origin {k}" if o_w["Id"] == EDGE_FUNCTION else f"origin {o_w['Id']} {k}")
    out += [f"origin {i} (not part of the design)" for i in have]
    keys = ("CachePolicyId", "OriginRequestPolicyId", "ResponseHeadersPolicyId", "Compress", "ViewerProtocolPolicy")
    same = lambda a, b, k: (a.get(k) or "") == (b.get(k) or "")
    for k in keys:
        if not same(current["DefaultCacheBehavior"], desired["DefaultCacheBehavior"], k):
            out.append(f"default behavior {k}")
    cb_c = (current.get("CacheBehaviors") or {}).get("Items") or []
    cb_w = desired["CacheBehaviors"]["Items"]
    if len(cb_c) != len(cb_w) or any(
        not same(cb_c[i], cb_w[i], k) for i in range(len(cb_w)) for k in keys + ("PathPattern", "TargetOriginId")
    ):
        out.append("static behavior" if len(cb_w) == 1 and len(cb_c) <= 1 else "path behaviors (static, /api/*)")
    if current.get("Enabled") is not True:
        out.append("enabled")
    if (current.get("Logging") or {}).get("Enabled"):
        out.append("standard logging (it records query strings)")
    return out


def lambda_permission_requests(site: str, distribution_arn: str) -> list[dict]:
    """Both InvokeFunctionUrl and InvokeFunction, for CloudFront, from this distribution only."""
    return [
        {
            "FunctionName": EDGE_FUNCTION,
            "StatementId": f"cloudfront-{site}-url",
            "Action": "lambda:InvokeFunctionUrl",
            "Principal": "cloudfront.amazonaws.com",
            "SourceArn": distribution_arn,
        },
        {
            "FunctionName": EDGE_FUNCTION,
            "StatementId": f"cloudfront-{site}-invoke",
            "Action": "lambda:InvokeFunction",
            "Principal": "cloudfront.amazonaws.com",
            "SourceArn": distribution_arn,
        },
    ]


def permission_present(statements: list[dict], req: dict) -> bool:
    for st in statements:
        if st.get("Sid") != req["StatementId"]:
            continue
        action = st.get("Action")
        src = ((st.get("Condition") or {}).get("ArnLike") or {}).get("AWS:SourceArn")
        principal = (st.get("Principal") or {}).get("Service")
        return action == req["Action"] and src == req["SourceArn"] and principal == req["Principal"]
    return False


def browser_config(s: Settings, webview_domain: str) -> dict:
    """/devbox-config.json: non-secret, and the same for everyone. It names nobody: the page asks
    POST /api/box for the signed-in person's box, which the provisioner makes on their first visit."""
    return {
        "region": s.region,
        "commit": COMMIT,
        "serverRoot": SERVER_ROOT,
        "agentcoreBase": f"https://bedrock-agentcore.{s.region}.amazonaws.com",
        "okta": {"issuer": s.okta_issuer, "clientId": s.okta_client_id, "scopes": OKTA_SCOPES},
        "webviewOrigin": f"https://{webview_domain}" if webview_domain else "",
        "provision": {"path": PROVISION_PATH, "header": TOKEN_HEADER},
    }


PROVISIONER_VENDORED = ("boto3", "botocore", "s3transfer", "jmespath", "dateutil", "urllib3", "six")
PROVISIONER_SERVICES = (
    "bedrock-agentcore-control",
    "efs",
    "iam",
    "dynamodb",
    "sts",
)  # the API models it ships (of ~400)


def provisioner_zip() -> bytes:
    """The provisioner's code: devbox.py (its builders and advance_box), provisioner.py and the templates, plus the boto3
    this deploy runs with (Lambda's own copy is older than the AgentCore and EFS APIs a box needs). The same files
    give the same bytes, so deploy updates the function only when something in it changed."""
    import importlib
    import io
    import zipfile

    files = [("devbox.py", HERE / "devbox.py"), ("provisioner.py", HERE / "provisioner.py")]
    files += [
        (f"templates/{p.relative_to(TEMPLATES).as_posix()}", p) for p in sorted(TEMPLATES.rglob("*")) if p.is_file()
    ]
    for name in PROVISIONER_VENDORED:
        mod = importlib.import_module(name)
        root = Path(mod.__file__).parent
        if Path(mod.__file__).name == "__init__.py":  # a package (six is one file, though it sets __path__)

            def wanted(rel, name=name):
                # botocore/data/<service>/…: only the services the provisioner calls
                parts = rel.parts
                return not (
                    name == "botocore"
                    and len(parts) > 2
                    and parts[0] == "data"
                    and parts[1] not in PROVISIONER_SERVICES
                )

            files += [
                (f"{name}/{p.relative_to(root).as_posix()}", p)
                for p in sorted(root.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts and wanted(p.relative_to(root))
            ]
        else:
            files.append((Path(mod.__file__).name, Path(mod.__file__)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, path in files:
            info = zipfile.ZipInfo(arc, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr, info.compress_type = 0o644 << 16, zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    return buf.getvalue()


def code_sha256(code: bytes) -> str:
    """Lambda's CodeSha256: base64 of the zip's sha256."""
    import base64

    return base64.b64encode(hashlib.sha256(code).digest()).decode()


def provisioner_env(s: Settings, plan: BoxPlan) -> dict[str, str]:
    env = {
        "DEVBOX_SETTINGS": json.dumps(settings_env(s), separators=(",", ":"), sort_keys=True),
        "DEVBOX_PLAN": json.dumps(plan.__dict__, separators=(",", ":"), sort_keys=True),
    }
    if sum(len(k) + len(v) for k, v in env.items()) > 4096:
        raise ValueError("the provisioner's environment is over Lambda's 4 KB limit")
    return env


def provisioner_create_request(role_arn: str, code: bytes, env: dict[str, str]) -> dict:
    return {
        "FunctionName": PROVISIONER_FUNCTION,
        "Runtime": PROVISIONER_RUNTIME,
        "Handler": PROVISIONER_HANDLER,
        "Role": role_arn,
        "Code": {"ZipFile": code},
        "Architectures": ["arm64"],
        "MemorySize": PROVISIONER_MEMORY_MB,
        "Timeout": PROVISIONER_TIMEOUT_S,
        "Description": "Dev box provisioner: makes a group member's box on their first visit (POST /api/box)",
        "Environment": {"Variables": env},
        "LoggingConfig": {"LogFormat": "Text", "LogGroup": PROVISIONER_LOG_GROUP},
        "Tags": tags_map(),
    }


def provisioner_spec(
    account: str, region: str, file_system_id: str, boundary_arn: str, *, subnet_id: str, security_group_id: str
) -> dict:
    """CreateAgentRuntime has no resource type (the Service Authorization Reference lists none), so it's granted on "*" and
    narrowed by its condition keys instead: our tag, and the box subnet and security group. CreateAgentRuntime also
    makes the runtime's DEFAULT endpoint as the caller, checked as CreateAgentRuntimeEndpoint on the literal resource
    "runtime/*" (the new runtime has no id yet), so that one can't be narrowed to devbox_vm_* (seen live). And it copies
    the runtime's tags onto the endpoint and the workload identity, each checked as TagResource as the caller (for the
    workload identity, on its directory too: CreateWorkloadIdentity is the one call AgentCore makes as the caller, seen in
    CloudTrail): allowed only for our two tag keys, with devbox=remote-dev-box."""
    v = {
        "ACCOUNT_ID": account,
        "REGION": region,
        "FILE_SYSTEM_ARN": file_system_arn(account, region, file_system_id),
        "BOUNDARY_ARN": boundary_arn,
        "TAG_KEY": TAG_KEY,
        "TAG_VALUE": TAG_VALUE,
        "USER_TAG_KEY": USER_TAG_KEY,
        "BOX_SUBNET_ID": subnet_id,
        "BOX_SECURITY_GROUP_ID": security_group_id,
    }
    return {
        "trust": policy("iam/provisioner-trust.json", v),
        "managed": [],
        "inline": {"make-boxes-for-group-members": policy("iam/provisioner-policy.json", v)},
        "description": "Dev box: the provisioner (a group member's folder, role and runtime; execution roles only with the boundary)",
    }


def exec_boundary(account: str, region: str, file_system_id: str) -> dict:
    return policy(
        "iam/exec-boundary.json",
        {"ACCOUNT_ID": account, "REGION": region, "FILE_SYSTEM_ARN": file_system_arn(account, region, file_system_id)},
    )


def box_table_request() -> dict:
    return {
        "TableName": BOX_TABLE,
        "AttributeDefinitions": [{"AttributeName": "key", "AttributeType": "S"}],
        "KeySchema": [{"AttributeName": "key", "KeyType": "HASH"}],
        "BillingMode": "PAY_PER_REQUEST",
        "DeletionProtectionEnabled": True,
        "Tags": tag_list(),
    }


def api_authorizer_request(api_id: str, s: Settings) -> dict:
    """API Gateway's JWT authorizer: the Okta signature, issuer, audience and expiry (the route adds the devbox scope).
    The provisioner itself checks the client id, the groups and the uid."""
    return {
        "ApiId": api_id,
        "AuthorizerType": "JWT",
        "Name": "okta",
        "IdentitySource": [f"$request.header.{TOKEN_HEADER}"],
        "JwtConfiguration": {"Issuer": s.okta_issuer, "Audience": [s.okta_audience]},
    }


def api_integration_request(api_id: str, function_arn: str) -> dict:
    return {
        "ApiId": api_id,
        "IntegrationType": "AWS_PROXY",
        "IntegrationUri": function_arn,
        "PayloadFormatVersion": "2.0",
        "TimeoutInMillis": 29000,
    }


def api_route_request(api_id: str, authorizer_id: str, integration_id: str) -> dict:
    return {
        "ApiId": api_id,
        "RouteKey": PROVISION_ROUTE,
        "AuthorizationType": "JWT",
        "AuthorizerId": authorizer_id,
        "AuthorizationScopes": [DEVBOX_SCOPE],
        "Target": f"integrations/{integration_id}",
    }


def api_permission_request(account: str, region: str, api_id: str) -> dict:
    return {
        "FunctionName": PROVISIONER_FUNCTION,
        "StatementId": "apigateway-devbox-api",
        "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": f"arn:aws:execute-api:{region}:{account}:{api_id}/*/POST/api/box",
    }


def file_system_policy_static(file_system_arn: str) -> dict:
    """The file system policy names nobody. Every mount needs TLS; nobody gets root; and with a policy in
    effect EFS grants an anonymous client nothing. Each person's execution role allows only their own access point
    (exec-policy.json), and EFS allows what either the role's policy or this one allows (efs/ug iam-access-control-nfs-efs),
    so a new person needs no change here."""
    return {
        "Version": "2012-10-17",
        "Id": "devbox-tls-only-no-root",
        "Statement": [
            {
                "Sid": "TlsOnly",
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": [*EFS_CLIENT_ACTIONS, "elasticfilesystem:ClientRootAccess"],
                "Resource": file_system_arn,
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
            {
                "Sid": "NoRootForAnyone",
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": "elasticfilesystem:ClientRootAccess",
                "Resource": file_system_arn,
            },
        ],
    }


def lambda_env(config: dict, workbench_domain: str, webview_domain: str) -> dict[str, str]:
    env = {
        "DEVBOX_CONFIG_JSON": json.dumps(config, separators=(",", ":"), sort_keys=True),
        "WORKBENCH_ORIGIN": f"https://{workbench_domain}" if workbench_domain else "",
        "WEBVIEW_ORIGIN": f"https://{webview_domain}" if webview_domain else "",
    }
    if sum(len(k) + len(v) for k, v in env.items()) > 4096:
        raise ValueError("the edge Lambda's environment is over Lambda's 4 KB limit")
    return env


# ---- the printed guides
def okta_steps(s: Settings, workbench_domain: str | None) -> str:
    wb = f"https://{workbench_domain}" if workbench_domain else "https://<workbench distribution>.cloudfront.net"
    client = (
        f"(client id in devbox.env: {s.okta_client_id})"
        if s.okta_client_id
        else "→ copy its Client ID into devbox.env as DEVBOX_OKTA_CLIENT_ID, then run deploy again.\n"
        "      Until then the site answers with an error (the edge won't start without it) and the boxes have no runtime."
    )
    tiers = " or ".join(f"{g} ({t})" for t, g in sorted(s.tier_groups.items()))
    claim_re = "^(" + "|".join(re.escape(g) for g in [s.okta_group, *sorted(s.tier_groups.values())]) + ")$"
    return f"""\
Okta, by hand (admin console of {s.okta_domain}). Nobody is named anywhere: the groups decide.
 1. Directory › Groups › Add group: {s.okta_group}. Everyone in it gets a box the first time they open the workbench.
    Put each of them in exactly one tier group as well: {tiers}. They're the groups that grant ClaudeCode-<tier>
    in Identity Center (pushed from Okta), so the box's models and the person's AWS role always agree.
 2. Applications › Create App Integration › OIDC › Single-Page Application, name "Dev Box":
      Grant types            Authorization Code, Refresh Token (rotate token after every use)
      Sign-in redirect URI   {wb}/callback
      Sign-out redirect URI  {wb}/
      Assignments            only the group {s.okta_group}
      DPoP                   off ("Require Demonstrating Proof of Possession" unticked)
    {client}
 3. Security › API › Trusted Origins › Add origin: {wb}
      Type: CORS and Redirect. Exactly this origin, never a wildcard (cloudfront.net is a public suffix).
 4. Security › API › Authorization Servers › {s.okta_auth_server}:
      Scopes   › Add scope: {DEVBOX_SCOPE}
      Claims   › Add claim: client_id · Access Token · Expression · app.clientId · include in scope {DEVBOX_SCOPE}
      Claims   › Add claim: groups · Access Token · Groups · filter Matches regex {claim_re} · include in scope {DEVBOX_SCOPE}
               (the authorizers need {s.okta_group}; the provisioner reads the tier group)
      Access Policies › Add policy "Dev Box", assigned to the Dev Box client, with one rule:
               group {s.okta_group}; grant types Authorization Code + Refresh Token;
               scopes exactly: {OKTA_SCOPES};
               access token 60 minutes; refresh token 12 hours, expires if not used for 2 hours
               Put it at priority 1 (above the Default Policy). No rule in this server may say "Any scopes":
               a lower-priority catch-all can still mint the token.
 5. Token Preview (same authorization server), client Dev Box, grant type Authorization Code, scopes {OKTA_SCOPES}:
      for a {s.okta_group} member: scp has {DEVBOX_SCOPE}, groups has {s.okta_group} and their one tier group, client_id = the Dev
      Box app, and uid is present. For someone not in {s.okta_group} the preview must be denied.
 6. The provisioner ({PROVISION_PATH}) and each box's runtime accept only a token with aud {s.okta_audience}, client_id = the Dev
    Box app, scope {DEVBOX_SCOPE} and groups containing {s.okta_group}; a runtime also only its owner's uid. Someone new just opens
    the workbench: the page shows "Setting up your dev box" while the provisioner makes it (a few minutes, once)."""


def okta_domain_change(old: str, new: str) -> str:
    """The workbench distribution was made again (after an undeploy), so it has a new domain. Okta's
    redirect URIs and Trusted Origin are exact matches (never a wildcard), so sign-in breaks until
    they're changed."""
    return (
        f"The workbench domain changed from {old} to {new}. In Okta (the Dev Box app and Security › API):\n"
        f"      Sign-in redirect URI   https://{old}/callback  →  https://{new}/callback\n"
        f"      Sign-out redirect URI  https://{old}/  →  https://{new}/\n"
        f"      Trusted Origin         https://{old}  →  https://{new}\n"
        "    Until then sign-in fails (a redirect_uri error, or CORS on the token request)."
    )


def spike_checklist(
    s: Settings, boxes: dict[str, dict], workbench_domain: str | None, box_image: str | None = None
) -> str:
    """What only the live account can tell. deploy/README.md › Spike checklist has the same items, with the why."""
    arn = next((b["runtimeArn"] for b in boxes.values() if b.get("runtimeArn")), "<runtime ARN>")
    base = f"https://bedrock-agentcore.{s.region}.amazonaws.com/runtimes/{urllib.parse.quote(arn, safe='')}"
    wb = f"https://{workbench_domain}" if workbench_domain else "the workbench URL"
    image = box_image or f"<account>.dkr.ecr.{s.region}.amazonaws.com/{ECR_BOX}:<tag>"
    return f"""\
Spike checklist (deploy/README.md › Spike checklist: what only the live account can tell).
In the signed-in page's console: T = __devbox.getToken() (the owner's Okta access token), S = __devbox.sessionId.
 1. op: diag. Sign in at {wb}, then in the page's console:
      fetch(__devbox.agentcoreBase+'/runtimes/'+encodeURIComponent(__devbox.runtimeArn)+'/invocations?qualifier=DEFAULT',
        {{method:'POST',headers:{{Authorization:'Bearer '+__devbox.getToken(),'Content-Type':'application/json',
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id':__devbox.sessionId}},body:'{{"v":1,"op":"diag"}}'}}).then(r=>r.json())
    Check the header names on /invocations and the /ws upgrade (is authorization there?), and, new on microVM, the uid,
    the capabilities and user namespaces: does the box run multi-user (dev 1000, proxy 1001) or single-user?
 2. The resource policy (which allows the terminal), with the owner's own token. Each must be refused (403):
      curl -s -XPOST "{base}/commands?qualifier=DEFAULT" -H "Authorization: Bearer $T" \\
        -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $S" -H 'Content-Type: application/json' -d '{{"command":"id"}}'
      curl -s -XPOST "{base}/stopruntimesession?qualifier=DEFAULT" -H "Authorization: Bearer $T" \\
        -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $S"
    …while the workbench (InvokeAgentRuntime + /ws) and the terminal (/ws/shells, item 6) work for the owner, and another
    person's token is refused on all of them (the authorizer's uid EQUALS the owner).
 3. EFS mount works. The first invoke of a new session must not end in a 424 (a failed mount; each mount has 30 s). op: diag's
    stat of {MOUNT_PATH}: 1000:1000, mode 0750 (the access point {EFS_ROOT}/<name>); dev must write home/ and projects/, and
    `ls -la {MOUNT_PATH}/..` shows only this person's folder. A 424: security groups (TCP {NFS_PORT} devbox-box → devbox-efs),
    the mount target `available` in the box subnet, the mount target's name on the allowlist
    (<az-id>.<fs-id>.efs.{s.region}.amazonaws.com), and the execution role devbox-exec-<name> (README › Troubleshooting).
 4. Blocked domains. After a full session (cold start, aws sso login, a Claude turn, a web search, a reconnect),
    `network allowlist` lists every name the firewalls blocked (TLS, HTTP and DNS) that the allowlist doesn't have.
 5. Image size. `docker image ls {image}` (on disk; `docker image inspect --format '{{{{.Size}}}}'` gives only the compressed size)
    must stay under AgentCore's 2 GB image limit.
 6. Terminal opens. `uv run deploy/shell-probe.py <user>` (after copy(__devbox.getToken()) in the tab): a STATUS frame with the
    shellId, then the output of `id`. Note the shell's uid (root or not) and that it doesn't get the box's HOME or AWS_PROFILE.
 7. MMDSv2. deploy says whether metadataConfiguration.requireMMDSV2 was accepted for a microVM runtime.
 8. /ws works on microVM. The workbench connects (no "cannot reconnect"), and `deploy/ws-probe.sh <user>` shows HTTP 101 for the
    browser's way (token in Sec-WebSocket-Protocol). On Instances this was the 424 that moved the boxes to microVM.
 9. Idle stop (the cost model). Close every tab: about {s.vm_idle_seconds} s later the session ends (microVM idle timeout), and
    the next visit is a cold start (the loader shows the wait). There's no instance to watch on microVM.
10. Resume after an idle stop, or after the 8-hour maximum lifetime. Reopen the page: the files are back (they're on EFS),
    and devbox-claude resumes the last Claude Code session; processes and tmux don't survive a new microVM.
11. The real 60-minute WebSocket cutoff. Keep a tab open for over an hour: VS Code reconnects with no dialog, and the
    terminal (and anything in tmux) survives.
12. Cold start. On a new session, note how long the loader waits for the box and any 409 / 424 / timeout it saw
    (its request timeout is 60 s; InvokeAgentRuntime isn't idempotent). The EFS mount is part of every cold start.
13. undeploy, then deploy. Write a marker file in ~/ first; after the redeploy it must still be there (a plain undeploy keeps
    the EFS file system, its access points and the mount target; the new runtime mounts the same folder).
14. Web search. After `aws sso login` in the box, the web-search MCP server connects without running /mcp.
15. DNS Firewall. `getent hosts example.com` in the box answers nothing while an allowlisted name resolves
    (and a new session still mounts EFS), and the VPC's DNS queries reach {DNS_LOG_GROUP}.
16. Port 80. `curl -m 5 http://example.com` in the box can't connect (only 443 leaves the subnet).
17. Sign-out. Run __devbox.signOut() in the page's console (or open {wb}/#signout), then reload twice: the Okta
    sign-in page must appear (no silent prompt=none sign-in). That proves the org accepted the form POST to /v1/logout."""


# ============================================================================= AWS access
class Aws:
    """boto3 clients for the two admin profiles; tests hand in fake clients instead."""

    def __init__(self, s: Settings, clients: dict | None = None):
        self.s = s
        self._fixed = clients
        self._cache: dict = {}
        self._cfg = Config(retries={"mode": "standard", "max_attempts": 8}, read_timeout=120)

    def client(self, service: str, org: bool = False, region: str | None = None):
        if self._fixed is not None:
            return self._fixed[("org:" if org else "") + service]
        key = (service, org, region)
        if key not in self._cache:
            sess = boto3.Session(profile_name=self.s.org_profile if org else self.s.ai_profile)
            self._cache[key] = sess.client(
                service, region_name=region or (self.s.idc_region if org else self.s.region), config=self._cfg
            )
        return self._cache[key]

    def __getattr__(self, name: str):
        services = {
            "ec2": "ec2",
            "iam": "iam",
            "ecr": "ecr",
            "logs": "logs",
            "nfw": "network-firewall",
            "lam": "lambda",
            "cf": "cloudfront",
            "acc": "bedrock-agentcore-control",
            "acd": "bedrock-agentcore",
            "sts": "sts",
            "r53r": "route53resolver",
            "efs": "efs",
            "ddb": "dynamodb",
            "apigw": "apigatewayv2",
        }
        if name in services:
            return self.client(services[name])
        raise AttributeError(name)


def paged(call, key: str, token: str = "nextToken", *, send: str | None = None, **kw) -> list:
    """Every page of a list call. `token` is the response's next-page field; `send` is the request's, when it has
    another name (EFS DescribeMountTargets answers NextMarker and takes Marker)."""
    out: list = []
    while True:
        r = call(**kw)
        out += r.get(key) or []
        nxt = r.get(token)
        if not nxt:
            return out
        kw[send or token] = nxt


@dataclass
class Ctx:
    s: Settings
    aws: Aws
    state: dict
    check: bool = False
    account: str = ""
    mgmt_account: str = ""
    identity_store: str = ""
    start_url: str = ""
    uids: dict[str, str] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    net: dict[str, str | None] = field(default_factory=dict)
    efs: dict = field(default_factory=dict)  # id, arn, mountTargetId, aps: {name: {id, arn}}
    gateway: dict[str, str] = field(default_factory=dict)
    edge: dict[str, str] = field(default_factory=dict)
    plan: BoxPlan | None = None  # what every box shares, once deploy has made it (step 6)
    notes: list[str] = field(default_factory=list)  # printed again right before the Okta steps


def change(ctx: Ctx, what: str, fn, *args, **kwargs):
    """Make one change, or in check mode only say it would be made."""
    Report.changes += 1
    indent, what = what[: len(what) - len(what.lstrip())], what.lstrip()
    if ctx.check:
        todo(f"{indent}would {what}")
        return None
    try:
        out = fn(*args, **kwargs)
    except ClientError as e:
        die(f"{what} failed: {err_text(e)}")
    did(f"{indent}{what}")
    return out


def pending(ctx: Ctx, what: str) -> None:
    """A change that needs something not made yet: listed in check mode, a problem in a real run."""
    Report.changes += 1
    indent, what = what[: len(what) - len(what.lstrip())], what.lstrip()
    if ctx.check:
        todo(f"{indent}would {what}")
    else:
        bad(f"{indent}couldn't {what}: something it needs is missing (see above)")


def retry_iam(fn, *args, **kwargs):
    """A new IAM role takes a few seconds before other services can use it (or name it as a principal)."""
    for i in range(10):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            msg = err_text(e)
            if (
                i < 9
                and err_code(e)
                in (
                    "ValidationException",
                    "InvalidParameterValueException",
                    "AccessDeniedException",
                    "InvalidPolicyException",
                )
                and re.search(r"role|assum|trust|permission|principal", msg, re.IGNORECASE)
            ):
                SLEEP(8)
                continue
            raise


def retry_in_use(fn, *args, **kwargs):
    """A delete that AWS refuses for a while after what used the resource is gone (live 2026-10-02: a firewall rule
    group stays "still in use" for a moment after its firewall policy is deleted). Up to about 5 minutes."""
    for _ in range(30):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            if not (
                err_code(e) in ("InvalidOperationException", "DependencyViolation", "ResourceInUseException")
                and "in use" in err_text(e).lower()
            ):
                raise
            SLEEP(10)
    return fn(*args, **kwargs)


def wait_for(what: str, probe, done, *, failed=lambda v: False, timeout: int = 900, every: int = 10, reason=None):
    """Poll probe() until done(value). A probe that raises a not-found error counts as the value None.
    reason() (optional) says why, from the resource itself, when it fails or times out."""

    def why() -> str:
        if reason is None:
            return ""
        try:
            r = reason()
        except ClientError as e:
            r = err_text(e)
        return f" ({r})" if r else ""

    last = None
    for _ in range(timeout // every + 1):
        try:
            last = probe()
        except ClientError as e:
            if not is_missing(e):
                raise
            last = None
        if done(last):
            return last
        if failed(last):
            die(f"{what}: {last}{why()}")
        SLEEP(every)
    die(f"{what}: still {last!r} after {timeout // 60} minutes{why()}")


def wait_waiter(client, name: str, what: str, reason, **kw) -> None:
    """A boto3 waiter whose failure is reported like every other failure (with the service's own reason),
    not as a traceback."""
    try:
        client.get_waiter(name).wait(**kw)
    except WaiterError as e:
        try:
            r = reason()
        except ClientError as e2:
            r = err_text(e2)
        die(f"{what}: {r or e}")


def failed_status(v) -> bool:
    """AgentCore's failure states: *_FAILED, FAILED, and the gateway's and target's *_UNSUCCESSFUL."""
    return bool(v) and ("FAIL" in v or "UNSUCCESSFUL" in v)


def load_state() -> dict:
    try:
        return migrate_state(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ============================================================================= 0 prerequisites
def fetch_json(url: str) -> dict:
    if not url.startswith("https://"):  # only Okta's discovery document is fetched, and only over TLS
        raise ValueError(f"refusing to fetch {url}: only https:// URLs")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=10) as r:
        return json.load(r)


def has_microvm_efs_api() -> bool:
    """CreateAgentRuntime with an EFS access point (and the capacity provider APIs retire-instances uses)."""
    model = botocore.loaders.create_loader().load_service_model("bedrock-agentcore-control", "service-2")
    fs = model["shapes"].get("FilesystemConfiguration", {}).get("members", {})
    return "efsAccessPoint" in fs and "DeleteCapacityProvider" in model["operations"]


def docker_ready() -> bool:
    return (
        bool(shutil.which("docker"))
        and subprocess.run(["docker", "buildx", "version"], capture_output=True, check=False).returncode == 0
    )


def prerequisites(ctx: Ctx, *, need_docker: bool) -> None:
    s = ctx.s
    section("0 · Prerequisites (read-only)")
    for e in s.errors:
        bad(f"devbox.env: {e}")
    for w in s.warnings:
        warn(f"devbox.env: {w}")
    if need_docker:
        if docker_ready():
            ok("docker with buildx")
        elif ctx.check:
            warn("docker with buildx isn't available: deploy needs it to build the images")
        else:
            bad("docker with buildx isn't available (Docker Desktop, then run again)")
    if not has_microvm_efs_api():
        bad(
            "this boto3 can't give a runtime an EFS access point; run with `uv run`, which installs the pinned boto3 1.43.108"
        )
    if Report.problems:
        die("Fix the problems above, then run again.")

    who = {}
    for label, org in (("AI account", False), ("management account", True)):
        prof = s.org_profile if org else s.ai_profile
        try:
            arn = ctx.aws.client("sts", org=org).get_caller_identity()["Arn"]
        except Exception as e:  # noqa: BLE001 - no credentials, expired SSO session, unknown profile
            bad(f"profile {prof} can't sign in ({type(e).__name__}): refresh its credentials")
            continue
        name = arn.split(":", 5)[-1]
        name = name.split("/")[1] if name.startswith("assumed-role/") else name
        if name.startswith("AWSReservedSSO_ClaudeCode-"):
            bad(f"profile {prof} is a demo person's role ({name}); use an admin profile")
            continue
        who[org] = arn.split(":")[4]
        ok(f"{prof} → {label} {who[org]} (as {name})")
    if Report.problems:
        die("Fix the problems above, then run again.")
    ctx.account, ctx.mgmt_account = who[False], who[True]
    ctx.state.setdefault("account", ctx.account)
    if ctx.state["account"] != ctx.account:
        die(f".state.json belongs to account {ctx.state['account']}, but {s.ai_profile} is {ctx.account}")

    sso = ctx.aws.client("sso-admin", org=True)
    try:
        inst = (sso.list_instances().get("Instances") or [None])[0]
    except ClientError as e:
        die(f"can't read IAM Identity Center with {s.org_profile}: {err_text(e)}")
    if not inst:
        die(f"no IAM Identity Center instance in {s.idc_region}")
    ctx.identity_store = inst["IdentityStoreId"]
    ctx.start_url = s.idc_start_url or f"https://{ctx.identity_store}.awsapps.com/start"
    ok(f"Identity Center {inst['InstanceArn']}, sign-in {ctx.start_url}")
    sets = set()
    for ps in paged(sso.list_permission_sets, "PermissionSets", "NextToken", InstanceArn=inst["InstanceArn"]):
        sets.add(
            sso.describe_permission_set(InstanceArn=inst["InstanceArn"], PermissionSetArn=ps)["PermissionSet"]["Name"]
        )
    for tier in sorted(s.tier_groups):
        if f"ClaudeCode-{tier}" in sets:
            ok(f"permission set ClaudeCode-{tier} (the box's sign-in role for {tier})")
        else:
            bad(f"permission set ClaudeCode-{tier} is missing: create it (IDENTITY-SETUP.md, part 3)")

    # Nobody is listed: the groups decide. Each tier group should also be the one that grants ClaudeCode-<tier> in
    # Identity Center (pushed from Okta), so the box's tier and the person's AWS role can't disagree.
    ids = ctx.aws.client("identitystore", org=True)
    for group, what in [
        (s.okta_group, "who gets a box"),
        *((g, f"the {t} tier") for t, g in sorted(s.tier_groups.items())),
    ]:
        try:
            ids.get_group_id(
                IdentityStoreId=ctx.identity_store,
                AlternateIdentifier={"UniqueAttribute": {"AttributePath": "displayName", "AttributeValue": group}},
            )
            ok(f"group {group} ({what}) is in Identity Center")
        except ClientError as e:
            if not is_missing(e):
                warn(f"couldn't look group {group} up in Identity Center: {err_text(e)}")
            elif group == s.okta_group:
                ok(f"group {group} ({what}): an Okta group; it needn't be in Identity Center")
            else:
                warn(
                    f"group {group} ({what}) isn't in Identity Center: push it from Okta, and assign it ClaudeCode-{what.split()[1]}"
                )

    try:
        issuer = fetch_json(s.discovery_url).get("issuer")
    except Exception as e:  # noqa: BLE001 - any failure is reported as a check result
        issuer = None
        bad(f"can't read {s.discovery_url} ({type(e).__name__}): check OKTA_DOMAIN / OKTA_AUTH_SERVER")
    if issuer and issuer != s.okta_issuer:
        bad(f"Okta says its issuer is {issuer}, not {s.okta_issuer}")
    elif issuer:
        ok(f"Okta authorization server {issuer}")
    if s.okta_client_id:
        ok(f"Okta Dev Box app: client {s.okta_client_id}")
    else:
        warn(
            "DEVBOX_OKTA_CLIENT_ID is empty: the boxes' runtimes wait for it (deploy prints the Okta steps with the real URL)"
        )

    supported = MICROVM_AZ_IDS.get(s.region, ())
    try:
        zone = (ctx.aws.ec2.describe_availability_zones(ZoneNames=[s.az]).get("AvailabilityZones") or [{}])[0].get(
            "ZoneId"
        )
        if zone in supported:
            ok(f"{s.az} is {zone}: AgentCore microVM VPC mode supports it ({', '.join(supported)})")
        else:
            bad(
                f"{s.az} is {zone}, and AgentCore microVM VPC mode in {s.region} supports only {', '.join(supported)} "
                "(docs: agentcore-vpc.html › Supported Availability Zones): pick another DEVBOX_AZ"
            )
    except ClientError as e:
        warn(f"couldn't look up {s.az}'s zone id: {err_text(e)}")
    if Report.problems:
        die(f"{Report.problems} prerequisite(s) missing. Fix them, then run again.")


# ============================================================================= 1 images
ALWAYS_SKIP = {".git", ".DS_Store"}


def dockerignore_rules(root: Path) -> list[tuple[bool, re.Pattern]]:
    """.dockerignore as Docker reads it: patterns from the context root, `*` and `?` within one path
    segment, `**` across segments, `!` re-includes, and the last matching line wins."""
    rules = []
    f = root / ".dockerignore"
    for line in f.read_text().splitlines() if f.exists() else []:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        parts = [p for p in line.lstrip("!").strip().strip("/").split("/") if p not in ("", ".")]
        rx = ""
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if part == "**":
                rx += ".*" if last else "(?:[^/]+/)*"
                continue
            rx += "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in part) + (
                "" if last else "/"
            )
        rules.append((neg, re.compile(rx + r"\Z")))
    return rules


def docker_excludes(rel: str, rules: list[tuple[bool, re.Pattern]]) -> bool:
    # A pattern that matches a folder also applies to everything in it.
    parts = rel.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    out = False
    for neg, rx in rules:
        if any(rx.match(p) for p in prefixes):
            out = not neg
    return out


def context_files(root: Path) -> list[Path]:
    """The files (and symlinks) a `COPY .` would see: the build context after .dockerignore."""
    rules = dockerignore_rules(root)
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in ALWAYS_SKIP for part in rel.parts) or not (p.is_file() or p.is_symlink()):
            continue
        if not docker_excludes(rel.as_posix(), rules):
            out.append(p)
    return out


def context_hash(root: Path) -> str:
    """The image tag: a hash of exactly what Docker sends as the build context (plus the Dockerfile and
    .dockerignore, which shape the build even when excluded), so an unchanged component is never rebuilt
    or pushed, and any change that reaches the image gets a new tag."""
    h = hashlib.sha256(f"linux/arm64 {COMMIT}\n".encode())
    special = [root / n for n in ("Dockerfile", ".dockerignore") if (root / n).is_file()]
    for p in sorted(set(context_files(root)) | set(special)):
        r = p.relative_to(root).as_posix()
        if p.is_symlink():
            h.update(f"L {r} {os.readlink(p)}\n".encode())
        else:
            h.update(f"F {r} {p.stat().st_mode & 0o111:o} {hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()[:20]


def prebuild(context_dir: Path, cmd: list[str]) -> None:
    log = Path(tempfile.gettempdir()) / f"devbox-prebuild-{context_dir.name}.log"
    say(f"      running {context_dir.name}/{' '.join(cmd)} (log: {log})")
    with open(log, "w") as out:
        r = subprocess.run(cmd, cwd=context_dir, stdout=out, stderr=subprocess.STDOUT, check=False)
    if r.returncode:
        tail = "\n".join(log.read_text().splitlines()[-25:])
        raise RuntimeError(f"{context_dir.name}/{' '.join(cmd)} failed:\n{tail}")


def docker_host() -> str:
    r = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def build_and_push(context_dir: Path, image_uri: str, registry: str, token: str) -> int | None:
    """Build for linux/arm64 with the user's own Docker setup, push with a throwaway credential file
    (so ~/.docker is never written). No provenance/SBOM: Lambda and AgentCore want a plain image manifest."""
    log = Path(tempfile.gettempdir()) / f"devbox-build-{image_uri.rsplit('/', 1)[-1].split(':')[0]}.log"
    say(f"      building {image_uri.rsplit('/', 1)[-1]} (log: {log})")
    with open(log, "w") as out:
        r = subprocess.run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/arm64",
                "--provenance=false",
                "--sbom=false",
                "--load",
                "-t",
                image_uri,
                str(context_dir),
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if r.returncode:
        tail = "\n".join(log.read_text().splitlines()[-25:])
        raise RuntimeError(f"docker build failed:\n{tail}")
    with tempfile.TemporaryDirectory(prefix="devbox-docker-") as cfg:
        Path(cfg, "config.json").write_text(json.dumps(docker_auth_config(registry, token)))
        env = dict(os.environ)
        host = docker_host()
        if host:
            env["DOCKER_HOST"] = host
        r = subprocess.run(
            ["docker", "--config", cfg, "push", image_uri], capture_output=True, text=True, env=env, check=False
        )
    if r.returncode:
        raise RuntimeError(f"docker push failed: {(r.stderr or r.stdout).strip()[-2000:]}")
    return local_image_size(image_uri)


def local_image_size(image_uri: str) -> int | None:
    """The larger of the two sizes Docker reports for a local image, or None (build_and_push tags only
    the full ECR reference). With the containerd image store, `image inspect .Size` is the compressed
    content and `image ls` adds the unpacked layers; AgentCore's docs don't say which one its 2 GB limit
    measures, so check the larger."""
    sizes = []
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", image_uri],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            sizes.append(int(r.stdout.strip()))
        r = subprocess.run(
            ["docker", "image", "ls", "--format", "{{.Size}}", image_uri], capture_output=True, text=True, check=False
        )
        line = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
        m = re.fullmatch(r"([0-9.]+)\s*([kMG]?B)", line)
        if m:
            sizes.append(int(float(m.group(1)) * {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}[m.group(2)]))
    except (OSError, KeyError, ValueError):
        pass
    return max(sizes) if sizes else None


def report_image_size(key: str, size: int | None) -> None:
    if key != "box":
        if size:
            ok(f"  {size / 1e6:.0f} MB on disk")
        return
    if not size:
        warn("  image size unknown (the image isn't in this machine's Docker): spike item 5")
        return
    (warn if size > IMAGE_LIMIT else ok)(
        f"  {size / 1e6:.0f} MB on disk (AgentCore's image limit is {IMAGE_LIMIT / 1e9:.0f} GB)"
    )


def ensure_images(ctx: Ctx) -> None:
    section("1 · Images: devbox-box (the dev box) and devbox-edge (the static edge Lambda), linux/arm64")
    ecr = ctx.aws.ecr
    registry = f"{ctx.account}.dkr.ecr.{ctx.s.region}.amazonaws.com"
    token = None
    for key, (repo, context_dir, first) in IMAGES.items():
        try:
            ecr.describe_repositories(repositoryNames=[repo])
            ok(f"ECR repository {repo}")
        except ClientError as e:
            if not is_missing(e):
                raise
            change(
                ctx,
                f"create ECR repository {repo} (private, scan on push, immutable tags)",
                ecr.create_repository,
                **ecr_repository_request(repo),
            )
        if not (context_dir / "Dockerfile").exists():
            where = context_dir.relative_to(REMOTE.parent) if context_dir.is_relative_to(REMOTE.parent) else context_dir
            (warn if ctx.check else bad)(f"{where}/Dockerfile isn't there yet: build that component first")
            continue
        if first and not ctx.check:
            try:
                prebuild(context_dir, first)
            except RuntimeError as e:
                die(str(e))
            ok(f"  {context_dir.name}/{' '.join(first)}")
        elif first:
            ok(
                f"  (deploy runs {context_dir.name}/{' '.join(first)} first; check hashes {context_dir.name}/ as it is now)"
            )
        tag = context_hash(context_dir)
        uri = f"{registry}/{repo}:{tag}"
        ctx.images[key] = uri
        try:
            img = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])["imageDetails"][0]
            ok(f"  {repo}:{tag} is in ECR ({img.get('imageSizeInBytes', 0) / 1e6:.0f} MB compressed)")
            if key == "box":
                report_image_size(key, local_image_size(uri))
            continue
        except ClientError as e:
            if not is_missing(e):
                raise
        if token is None and not ctx.check:
            token = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
        try:
            size = change(
                ctx,
                f"build and push {repo}:{tag} (from {context_dir.name}/)",
                build_and_push,
                context_dir,
                uri,
                registry,
                token,
            )
        except RuntimeError as e:
            die(str(e))
        if not ctx.check:
            report_image_size(key, size)
    if Report.problems:
        die("Fix the problems above, then run again.")


# ============================================================================= 2 IAM
def ensure_role(ctx: Ctx, name: str, spec: dict) -> str:
    iam = ctx.aws.iam
    arn = f"arn:aws:iam::{ctx.account}:role/{name}"
    try:
        cur = iam.get_role(RoleName=name)["Role"]
    except ClientError as e:
        if not is_missing(e):
            raise
        cur = None
    if cur is None:
        change(ctx, f"create role {name}", iam.create_role, **role_request(name, spec["trust"], spec["description"]))
        for m in spec["managed"]:
            change(ctx, f"  attach {m.rsplit('/', 1)[-1]}", iam.attach_role_policy, RoleName=name, PolicyArn=m)
        for pname, doc in spec["inline"].items():
            change(
                ctx,
                f"  set {pname}",
                iam.put_role_policy,
                RoleName=name,
                PolicyName=pname,
                PolicyDocument=json.dumps(doc),
            )
    else:
        if same_policy(cur["AssumeRolePolicyDocument"], spec["trust"]):
            ok(f"role {name}")
        else:
            change(
                ctx,
                f"update who can assume {name}",
                iam.update_assume_role_policy,
                RoleName=name,
                PolicyDocument=json.dumps(spec["trust"]),
            )
        attached = {
            p["PolicyArn"] for p in paged(iam.list_attached_role_policies, "AttachedPolicies", "Marker", RoleName=name)
        }
        for m in spec["managed"]:
            if m in attached:
                ok(f"  {m.rsplit('/', 1)[-1]}")
            else:
                change(ctx, f"  attach {m.rsplit('/', 1)[-1]}", iam.attach_role_policy, RoleName=name, PolicyArn=m)
        for m in sorted(attached - set(spec["managed"])):
            change(
                ctx,
                f"  detach {m.rsplit('/', 1)[-1]} (not part of the dev box's design)",
                iam.detach_role_policy,
                RoleName=name,
                PolicyArn=m,
            )
        inline = set(paged(iam.list_role_policies, "PolicyNames", "Marker", RoleName=name))
        for pname, doc in spec["inline"].items():
            curdoc = iam.get_role_policy(RoleName=name, PolicyName=pname)["PolicyDocument"] if pname in inline else None
            if curdoc is not None and same_policy(curdoc, doc):
                ok(f"  {pname}")
            else:
                change(
                    ctx,
                    f"  set {pname}",
                    iam.put_role_policy,
                    RoleName=name,
                    PolicyName=pname,
                    PolicyDocument=json.dumps(doc),
                )
        for pname in sorted(inline - set(spec["inline"])):
            change(
                ctx,
                f"  remove inline policy {pname} (not part of the dev box's design)",
                iam.delete_role_policy,
                RoleName=name,
                PolicyName=pname,
            )
    return arn


def ensure_iam(ctx: Ctx) -> None:
    section("2 · IAM: the edge and gateway roles (each box's own execution role is made with the box, step 6)")
    for name, spec in iam_roles(ctx.account, ctx.s.region).items():
        ctx.roles[name] = ensure_role(ctx, name, spec)


# ============================================================================= 3 tools gateway
def ensure_cedar_rule(ctx: Ctx, engine_id: str, statement: str) -> None:
    acc = ctx.aws.acc
    rule = next(
        (p for p in paged(acc.list_policies, "policies", policyEngineId=engine_id) if p["name"] == CEDAR_RULE), None
    )
    if rule and "FAIL" in acc.get_policy(policyEngineId=engine_id, policyId=rule["policyId"]).get("status", ""):
        change(
            ctx,
            f"remove Cedar rule {CEDAR_RULE}, left failed by an earlier run",
            acc.delete_policy,
            policyEngineId=engine_id,
            policyId=rule["policyId"],
        )
        if not ctx.check:
            wait_for(
                "the failed Cedar rule to go",
                lambda: acc.get_policy(policyEngineId=engine_id, policyId=rule["policyId"]),
                lambda v: v is None,
                every=5,
                timeout=300,
            )
        rule = None

    def active(pid: str) -> None:
        wait_for(
            f"Cedar rule {CEDAR_RULE}",
            lambda: acc.get_policy(policyEngineId=engine_id, policyId=pid),
            lambda v: v and v["status"] == "ACTIVE",
            failed=lambda v: v and "FAIL" in v["status"],
            every=5,
            timeout=360,
        )

    if rule is None:
        r = change(
            ctx,
            f"create Cedar rule {CEDAR_RULE} (ClaudeCode-<tier> sessions may search)",
            acc.create_policy,
            **cedar_policy_request(engine_id, statement),
        )
        if r:
            active(r["policyId"])
        return
    cur = acc.get_policy(policyEngineId=engine_id, policyId=rule["policyId"])
    have = next(iter((cur.get("definition") or {}).values()), {}).get("statement", "")
    if re.sub(r"\s", "", have) == re.sub(r"\s", "", statement):
        ok(f"  Cedar rule {CEDAR_RULE}")
        return
    req = cedar_policy_request(engine_id, statement)
    change(
        ctx,
        f"update Cedar rule {CEDAR_RULE}",
        acc.update_policy,
        policyEngineId=engine_id,
        policyId=rule["policyId"],
        definition=req["definition"],
        validationMode=req["validationMode"],
    )
    if not ctx.check:
        active(rule["policyId"])


def status_reasons(get) -> str:
    """The statusReasons a get_gateway / get_gateway_target shows, as one line."""
    try:
        return "; ".join(get().get("statusReasons") or []) or "no reason given"
    except ClientError as e:
        return err_text(e)


def ensure_gateway(ctx: Ctx) -> None:
    section("3 · Tools: the devbox-tools gateway (AWS IAM sign-in, web-search connector, Cedar policy)")
    acc = ctx.aws.acc
    role_arn = ctx.roles.get(GATEWAY_ROLE) or f"arn:aws:iam::{ctx.account}:role/{GATEWAY_ROLE}"
    gw = next((g for g in paged(acc.list_gateways, "items") if g["name"] == GATEWAY_NAME), None)
    gid = gw["gatewayId"] if gw else None
    get_gw = lambda: acc.get_gateway(gatewayIdentifier=gid)

    def wait_gateway() -> None:
        wait_for(
            f"gateway {GATEWAY_NAME}",
            lambda: get_gw()["status"],
            lambda v: v == "READY",
            failed=failed_status,
            reason=lambda: status_reasons(get_gw),
        )

    if gid:
        status = gw.get("status", "")
        if status == "FAILED":
            (bad if ctx.check else die)(
                f"gateway {GATEWAY_NAME} ({gid}) is FAILED: {status_reasons(get_gw)}. Delete it (its targets first: "
                f"aws bedrock-agentcore-control delete-gateway-target / delete-gateway --gateway-identifier {gid}), then deploy again"
            )
            return
        if status in ("CREATING", "UPDATING") and not ctx.check:
            wait_gateway()
        elif status == "UPDATE_UNSUCCESSFUL":
            warn(
                f"gateway {GATEWAY_NAME}'s last update didn't apply ({status_reasons(get_gw)}); deploy re-applies what it needs"
            )
        ok(f"gateway {GATEWAY_NAME} ({gid})")
    else:
        r = change(
            ctx,
            f"create gateway {GATEWAY_NAME} (inbound: AWS IAM, signed as the person)",
            retry_iam,
            acc.create_gateway,
            **gateway_request(role_arn),
        )
        gid = r and r["gatewayId"]
        if gid:
            wait_gateway()
    if not gid:
        todo(
            "would add the WebSearch target, the DevboxToolsPolicies engine and its Cedar rule, and the gateway's resource policy"
        )
        Report.changes += 1
        return

    tgt = next(
        (t for t in paged(acc.list_gateway_targets, "items", gatewayIdentifier=gid) if t["name"] == GATEWAY_TARGET),
        None,
    )
    if tgt and failed_status(tgt.get("status")):
        get_t = lambda: acc.get_gateway_target(gatewayIdentifier=gid, targetId=tgt["targetId"])
        change(
            ctx,
            f"remove target {GATEWAY_TARGET}, left {tgt['status']} by an earlier run ({status_reasons(get_t)})",
            acc.delete_gateway_target,
            gatewayIdentifier=gid,
            targetId=tgt["targetId"],
        )
        if not ctx.check:
            wait_for(f"the failed {GATEWAY_TARGET} target to go", get_t, lambda v: v is None, every=5, timeout=300)
        tgt = None
    if tgt:
        ok(f"  target {GATEWAY_TARGET} ({tgt['targetId']})")
    else:
        r = change(
            ctx,
            f"add the {GATEWAY_TARGET} target (web-search connector)",
            retry_iam,
            acc.create_gateway_target,
            **gateway_target_request(gid),
        )
        if r:
            get_t = lambda: acc.get_gateway_target(gatewayIdentifier=gid, targetId=r["targetId"])
            wait_for(
                f"target {GATEWAY_TARGET}",
                lambda: get_t()["status"],
                lambda v: v == "READY",
                failed=failed_status,
                reason=lambda: status_reasons(get_t),
            )

    eng = next((e for e in paged(acc.list_policy_engines, "policyEngines") if e["name"] == POLICY_ENGINE), None)
    eid = eng["policyEngineId"] if eng else None
    if eid:
        ok(f"  policy engine {POLICY_ENGINE} ({eid})")
    else:
        r = change(ctx, f"create policy engine {POLICY_ENGINE}", acc.create_policy_engine, **policy_engine_request())
        eid = r and r["policyEngineId"]
        if eid:
            wait_for(
                f"policy engine {POLICY_ENGINE}",
                lambda: acc.get_policy_engine(policyEngineId=eid)["status"],
                lambda v: v == "ACTIVE",
                failed=failed_status,
            )
    if eid:
        ensure_cedar_rule(ctx, eid, cedar_statement(ctx.account, ctx.s.region, gid))
        engine_arn = acc.get_policy_engine(policyEngineId=eid)["policyEngineArn"]
        cur = acc.get_gateway(gatewayIdentifier=gid)
        pe = cur.get("policyEngineConfiguration") or {}
        if pe.get("arn") == engine_arn and pe.get("mode") == "ENFORCE":
            ok(f"  {GATEWAY_NAME} enforces {POLICY_ENGINE}")
        else:
            change(
                ctx,
                f"attach {POLICY_ENGINE} to {GATEWAY_NAME} (ENFORCE)",
                acc.update_gateway,
                **attach_policy_engine_request(gid, role_arn, engine_arn),
            )
            if not ctx.check:
                wait_gateway()
    else:
        todo(f"would add the Cedar rule {CEDAR_RULE} and attach {POLICY_ENGINE} to {GATEWAY_NAME}")
        Report.changes += 1

    cur = acc.get_gateway(gatewayIdentifier=gid)
    garn, url = cur["gatewayArn"], cur["gatewayUrl"]
    want = gateway_resource_policy(garn, ctx.account)
    try:
        have = acc.get_resource_policy(resourceArn=garn).get("policy")
    except ClientError as e:
        if not is_missing(e):
            raise
        have = None
    if have and same_policy(have, want):
        ok("  resource policy: the ClaudeCode-<tier> roles may call it")
    else:
        change(
            ctx,
            "set the gateway's resource policy (the ClaudeCode-<tier> roles may call it)",
            acc.put_resource_policy,
            resourceArn=garn,
            policy=json.dumps(want),
        )
    ctx.gateway = {"id": gid, "arn": garn, "url": url}
    ctx.state["gateway"] = dict(ctx.gateway)


# ============================================================================= 4 storage (EFS)
def find_file_system(ctx: Ctx) -> dict | None:
    fss = [
        f
        for f in ctx.aws.efs.describe_file_systems(CreationToken=EFS_TOKEN).get("FileSystems") or []
        if f.get("LifeCycleState") not in ("deleting", "deleted")
    ]
    return find_one(fss, "EFS file system")


def file_system_state(ctx: Ctx, fs_id: str) -> str | None:
    fss = ctx.aws.efs.describe_file_systems(FileSystemId=fs_id).get("FileSystems") or []
    return fss[0]["LifeCycleState"] if fss else None


def access_points(ctx: Ctx, fs_id: str) -> list[dict]:
    return [
        a
        for a in paged(ctx.aws.efs.describe_access_points, "AccessPoints", "NextToken", FileSystemId=fs_id)
        if a.get("LifeCycleState") not in ("deleting", "deleted")
    ]


def person_access_point(aps: list[dict], user: User) -> dict | None:
    return find_one(
        [a for a in aps if (a.get("RootDirectory") or {}).get("Path") == user.efs_root],
        f"access point for {user.efs_root}",
    )


def mount_targets(ctx: Ctx, fs_id: str) -> list[dict]:
    return [
        m
        for m in paged(
            ctx.aws.efs.describe_mount_targets, "MountTargets", "NextMarker", send="Marker", FileSystemId=fs_id
        )
        if m.get("LifeCycleState") not in ("deleting", "deleted")
    ]


def ensure_storage(ctx: Ctx) -> None:
    """The EFS file system and its policy. Each person's folder (an access point) and execution role are made with
    their box, on their first visit (advance_box). The policy names nobody, so a new person needs no change
    here. None of it needs the VPC; the mount target and its security group come with the network (step 5), so the
    policy is in place before anything can reach the file system."""
    s, efs = ctx.s, ctx.aws.efs
    section(f"4 · Storage: EFS file system {EFS_NAME} (each person's folder {EFS_ROOT}/<name> comes with their box)")
    fs = find_file_system(ctx)
    if fs:
        fs_id = fs["FileSystemId"]
        if fs["LifeCycleState"] == "error":
            bad(f"EFS file system {EFS_NAME} ({fs_id}) is in the error state: see the EFS console")
        elif fs["LifeCycleState"] != "available" and not ctx.check:
            wait_for(
                f"EFS file system {fs_id}",
                lambda: file_system_state(ctx, fs_id),
                lambda v: v == "available",
                failed=lambda v: v == "error",
                every=5,
                timeout=600,
            )
        ok(
            f"EFS file system {EFS_NAME} ({fs_id}, {'encrypted' if fs.get('Encrypted') else 'NOT encrypted'}, "
            f"{fs.get('ThroughputMode', '?')} throughput)"
        )
        if not fs.get("Encrypted"):
            warn("  it isn't encrypted at rest, and that can't be changed: move the data and make a new one to fix it")
        if fs.get("ThroughputMode") != "elastic":
            warn(f"  it has {fs.get('ThroughputMode')} throughput, not elastic (deploy leaves it)")
    else:
        r = change(
            ctx,
            f"create EFS file system {EFS_NAME} (encrypted, elastic throughput, general purpose)",
            efs.create_file_system,
            **file_system_request(),
        )
        fs_id = r and r["FileSystemId"]
        if fs_id:
            wait_for(
                f"EFS file system {fs_id}",
                lambda: file_system_state(ctx, fs_id),
                lambda v: v == "available",
                failed=lambda v: v == "error",
                every=5,
                timeout=600,
            )
    if not fs_id:
        pending(ctx, "  set the file system policy (TLS only, no root, no anonymous NFS)")
        return
    fs_arn = file_system_arn(ctx.account, s.region, fs_id)
    ctx.efs.update(id=fs_id, arn=fs_arn)
    ctx.state["efs"] = {**ctx.state.get("efs", {}), "fileSystemId": fs_id, "fileSystemArn": fs_arn}
    for ap in access_points(ctx, fs_id):
        if ap.get("LifeCycleState") == "error":
            warn(
                f"  access point {(ap.get('RootDirectory') or {}).get('Path')} ({ap['AccessPointId']}) is in the error state, so its "
                "box can't mount it (a 424 on every invoke): delete it by hand (aws efs delete-access-point); the provisioner "
                "makes it again on the person's next visit. Their files stay"
            )

    want = file_system_policy_static(fs_arn)
    try:
        have = efs.describe_file_system_policy(FileSystemId=fs_id).get("Policy")
    except ClientError as e:
        if not is_missing(e):
            raise
        have = None
    what = "TLS only, no root, no anonymous NFS; each person's role allows only their own access point"
    if have and same_policy(have, want):
        ok(f"  file system policy: {what}")
    else:
        change(
            ctx,
            f"  set the file system policy ({what})",
            retry_iam,
            efs.put_file_system_policy,
            FileSystemId=fs_id,
            Policy=json.dumps(want),
        )


def wait_access_point(ctx: Ctx, ap_id: str) -> None:
    def probe():
        got = ctx.aws.efs.describe_access_points(AccessPointId=ap_id).get("AccessPoints") or []
        return got[0]["LifeCycleState"] if got else None

    wait_for(
        f"access point {ap_id}", probe, lambda v: v == "available", failed=lambda v: v == "error", every=5, timeout=300
    )


def ensure_storage_lookup(ctx: Ctx) -> None:
    """The file system's id (for the allowlist) without changing anything."""
    fs = find_file_system(ctx)
    if fs:
        ctx.efs.update(id=fs["FileSystemId"], arn=file_system_arn(ctx.account, ctx.s.region, fs["FileSystemId"]))


def ensure_mount_target(ctx: Ctx, vpc_id: str, subnet_id: str | None, efs_sg: str | None) -> None:
    """One mount target, in the box subnet (the runtime's only subnet, so the same AZ), behind devbox-efs."""
    efs, fs_id = ctx.aws.efs, ctx.efs.get("id")
    if not fs_id and not ctx.check:
        warn(f"no EFS file system {EFS_NAME} yet: deploy makes it, and then its mount target")
        return
    if not (fs_id and subnet_id and efs_sg):
        pending(ctx, f"create the EFS mount target in devbox-box (security group {EFS_SG})")
        return
    mts = mount_targets(ctx, fs_id)
    elsewhere = [m for m in mts if m.get("VpcId") and m["VpcId"] != vpc_id]
    if elsewhere:
        bad(
            f"EFS file system {fs_id} has a mount target in another VPC ({elsewhere[0]['VpcId']}): a file system's mount "
            "targets all live in one VPC. Delete it by hand, then deploy again"
        )
        return
    mt = next((m for m in mts if m["SubnetId"] == subnet_id), None)
    if mt:
        if mt["LifeCycleState"] == "error":
            bad(f"EFS mount target {mt['MountTargetId']} is in the error state: delete it by hand, then deploy again")
        elif mt["LifeCycleState"] != "available" and not ctx.check:
            wait_mount_target(ctx, mt["MountTargetId"])
        ok(f"EFS mount target in devbox-box ({mt['MountTargetId']}, {mt.get('IpAddress', '?')})")
        sgs = efs.describe_mount_target_security_groups(MountTargetId=mt["MountTargetId"]).get("SecurityGroups") or []
        if sorted(sgs) == [efs_sg]:
            ok(f"  behind {EFS_SG} only")
        else:
            change(
                ctx,
                f"  put it behind {EFS_SG} only (it had {', '.join(sgs) or 'none'})",
                efs.modify_mount_target_security_groups,
                MountTargetId=mt["MountTargetId"],
                SecurityGroups=[efs_sg],
            )
        mt_id = mt["MountTargetId"]
    else:
        r = change(
            ctx,
            f"create the EFS mount target in devbox-box (security group {EFS_SG}; a few minutes)",
            efs.create_mount_target,
            **mount_target_request(fs_id, subnet_id, efs_sg),
        )
        mt_id = r and r["MountTargetId"]
        if mt_id:
            wait_mount_target(ctx, mt_id)
    if mt_id:
        ctx.efs["mountTargetId"] = mt_id
        ctx.state.setdefault("efs", {})["mountTargetId"] = mt_id


def wait_mount_target(ctx: Ctx, mt_id: str) -> None:
    def probe():
        got = ctx.aws.efs.describe_mount_targets(MountTargetId=mt_id).get("MountTargets") or []
        return got[0]["LifeCycleState"] if got else None

    wait_for(
        f"EFS mount target {mt_id}",
        probe,
        lambda v: v == "available",
        failed=lambda v: v == "error",
        every=10,
        timeout=900,
    )


# ============================================================================= 5 network
def find_one(items: list, what: str):
    if len(items) > 1:
        die(f"more than one {what} tagged for the dev box: remove the extra one by hand")
    return items[0] if items else None


def tag_filters(name: str) -> list[dict]:
    return [{"Name": "tag:Name", "Values": [name]}, {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]}]


def find_vpc(ctx: Ctx) -> dict | None:
    return find_one(ctx.aws.ec2.describe_vpcs(Filters=tag_filters(VPC_NAME))["Vpcs"], "VPC")


def is_paused(vpc: dict | None) -> bool:
    return bool(vpc) and any(t["Key"] == PAUSED_TAG and t["Value"] == "true" for t in vpc.get("Tags", []))


def find_firewall(ctx: Ctx) -> dict | None:
    try:
        return ctx.aws.nfw.describe_firewall(FirewallName=FIREWALL)
    except ClientError as e:
        if is_missing(e):
            return None
        raise


def firewall_endpoint(desc: dict | None, az: str) -> str | None:
    att = (((desc or {}).get("FirewallStatus") or {}).get("SyncStates") or {}).get(az, {}).get("Attachment") or {}
    return att.get("EndpointId") if att.get("Status") == "READY" else None


def drop_firewall_routes(ctx: Ctx, vpc_id: str, fw: dict | None) -> None:
    """Network Firewall won't delete a firewall while any route table points at its endpoint (live 2026-10-02:
    "related VPC endpoint(s) still exist in route table(s)"). So the routes to it go first; `network resume` and deploy
    make them again. Only routes to the firewall's endpoint: the S3 gateway endpoint's route (a prefix list) stays."""
    ec2 = ctx.aws.ec2
    att = (((fw or {}).get("FirewallStatus") or {}).get("SyncStates") or {}).get(ctx.s.az, {}).get("Attachment") or {}
    ep = att.get("EndpointId")
    if not ep:
        return
    for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]:
        name = next((t["Value"] for t in rt.get("Tags", []) if t["Key"] == "Name"), rt["RouteTableId"])
        for r in rt.get("Routes", []):
            if (r.get("GatewayId") or r.get("VpcEndpointId")) == ep and r.get("DestinationCidrBlock"):
                change(
                    ctx,
                    f"  {name}: drop the route {r['DestinationCidrBlock']} → the firewall (it can't be deleted while routed to)",
                    ec2.delete_route,
                    RouteTableId=rt["RouteTableId"],
                    DestinationCidrBlock=r["DestinationCidrBlock"],
                )


def drop_firewall_logging(ctx: Ctx) -> None:
    """Network Firewall won't delete a firewall that still has a logging configuration (live 2026-10-02), and
    UpdateLoggingConfiguration adds or removes one destination per call: drop them one at a time. Resume and deploy set
    them again (logging_steps)."""
    nfw = ctx.aws.nfw
    cur = (nfw.describe_logging_configuration(FirewallName=FIREWALL).get("LoggingConfiguration") or {}).get(
        "LogDestinationConfigs"
    ) or []
    while cur:
        gone, cur = cur[-1], cur[:-1]
        change(
            ctx,
            f"  stop the firewall's {gone.get('LogType', '?')} log (it can't be deleted while it logs)",
            nfw.update_logging_configuration,
            FirewallName=FIREWALL,
            LoggingConfiguration={"LogDestinationConfigs": cur},
        )


def find_nat(ctx: Ctx, vpc_id: str) -> dict | None:
    nats = ctx.aws.ec2.describe_nat_gateways(
        Filter=[
            *tag_filters(NAT_NAME),
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "state", "Values": ["pending", "available"]},
        ]
    )["NatGateways"]
    return find_one(nats, "NAT gateway")


def rule_group_arn(ctx: Ctx, name: str) -> tuple[str | None, dict | None]:
    try:
        d = ctx.aws.nfw.describe_rule_group(RuleGroupName=name, Type="STATEFUL")
        return d["RuleGroupResponse"]["RuleGroupArn"], d
    except ClientError as e:
        if is_missing(e):
            return None, None
        raise


def allowlist_values(account: str, gateway_url: str, s: Settings, file_system_id: str = "") -> dict[str, str]:
    host = gateway_host(gateway_url) or f"{GATEWAY_NAME}-pending.gateway.bedrock-agentcore.{s.region}.amazonaws.com"
    return {
        "ACCOUNT_ID": account,
        "TOOLS_GATEWAY_HOST": host,
        "IDC_REGION": s.idc_region,
        "EFS_FILE_SYSTEM_ID": file_system_id or "fs-pending",
    }


def allowlist_domains(ctx: Ctx) -> list[str]:
    return parse_allowlist(
        (TEMPLATES / "egress-allowlist.txt").read_text(),
        allowlist_values(ctx.account, ctx.gateway.get("url", ""), ctx.s, ctx.efs.get("id", "")),
    )


def ensure_rule_group(ctx: Ctx) -> str | None:
    nfw = ctx.aws.nfw
    domains = allowlist_domains(ctx)
    allow_arn, cur = rule_group_arn(ctx, RG_ALLOW)
    if not allow_arn:
        r = change(
            ctx,
            f"create firewall rule group {RG_ALLOW} ({len(domains)} names from templates/egress-allowlist.txt)",
            nfw.create_rule_group,
            **allowlist_rule_group_request(domains),
        )
        allow_arn = r and r["RuleGroupResponse"]["RuleGroupArn"]
    else:
        have = (cur["RuleGroup"]["RulesSource"].get("RulesSourceList") or {}).get("Targets") or []
        if sorted(have) == sorted(domains):
            ok(f"firewall rule group {RG_ALLOW} ({len(domains)} names)")
        else:
            added, removed = sorted(set(domains) - set(have)), sorted(set(have) - set(domains))
            what = ", ".join([f"+{d}" for d in added] + [f"-{d}" for d in removed])
            change(
                ctx,
                f"update the allowlist ({what})",
                nfw.update_rule_group,
                UpdateToken=cur["UpdateToken"],
                RuleGroupArn=allow_arn,
                Type="STATEFUL",
                RuleGroup=allowlist_rule_group(domains),
            )
    return allow_arn


def ensure_firewall_policy(ctx: Ctx, allow_arn: str | None) -> str | None:
    """The allowlist, then drop (and alert on) everything else."""
    nfw = ctx.aws.nfw
    try:
        cur = nfw.describe_firewall_policy(FirewallPolicyName=FIREWALL_POLICY)
    except ClientError as e:
        if not is_missing(e):
            raise
        cur = None
    what = "the allowlist, then drop everything else"
    if not allow_arn:
        pending(ctx, f"create firewall policy {FIREWALL_POLICY} ({what})")
        return None
    doc = firewall_policy_doc(allow_arn, ctx.s.enforce_defaults)
    if cur is None:
        r = change(
            ctx,
            f"create firewall policy {FIREWALL_POLICY} ({what})",
            nfw.create_firewall_policy,
            **firewall_policy_request(doc),
        )
        return r and r["FirewallPolicyResponse"]["FirewallPolicyArn"]
    arn = cur["FirewallPolicyResponse"]["FirewallPolicyArn"]
    if covers(cur["FirewallPolicy"], doc):
        ok(f"firewall policy {FIREWALL_POLICY}: {what}")
    else:
        change(
            ctx,
            f"set firewall policy {FIREWALL_POLICY} to {what}",
            nfw.update_firewall_policy,
            UpdateToken=cur["UpdateToken"],
            FirewallPolicyArn=arn,
            FirewallPolicy=doc,
        )
    return arn


def rule_label(key: tuple) -> str:
    proto = "all" if key[0] == "-1" else key[0]
    return f"{proto}/{key[1] if key[1] != -1 else 'all'}→{key[3]}"


def revoke_rules(revoke, sg_id: str, rule_ids: list[str]) -> None:
    """Revoke by rule id; one that's already gone (a stale read) is fine."""
    try:
        revoke(GroupId=sg_id, SecurityGroupRuleIds=rule_ids)
    except ClientError as e:
        if not is_missing(e):
            raise
        for rid in rule_ids:
            try:
                revoke(GroupId=sg_id, SecurityGroupRuleIds=[rid])
            except ClientError as e2:
                if not is_missing(e2):
                    raise


def authorize_rules(authorize, sg_id: str, perms: list[dict]) -> None:
    """Authorize; one that's already there (a stale read) is fine."""
    try:
        authorize(GroupId=sg_id, IpPermissions=perms)
    except ClientError as e:
        if err_code(e) != "InvalidPermission.Duplicate":
            raise
        for perm in perms:
            try:
                authorize(GroupId=sg_id, IpPermissions=[perm])
            except ClientError as e2:
                if err_code(e2) != "InvalidPermission.Duplicate":
                    raise


def find_security_group(ctx: Ctx, vpc_id: str, name: str) -> dict | None:
    return find_one(
        ctx.aws.ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": [name]}]
        )["SecurityGroups"],
        f"security group {name}",
    )


def ensure_group(ctx: Ctx, vpc_id: str, name: str, description: str) -> str | None:
    ec2 = ctx.aws.ec2
    sg = find_security_group(ctx, vpc_id, name)
    if sg:
        ok(f"security group {name} ({sg['GroupId']})")
        return sg["GroupId"]

    def create() -> str:
        gid = ec2.create_security_group(
            GroupName=name, VpcId=vpc_id, TagSpecifications=tag_spec("security-group", name), Description=description
        )["GroupId"]
        # A new group allows all outbound. Revoke that by what it is, not by a rule id read back straight away:
        # that read is eventually consistent and can come back without it.
        try:
            r = ec2.revoke_security_group_egress(GroupId=gid, IpPermissions=DEFAULT_EGRESS)
        except ClientError as e:
            if err_code(e) != "InvalidPermission.NotFound":
                raise
        else:
            if r.get("UnknownIpPermissions"):
                warn("  the new group had no default allow-all outbound rule to remove")
        return gid

    return change(ctx, f"create security group {name} (its default allow-all outbound removed)", create)


def reconcile_rules(ctx: Ctx, sg_id: str, name: str, egress_wanted: list[dict], ingress_wanted: list[dict]) -> None:
    ec2 = ctx.aws.ec2
    rules = ec2.describe_security_group_rules(Filters=[{"Name": "group-id", "Values": [sg_id]}])["SecurityGroupRules"]
    for egress, wanted, authorize, revoke in (
        (True, egress_wanted, ec2.authorize_security_group_egress, ec2.revoke_security_group_egress),
        (False, ingress_wanted, ec2.authorize_security_group_ingress, ec2.revoke_security_group_ingress),
    ):
        have = {rule_key(r): r["SecurityGroupRuleId"] for r in rules if r["IsEgress"] == egress}
        want = {rule_key(w): w for w in wanted}
        extra = {k: rid for k, rid in have.items() if k not in want}
        missing = [w for k, w in want.items() if k not in have]
        kind = "outbound" if egress else "inbound"
        if extra:
            change(
                ctx,
                f"  remove {kind} {', '.join(rule_label(k) for k in extra)} from {name} (not wanted)",
                revoke_rules,
                revoke,
                sg_id,
                list(extra.values()),
            )
        if missing:
            change(
                ctx,
                f"  {name}: allow {kind}: " + ", ".join(rule_label(rule_key(m)) for m in missing),
                authorize_rules,
                authorize,
                sg_id,
                missing,
            )
        if not extra and not missing:
            ok(f"  {name} {kind}: " + (", ".join(rule_label(k) for k in want) or "none"))


def ensure_security_group(ctx: Ctx, vpc_id: str) -> str | None:
    """devbox-box (the runtimes' network interfaces): no inbound; HTTPS out through the firewall, DNS to the
    resolver, NFS to devbox-efs. devbox-efs (the mount target): NFS in from devbox-box only, nothing out."""
    box_sg = ensure_group(
        ctx, vpc_id, SG_NAME, "Dev box runtimes: no inbound; HTTPS out through the firewall; NFS to devbox-efs"
    )
    efs_sg = ensure_group(ctx, vpc_id, EFS_SG, "Dev box EFS mount target: NFS in from devbox-box only")
    ctx.net["efs_sg"] = efs_sg
    if box_sg:
        reconcile_rules(ctx, box_sg, SG_NAME, sg_egress_wanted(efs_sg), [])
    if efs_sg and box_sg:
        reconcile_rules(ctx, efs_sg, EFS_SG, [], efs_sg_ingress_wanted(box_sg))
    else:
        pending(ctx, f"  allow NFS (TCP {NFS_PORT}) from {SG_NAME} to {EFS_SG} (both groups' rules)")
    return box_sg


def ensure_route(ctx: Ctx, table: dict, dest: str, kind: str, target: str) -> None:
    ec2 = ctx.aws.ec2
    cur = next((r for r in table.get("Routes", []) if r.get("DestinationCidrBlock") == dest), None)
    name = next((t["Value"] for t in table.get("Tags", []) if t["Key"] == "Name"), table["RouteTableId"])
    if cur and route_target(cur) == target and cur.get("State") == "active":
        ok(f"  {name}: {dest} → {target}")
    elif cur:
        change(
            ctx,
            f"  {name}: point {dest} at {target}"
            + (" (it was a blackhole)" if cur.get("State") == "blackhole" else ""),
            ec2.replace_route,
            RouteTableId=table["RouteTableId"],
            DestinationCidrBlock=dest,
            **{kind: target},
        )
    else:
        change(
            ctx,
            f"  {name}: route {dest} → {target}",
            ec2.create_route,
            RouteTableId=table["RouteTableId"],
            DestinationCidrBlock=dest,
            **{kind: target},
        )


def ensure_network(ctx: Ctx, *, paused: bool | None = None) -> None:
    """paused: None = read the VPC's tag; resume passes False, since a tag read straight after DeleteTags can be stale."""
    s, ec2, nfw = ctx.s, ctx.aws.ec2, ctx.aws.nfw
    section(
        f"5 · Network: one AZ ({s.az}), box subnet → Network Firewall → NAT → internet; DNS Firewall on the VPC resolver; "
        "the EFS mount target; the S3 gateway endpoint"
    )
    vpc = find_vpc(ctx)
    if vpc and vpc["CidrBlock"] != VPC_CIDR:
        die(f"VPC {vpc['VpcId']} is tagged for the dev box but has {vpc['CidrBlock']}, not {VPC_CIDR}")
    vpc_id = vpc and vpc["VpcId"]
    if vpc_id:
        ok(f"VPC {VPC_NAME} {VPC_CIDR} ({vpc_id})")
    else:
        r = change(
            ctx,
            f"create VPC {VPC_NAME} {VPC_CIDR}",
            ec2.create_vpc,
            CidrBlock=VPC_CIDR,
            TagSpecifications=tag_spec("vpc", VPC_NAME),
        )
        vpc_id = r and r["Vpc"]["VpcId"]
        if vpc_id:
            wait_waiter(ec2, "vpc_available", f"VPC {vpc_id}", lambda: "still not available", VpcIds=[vpc_id])
    if not vpc_id:
        for what in (
            "subnets devbox-box, devbox-firewall and devbox-public (DNS support and hostnames on)",
            "the internet gateway",
            f"security groups {SG_NAME} and {EFS_SG} (NFS from {SG_NAME} only)",
            "the EFS mount target in devbox-box",
            "the NAT gateway and its address",
            f"firewall {FIREWALL} and its logging",
            "the route tables and routes",
            f"the S3 gateway endpoint {S3_ENDPOINT_NAME} on devbox-rt-box (ECR's image layers only)",
        ):
            todo(f"would create {what}")
            Report.changes += 1
        ctx.net = {"vpc": None}
        ensure_log_group(ctx, FIREWALL_LOG_GROUP, 90)
        ensure_firewall_policy(ctx, ensure_rule_group(ctx))
        ensure_dns_firewall(ctx, None)
        return
    for attr in ("EnableDnsSupport", "EnableDnsHostnames"):
        if ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute=attr[0].lower() + attr[1:])[attr]["Value"]:
            ok(f"  {attr}")
        else:
            change(ctx, f"  turn on {attr}", ec2.modify_vpc_attribute, VpcId=vpc_id, **{attr: {"Value": True}})

    subnet_ids: dict[str, str | None] = {}
    for name, cidr in SUBNETS.items():
        sn = find_one(
            ec2.describe_subnets(Filters=[*tag_filters(name), {"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"],
            "subnet",
        )
        if sn:
            if sn["AvailabilityZone"] != s.az:
                die(
                    f"subnet {name} is in {sn['AvailabilityZone']}, not DEVBOX_AZ={s.az} (a subnet can't move; the boxes' "
                    "capacity providers use it)"
                )
            ok(f"subnet {name} {cidr} ({sn['SubnetId']})")
            if sn.get("MapPublicIpOnLaunch"):
                change(
                    ctx,
                    f"  stop giving public IPs in {name}",
                    ec2.modify_subnet_attribute,
                    SubnetId=sn["SubnetId"],
                    MapPublicIpOnLaunch={"Value": False},
                )
            subnet_ids[name] = sn["SubnetId"]
        else:
            r = change(
                ctx,
                f"create subnet {name} {cidr} in {s.az}",
                ec2.create_subnet,
                **subnet_request(vpc_id, name, s.az),
            )
            subnet_ids[name] = r and r["Subnet"]["SubnetId"]

    igw = find_one(
        ec2.describe_internet_gateways(Filters=tag_filters(IGW_NAME))["InternetGateways"], "internet gateway"
    )
    igw_id = igw and igw["InternetGatewayId"]
    if igw_id:
        ok(f"internet gateway {IGW_NAME} ({igw_id})")
    else:
        r = change(
            ctx,
            f"create internet gateway {IGW_NAME}",
            ec2.create_internet_gateway,
            TagSpecifications=tag_spec("internet-gateway", IGW_NAME),
        )
        igw_id = r and r["InternetGateway"]["InternetGatewayId"]
    if igw_id and not any(a.get("VpcId") == vpc_id for a in (igw or {}).get("Attachments", [])):
        change(
            ctx, f"  attach {IGW_NAME} to the VPC", ec2.attach_internet_gateway, InternetGatewayId=igw_id, VpcId=vpc_id
        )

    ensure_log_group(ctx, FIREWALL_LOG_GROUP, 90)
    policy_arn = ensure_firewall_policy(ctx, ensure_rule_group(ctx))
    sg_id = ensure_security_group(ctx, vpc_id)
    ensure_mount_target(ctx, vpc_id, subnet_ids.get("devbox-box"), ctx.net.get("efs_sg"))
    ensure_dns_firewall(ctx, vpc_id)

    paused = is_paused(vpc) if paused is None else paused
    nat_id = endpoint = None
    if paused:
        warn("the network is paused (no firewall, no NAT: the box has no way out). `network resume` brings it back.")
    else:
        addr = find_one(ec2.describe_addresses(Filters=tag_filters(NAT_NAME))["Addresses"], "Elastic IP")
        alloc = addr and addr["AllocationId"]
        if alloc:
            ok(f"Elastic IP for the NAT gateway ({addr.get('PublicIp')})")
        else:
            r = change(
                ctx,
                "allocate an Elastic IP for the NAT gateway",
                ec2.allocate_address,
                Domain="vpc",
                TagSpecifications=tag_spec("elastic-ip", NAT_NAME),
            )
            alloc = r and r["AllocationId"]
        nat = find_nat(ctx, vpc_id)
        nat_id = nat and nat["NatGatewayId"]
        if nat_id:
            ok(f"NAT gateway {NAT_NAME} ({nat_id}, {nat['State']})")
        elif subnet_ids.get("devbox-public") and alloc:
            r = change(
                ctx,
                f"create NAT gateway {NAT_NAME} in devbox-public",
                ec2.create_nat_gateway,
                SubnetId=subnet_ids["devbox-public"],
                AllocationId=alloc,
                ConnectivityType="public",
                TagSpecifications=tag_spec("natgateway", NAT_NAME),
            )
            nat_id = r and r["NatGateway"]["NatGatewayId"]
        else:
            pending(ctx, f"create NAT gateway {NAT_NAME} in devbox-public")
        if nat_id and not ctx.check:
            wait_waiter(
                ec2,
                "nat_gateway_available",
                f"NAT gateway {nat_id}",
                lambda: nat_reason(ctx, nat_id),
                NatGatewayIds=[nat_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": 40},
            )

        fw = find_firewall(ctx)
        if fw:
            ok(f"firewall {FIREWALL} ({fw['FirewallStatus']['Status']})")
            if policy_arn and fw["Firewall"]["FirewallPolicyArn"] != policy_arn:
                change(
                    ctx,
                    f"  use policy {FIREWALL_POLICY}",
                    nfw.associate_firewall_policy,
                    FirewallName=FIREWALL,
                    FirewallPolicyArn=policy_arn,
                    UpdateToken=fw["UpdateToken"],
                )
        elif policy_arn and subnet_ids.get("devbox-firewall"):
            change(
                ctx,
                f"create firewall {FIREWALL} in devbox-firewall (5–10 minutes)",
                nfw.create_firewall,
                **firewall_request(policy_arn, vpc_id, subnet_ids["devbox-firewall"]),
            )
        else:
            pending(ctx, f"create firewall {FIREWALL} in devbox-firewall")
        if not ctx.check:
            fw = wait_for(
                f"firewall {FIREWALL}",
                lambda: find_firewall(ctx),
                lambda v: bool(firewall_endpoint(v, s.az)),
                failed=lambda v: v and v["FirewallStatus"]["Status"] == "FAILED",
                timeout=1500,
                every=20,
            )
            endpoint = firewall_endpoint(fw, s.az)
        elif fw:
            endpoint = firewall_endpoint(fw, s.az)
        if fw or not ctx.check:
            cur = (
                []
                if not fw
                else (nfw.describe_logging_configuration(FirewallName=FIREWALL).get("LoggingConfiguration") or {}).get(
                    "LogDestinationConfigs", []
                )
            )
            steps = logging_steps(cur)
            for conf in steps:
                change(
                    ctx,
                    f"  send firewall {conf[-1]['LogType'].lower()} logs to {FIREWALL_LOG_GROUP}",
                    nfw.update_logging_configuration,
                    FirewallName=FIREWALL,
                    LoggingConfiguration={"LogDestinationConfigs": conf},
                )
            if not steps:
                ok(f"  alert and flow logs → {FIREWALL_LOG_GROUP}")

    ids = {
        "vpc": vpc_id,
        "igw": igw_id,
        "nat": nat_id,
        "firewall_endpoint": endpoint,
        "sg": sg_id,
        "efs_sg": ctx.net.get("efs_sg"),
        **subnet_ids,
    }
    plan = route_plan(ids)
    box_rt = None
    for rt_name, subnet_name in ROUTE_TABLES.items():
        rt = find_one(
            ec2.describe_route_tables(Filters=[*tag_filters(rt_name), {"Name": "vpc-id", "Values": [vpc_id]}])[
                "RouteTables"
            ],
            "route table",
        )
        if rt:
            ok(f"route table {rt_name}")
        else:
            r = change(
                ctx,
                f"create route table {rt_name}",
                ec2.create_route_table,
                VpcId=vpc_id,
                TagSpecifications=tag_spec("route-table", rt_name),
            )
            rt = r and r["RouteTable"]
        if not rt:
            continue
        if rt_name == "devbox-rt-box":
            box_rt = rt["RouteTableId"]
        sid = subnet_ids.get(subnet_name)
        if sid and not any(a.get("SubnetId") == sid for a in rt.get("Associations", [])):
            change(
                ctx,
                f"  associate {rt_name} with {subnet_name}",
                ec2.associate_route_table,
                RouteTableId=rt["RouteTableId"],
                SubnetId=sid,
            )
        for dest, kind, target in plan[rt_name]:
            if target:
                ensure_route(ctx, rt, dest, kind, target)
            elif paused:
                ok(f"  {rt_name}: {dest} stays a blackhole while paused")
            else:
                pending(
                    ctx,
                    f"  {rt_name}: route {dest} → the {'firewall endpoint' if kind == 'VpcEndpointId' else 'NAT gateway'}",
                )
    ids["s3_endpoint"] = ensure_s3_endpoint(ctx, vpc_id, box_rt)
    ctx.net = ids
    ctx.state["network"] = {**ctx.state.get("network", {}), **{k: v for k, v in ids.items() if v}, "paused": paused}


def find_s3_endpoints(ctx: Ctx, vpc_id: str) -> list[dict]:
    got = paged(
        ctx.aws.ec2.describe_vpc_endpoints,
        "VpcEndpoints",
        "NextToken",
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [f"com.amazonaws.{ctx.s.region}.s3"]},
            {"Name": "vpc-endpoint-type", "Values": ["Gateway"]},
            {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
        ],
    )
    return [e for e in got if e.get("State", "").lower() not in ("deleted", "deleting", "failed")]


def ensure_s3_endpoint(ctx: Ctx, vpc_id: str, box_rt: str | None) -> str | None:
    """The free S3 gateway endpoint on the box route table, limited to ECR's image-layer bucket. Runtimes made
    after AgentCore's May 2026 change get no service-managed S3 path, so a microVM pulls its image through this
    VPC; this keeps the layers off the NAT (and its per-GB charge) and lets nothing else use S3 through it."""
    ec2 = ctx.aws.ec2
    want = s3_endpoint_policy(ctx.s.region)
    if not box_rt:
        pending(ctx, f"create the S3 gateway endpoint {S3_ENDPOINT_NAME} on devbox-rt-box")
        return None
    ep = find_one(find_s3_endpoints(ctx, vpc_id), "S3 gateway endpoint")
    if not ep:
        r = change(
            ctx,
            f"create the S3 gateway endpoint {S3_ENDPOINT_NAME} on devbox-rt-box (only s3:GetObject on "
            f"prod-{ctx.s.region}-starport-layer-bucket, ECR's image layers; free)",
            ec2.create_vpc_endpoint,
            **s3_endpoint_request(vpc_id, box_rt, ctx.s.region),
        )
        return r and r["VpcEndpoint"]["VpcEndpointId"]
    ep_id = ep["VpcEndpointId"]
    ok(f"S3 gateway endpoint {S3_ENDPOINT_NAME} ({ep_id}, {ep.get('State')})")
    if same_policy(ep.get("PolicyDocument") or "{}", want):
        ok("  only ECR's image layers (s3:GetObject on the starport layer bucket)")
    else:
        change(
            ctx,
            "  limit it to ECR's image layers (s3:GetObject on the starport layer bucket)",
            ec2.modify_vpc_endpoint,
            VpcEndpointId=ep_id,
            PolicyDocument=json.dumps(want),
        )
    tables = set(ep.get("RouteTableIds") or [])
    add, remove = sorted({box_rt} - tables), sorted(tables - {box_rt})
    if add or remove:
        change(
            ctx,
            f"  route only devbox-rt-box through it ({', '.join([f'+{t}' for t in add] + [f'-{t}' for t in remove])})",
            ec2.modify_vpc_endpoint,
            VpcEndpointId=ep_id,
            **({"AddRouteTableIds": add} if add else {}),
            **({"RemoveRouteTableIds": remove} if remove else {}),
        )
    else:
        ok("  on devbox-rt-box only")
    return ep_id


def nat_reason(ctx: Ctx, nat_id: str) -> str:
    n = (ctx.aws.ec2.describe_nat_gateways(NatGatewayIds=[nat_id]).get("NatGateways") or [{}])[0]
    return (
        " ".join(x for x in (n.get("State"), n.get("FailureCode"), n.get("FailureMessage")) if x) or "no reason given"
    )


def find_named(items: list[dict], name: str, key: str = "Name") -> dict | None:
    return next((i for i in items if i.get(key) == name), None)


def dns_list_domains(ctx: Ctx, list_id: str) -> list[str]:
    """A domain list's names as devbox.py writes them (the service may add a trailing dot)."""
    got = paged(ctx.aws.r53r.list_firewall_domains, "Domains", "NextToken", FirewallDomainListId=list_id)
    return [d.lower().rstrip(".") for d in got]


def ensure_domain_list(ctx: Ctx, name: str, domains: list[str], what: str) -> str | None:
    r53 = ctx.aws.r53r
    cur = find_named(paged(r53.list_firewall_domain_lists, "FirewallDomainLists", "NextToken"), name)
    if not cur:

        def create() -> str:
            lid = r53.create_firewall_domain_list(CreatorRequestId=request_id(name), Name=name, Tags=tag_list(name))[
                "FirewallDomainList"
            ]["Id"]
            r53.update_firewall_domains(FirewallDomainListId=lid, Operation="REPLACE", Domains=domains)
            return lid

        return change(ctx, f"create DNS Firewall domain list {name} ({what})", create)
    have = dns_list_domains(ctx, cur["Id"])
    if sorted(have) == sorted(domains):
        ok(f"DNS Firewall domain list {name} ({what})")
    else:
        added, removed = sorted(set(domains) - set(have)), sorted(set(have) - set(domains))
        change(
            ctx,
            f"update DNS Firewall domain list {name} ({', '.join([f'+{d}' for d in added] + [f'-{d}' for d in removed])})",
            r53.update_firewall_domains,
            FirewallDomainListId=cur["Id"],
            Operation="REPLACE",
            Domains=domains,
        )
    return cur["Id"]


def dns_rule_label(want: dict) -> str:
    if want["Action"] == "ALLOW":
        return "answer the allowlist, trusting its CNAME chain"
    return "NXDOMAIN for every other name"


def ensure_dns_firewall(ctx: Ctx, vpc_id: str | None) -> None:
    """Route 53 Resolver DNS Firewall. The VPC resolver sits on the VPC's local route, so DNS never passes
    Network Firewall, and the resolver answers any public name: without this, a DNS query is an unwatched way out
    (<data>.attacker.example). The allowlist is the same file as Network Firewall's."""
    r53 = ctx.aws.r53r
    domains = dns_domains(allowlist_domains(ctx))
    allow_id = ensure_domain_list(
        ctx, DNS_ALLOW_LIST, domains, f"{len(domains)} names from templates/egress-allowlist.txt"
    )
    any_id = ensure_domain_list(ctx, DNS_ANY_LIST, ["*"], "every other name")

    grp = find_named(paged(r53.list_firewall_rule_groups, "FirewallRuleGroups", "NextToken"), DNS_RULE_GROUP)
    if grp:
        gid = grp["Id"]
        ok(f"DNS Firewall rule group {DNS_RULE_GROUP} ({gid})")
        rules = paged(r53.list_firewall_rules, "FirewallRules", "NextToken", FirewallRuleGroupId=gid)
    else:
        r = change(
            ctx,
            f"create DNS Firewall rule group {DNS_RULE_GROUP}",
            r53.create_firewall_rule_group,
            CreatorRequestId=request_id(DNS_RULE_GROUP),
            Name=DNS_RULE_GROUP,
            Tags=tag_list(DNS_RULE_GROUP),
        )
        gid, rules = r and r["FirewallRuleGroup"]["Id"], []
    for want in dns_rules_wanted(allow_id, any_id):
        have = next(
            (
                r
                for r in rules
                if want["FirewallDomainListId"] and r.get("FirewallDomainListId") == want["FirewallDomainListId"]
            ),
            None,
        )
        if have and dns_rule_matches(have, want):
            ok(f"  rule {want['Priority']} {want['Name']}: {dns_rule_label(want)}")
        elif have:
            change(
                ctx,
                f"  set rule {want['Priority']} {want['Name']} to {dns_rule_label(want)}",
                r53.update_firewall_rule,
                FirewallRuleGroupId=gid,
                **want,
            )
        else:
            change(
                ctx,
                f"  add rule {want['Priority']} {want['Name']}: {dns_rule_label(want)}",
                r53.create_firewall_rule,
                CreatorRequestId=request_id(want["Name"]),
                FirewallRuleGroupId=gid,
                **want,
            )

    # Query logging: what DNS Firewall blocked (`network allowlist` lists it).
    ensure_log_group(ctx, DNS_LOG_GROUP, 90)
    dest = dns_query_log_destination(ctx.account, ctx.s.region)
    qlc = find_named(paged(r53.list_resolver_query_log_configs, "ResolverQueryLogConfigs", "NextToken"), DNS_QUERY_LOG)
    if qlc:
        qid = qlc["Id"]
        if qlc.get("DestinationArn") and qlc["DestinationArn"].rstrip(":*") != dest.rstrip(":*"):
            warn(
                f"query logging {DNS_QUERY_LOG} writes to {qlc['DestinationArn']}, not {DNS_LOG_GROUP}: `network allowlist` won't see it"
            )
        else:
            ok(f"Resolver query logging {DNS_QUERY_LOG} → {DNS_LOG_GROUP}")
    else:
        r = change(
            ctx,
            f"create Resolver query logging {DNS_QUERY_LOG} → {DNS_LOG_GROUP}",
            r53.create_resolver_query_log_config,
            Name=DNS_QUERY_LOG,
            DestinationArn=dest,
            CreatorRequestId=request_id(DNS_QUERY_LOG),
            Tags=tag_list(DNS_QUERY_LOG),
        )
        qid = r and r["ResolverQueryLogConfig"]["Id"]
        if qid:
            wait_for(
                f"query logging {DNS_QUERY_LOG}",
                lambda: r53.get_resolver_query_log_config(ResolverQueryLogConfigId=qid)["ResolverQueryLogConfig"][
                    "Status"
                ],
                lambda v: v == "CREATED",
                failed=lambda v: v == "FAILED",
                every=5,
                timeout=300,
            )

    if not vpc_id:
        for what in (
            f"attach {DNS_RULE_GROUP} to the VPC's resolver (fail closed)",
            f"log the VPC's DNS queries to {DNS_LOG_GROUP}",
        ):
            todo(f"would {what}")
            Report.changes += 1
        return
    assoc = next(
        (
            a
            for a in paged(
                r53.list_firewall_rule_group_associations, "FirewallRuleGroupAssociations", "NextToken", VpcId=vpc_id
            )
            if gid and a.get("FirewallRuleGroupId") == gid
        ),
        None,
    )
    if assoc:
        ok(f"  {DNS_RULE_GROUP} filters every DNS query in the VPC ({assoc.get('Status', '?')})")
    else:
        change(
            ctx,
            f"  attach {DNS_RULE_GROUP} to the VPC's resolver (every DNS query the box makes goes through it)",
            r53.associate_firewall_rule_group,
            CreatorRequestId=request_id("assoc"),
            FirewallRuleGroupId=gid,
            VpcId=vpc_id,
            Priority=DNS_ASSOCIATION_PRIORITY,
            Name=DNS_RULE_GROUP,
            MutationProtection="DISABLED",
            Tags=tag_list(DNS_RULE_GROUP),
        )
    try:
        fail_open = (r53.get_firewall_config(ResourceId=vpc_id).get("FirewallConfig") or {}).get(
            "FirewallFailOpen", "DISABLED"
        )
    except ClientError as e:
        if not is_missing(e):
            raise
        fail_open = "DISABLED"
    if fail_open == "DISABLED":
        ok("  fails closed (no answer while DNS Firewall can't be reached)")
    else:
        change(
            ctx,
            f"  make DNS Firewall fail closed (FirewallFailOpen {fail_open} → DISABLED)",
            r53.update_firewall_config,
            ResourceId=vpc_id,
            FirewallFailOpen="DISABLED",
        )

    qa = next(
        (
            a
            for a in paged(
                r53.list_resolver_query_log_config_associations,
                "ResolverQueryLogConfigAssociations",
                "NextToken",
                Filters=[{"Name": "ResourceId", "Values": [vpc_id]}],
            )
            if qid and a.get("ResolverQueryLogConfigId") == qid and a.get("ResourceId") == vpc_id
        ),
        None,
    )
    if not qa:
        r = change(
            ctx,
            f"  log the VPC's DNS queries to {DNS_LOG_GROUP}",
            r53.associate_resolver_query_log_config,
            ResolverQueryLogConfigId=qid,
            ResourceId=vpc_id,
        )
        if not r:
            return
        aid = r["ResolverQueryLogConfigAssociation"]["Id"]
        qa = wait_for(
            "DNS query logging",
            lambda: r53.get_resolver_query_log_config_association(ResolverQueryLogConfigAssociationId=aid)[
                "ResolverQueryLogConfigAssociation"
            ],
            lambda v: v and v["Status"] != "CREATING",
            every=5,
            timeout=300,
        )
        if qa["Status"] == "ACTIVE":
            return
    if qa["Status"] in ("ACTION_NEEDED", "FAILED"):
        bad(
            f"  DNS query logging is {qa['Status']}: {qa.get('Error', '')} {qa.get('ErrorMessage', '')}".rstrip()
            + " (without it `network allowlist` can't list the DNS names it blocked)"
        )
    else:
        ok(f"  DNS queries → {DNS_LOG_GROUP} ({qa['Status']})")


def ensure_log_group(ctx: Ctx, name: str, days: int) -> None:
    logs = ctx.aws.logs
    have = [g for g in logs.describe_log_groups(logGroupNamePrefix=name)["logGroups"] if g["logGroupName"] == name]
    if not have:

        def create():
            logs.create_log_group(logGroupName=name, tags=tags_map())
            logs.put_retention_policy(logGroupName=name, retentionInDays=days)

        change(ctx, f"create log group {name} ({days} days)", create)
    elif have[0].get("retentionInDays") != days:
        change(ctx, f"keep {name} for {days} days", logs.put_retention_policy, logGroupName=name, retentionInDays=days)
    else:
        ok(f"log group {name} ({days} days)")


# ============================================================================= 6 the provisioner (group-driven boxes)
def box_plan(ctx: Ctx) -> BoxPlan | None:
    """What every box shares, once deploy has made it all (None until then)."""
    vals = {
        "account": ctx.account,
        "file_system_id": ctx.efs.get("id"),
        "subnet_id": ctx.net.get("devbox-box"),
        "security_group_id": ctx.net.get("sg"),
        "image_uri": ctx.images.get("box"),
        "gateway_url": ctx.gateway.get("url"),
        "start_url": ctx.start_url,
        "boundary_arn": ctx.roles.get(EXEC_BOUNDARY),
    }
    return BoxPlan(**vals) if all(vals.values()) else None


def ensure_box_table(ctx: Ctx) -> bool:
    ddb = ctx.aws.ddb
    try:
        t = ddb.describe_table(TableName=BOX_TABLE)["Table"]
    except ClientError as e:
        if not is_missing(e):
            raise
        t = None
    if t:
        if t["TableStatus"] != "ACTIVE" and not ctx.check:
            wait_for(
                f"table {BOX_TABLE}",
                lambda: ddb.describe_table(TableName=BOX_TABLE)["Table"]["TableStatus"],
                lambda v: v == "ACTIVE",
                every=3,
                timeout=300,
            )
        ok(
            f"table {BOX_TABLE}: who has which box (on demand{', deletion protection' if t.get('DeletionProtectionEnabled') else ''})"
        )
        return True
    r = change(
        ctx,
        f"create table {BOX_TABLE} (on demand, deletion protection): one record per person with a box",
        ddb.create_table,
        **box_table_request(),
    )
    if r:
        wait_for(
            f"table {BOX_TABLE}",
            lambda: ddb.describe_table(TableName=BOX_TABLE)["Table"]["TableStatus"],
            lambda v: v == "ACTIVE",
            every=3,
            timeout=300,
        )
    return bool(r)


def ensure_boundary(ctx: Ctx) -> str | None:
    """The permissions boundary on every execution role the provisioner makes: what any box may ever do."""
    iam, fs_id = ctx.aws.iam, ctx.efs.get("id")
    arn = f"arn:aws:iam::{ctx.account}:policy/{EXEC_BOUNDARY}"
    if not fs_id:
        pending(ctx, f"create the permissions boundary {EXEC_BOUNDARY} (it names the file system)")
        return None
    want = exec_boundary(ctx.account, ctx.s.region, fs_id)
    try:
        cur = iam.get_policy(PolicyArn=arn)["Policy"]
    except ClientError as e:
        if not is_missing(e):
            raise
        cur = None
    if cur is None:
        r = change(
            ctx,
            f"create the permissions boundary {EXEC_BOUNDARY} (box image, box logs, the box file system only)",
            iam.create_policy,
            PolicyName=EXEC_BOUNDARY,
            PolicyDocument=json.dumps(want),
            Tags=tag_list(),
            Description="Dev box: the most any box's execution role may do",
        )
        return arn if (r or ctx.check) else None  # check mode: go on, so it lists what the provisioner needs too
    have = iam.get_policy_version(PolicyArn=arn, VersionId=cur["DefaultVersionId"])["PolicyVersion"]["Document"]
    if same_policy(have, want):
        ok(f"permissions boundary {EXEC_BOUNDARY}")
    else:
        versions = iam.list_policy_versions(PolicyArn=arn)["Versions"]
        if len(versions) >= 5:
            oldest = min((v for v in versions if not v["IsDefaultVersion"]), key=lambda v: v["CreateDate"])
            change(
                ctx,
                f"  drop {EXEC_BOUNDARY} version {oldest['VersionId']} (IAM keeps 5)",
                iam.delete_policy_version,
                PolicyArn=arn,
                VersionId=oldest["VersionId"],
            )
        change(
            ctx,
            f"update the permissions boundary {EXEC_BOUNDARY}",
            iam.create_policy_version,
            PolicyArn=arn,
            PolicyDocument=json.dumps(want),
            SetAsDefault=True,
        )
    return arn


def ensure_provisioner(ctx: Ctx) -> None:
    """The table, the boundary, the provisioner Lambda, and the HTTP API the page calls (POST /api/box)."""
    s, lam, api = ctx.s, ctx.aws.lam, ctx.aws.apigw
    section(
        f"6 · The provisioner: a box for each member of {s.okta_group} on their first visit (POST {PROVISION_PATH})"
    )
    try:
        ctx.aws.ddb.describe_table(TableName=BOX_TABLE)
        first_time = False
    except ClientError as e:
        if not is_missing(e):
            raise
        first_time = True
    ensure_box_table(ctx)
    if first_time:  # the first deploy with the provisioner: the one Okta change it needs
        claim = "^(" + "|".join(re.escape(g) for g in [s.okta_group, *sorted(s.tier_groups.values())]) + ")$"
        note = (
            f"Okta: set the Dev Box groups claim's filter to Matches regex {claim} (Okta step 4 below). The provisioner reads "
            "the person's tier group from their token: until the claim carries it, nobody can open a box, Ada and Grace included."
        )
        ctx.notes.append(note)
        warn(note)
    boundary = ensure_boundary(ctx)
    if boundary:
        ctx.roles[EXEC_BOUNDARY] = boundary
    fs_id, subnet, sg = ctx.efs.get("id"), ctx.net.get("devbox-box"), ctx.net.get("sg")
    role_arn = (
        ensure_role(
            ctx,
            PROVISIONER_ROLE,
            provisioner_spec(ctx.account, s.region, fs_id, boundary, subnet_id=subnet, security_group_id=sg),
        )
        if fs_id and boundary and subnet and sg
        else None
    )
    if not role_arn:
        pending(ctx, f"create role {PROVISIONER_ROLE}")
    ensure_log_group(ctx, PROVISIONER_LOG_GROUP, 30)
    plan = box_plan(ctx)
    ctx.plan = plan
    if not (plan and role_arn and s.okta_client_id):
        what = "DEVBOX_OKTA_CLIENT_ID" if plan and role_arn else "the network, storage, gateway and box image"
        (todo if ctx.check else warn)(f"Lambda {PROVISIONER_FUNCTION} waits for {what}")
        Report.changes += ctx.check
        return
    code, env = provisioner_zip(), provisioner_env(s, plan)
    try:
        conf = lam.get_function(FunctionName=PROVISIONER_FUNCTION)["Configuration"]
    except ClientError as e:
        if not is_missing(e):
            raise
        conf = None
    if conf is None:
        r = change(
            ctx,
            f"create Lambda {PROVISIONER_FUNCTION} ({PROVISIONER_RUNTIME}, arm64, {len(code) // 1_000_000} MB of code)",
            retry_iam,
            lam.create_function,
            **provisioner_create_request(role_arn, code, env),
        )
        if r:
            wait_lambda(ctx, "function_active_v2", PROVISIONER_FUNCTION)
        fn_arn = r and r["FunctionArn"]
    else:
        fn_arn = conf["FunctionArn"]
        if conf.get("CodeSha256") != code_sha256(code):
            change(
                ctx,
                f"update Lambda {PROVISIONER_FUNCTION}'s code",
                lam.update_function_code,
                FunctionName=PROVISIONER_FUNCTION,
                ZipFile=code,
            )
            wait_lambda(ctx, fn=PROVISIONER_FUNCTION)
        else:
            ok(f"Lambda {PROVISIONER_FUNCTION} (code {conf['CodeSha256'][:12]}…)")
        have = (conf.get("Environment") or {}).get("Variables", {})
        if (have, conf.get("Role"), conf.get("Timeout"), conf.get("MemorySize"), conf.get("Runtime")) != (
            env,
            role_arn,
            PROVISIONER_TIMEOUT_S,
            PROVISIONER_MEMORY_MB,
            PROVISIONER_RUNTIME,
        ):
            change(
                ctx,
                f"  set {PROVISIONER_FUNCTION}'s settings (what every box shares: image, network, folder, gateway)",
                lam.update_function_configuration,
                FunctionName=PROVISIONER_FUNCTION,
                Role=role_arn,
                Timeout=PROVISIONER_TIMEOUT_S,
                MemorySize=PROVISIONER_MEMORY_MB,
                Runtime=PROVISIONER_RUNTIME,
                Environment={"Variables": env},
            )
            wait_lambda(ctx, fn=PROVISIONER_FUNCTION)
    if fn_arn:
        try:
            cur = lam.get_function_concurrency(FunctionName=PROVISIONER_FUNCTION).get("ReservedConcurrentExecutions")
        except ClientError as e:
            if not is_missing(e):
                raise
            cur = None
        if cur == PROVISIONER_CONCURRENCY:
            ok(f"  at most {PROVISIONER_CONCURRENCY} at once")
        else:
            change(
                ctx,
                f"  allow at most {PROVISIONER_CONCURRENCY} at once",
                lam.put_function_concurrency,
                FunctionName=PROVISIONER_FUNCTION,
                ReservedConcurrentExecutions=PROVISIONER_CONCURRENCY,
            )

    # The HTTP API: one route, POST /api/box, behind Okta's JWT
    found = next(
        (a for a in paged(api.get_apis, "Items", "NextToken", send="NextToken") if a["Name"] == API_NAME), None
    )
    if found:
        api_id, endpoint = found["ApiId"], found["ApiEndpoint"]
        ok(f"HTTP API {API_NAME} ({endpoint})")
    else:
        r = change(
            ctx,
            f"create HTTP API {API_NAME}",
            api.create_api,
            Name=API_NAME,
            ProtocolType="HTTP",
            Description="Dev box: POST /api/box (the provisioner), Okta JWT only",
            Tags=tags_map(),
        )
        if not r:
            pending(ctx, f"  add the JWT authorizer, the {PROVISION_ROUTE} route and the stage")
            return
        api_id, endpoint = r["ApiId"], r["ApiEndpoint"]
    want_auth = api_authorizer_request(api_id, s)
    auth = next(
        (
            a
            for a in paged(api.get_authorizers, "Items", "NextToken", send="NextToken", ApiId=api_id)
            if a["Name"] == "okta"
        ),
        None,
    )
    if (
        auth
        and auth.get("IdentitySource") == want_auth["IdentitySource"]
        and auth.get("JwtConfiguration") == want_auth["JwtConfiguration"]
    ):
        ok(f"  JWT authorizer: issuer {s.okta_issuer}, audience {s.okta_audience}, token in {TOKEN_HEADER}")
        auth_id = auth["AuthorizerId"]
    elif auth:
        change(
            ctx,
            "  update the JWT authorizer",
            api.update_authorizer,
            AuthorizerId=auth["AuthorizerId"],
            **{k: v for k, v in want_auth.items()},
        )
        auth_id = auth["AuthorizerId"]
    else:
        r = change(
            ctx,
            f"  add the JWT authorizer (issuer {s.okta_issuer}, audience {s.okta_audience}, token in {TOKEN_HEADER})",
            api.create_authorizer,
            **want_auth,
        )
        auth_id = r and r["AuthorizerId"]
    integ = next(
        (
            i
            for i in paged(api.get_integrations, "Items", "NextToken", send="NextToken", ApiId=api_id)
            if i.get("IntegrationUri") == fn_arn
        ),
        None,
    )
    if integ:
        integ_id = integ["IntegrationId"]
        ok(f"  integration: Lambda {PROVISIONER_FUNCTION}")
    else:
        r = change(
            ctx,
            f"  integrate Lambda {PROVISIONER_FUNCTION}",
            api.create_integration,
            **api_integration_request(api_id, fn_arn),
        )
        integ_id = r and r["IntegrationId"]
    if auth_id and integ_id:
        want_route = api_route_request(api_id, auth_id, integ_id)
        route = next(
            (
                r
                for r in paged(api.get_routes, "Items", "NextToken", send="NextToken", ApiId=api_id)
                if r["RouteKey"] == PROVISION_ROUTE
            ),
            None,
        )
        same = route and all(
            route.get(k) == want_route[k]
            for k in ("AuthorizationType", "AuthorizerId", "AuthorizationScopes", "Target")
        )
        if same:
            ok(f"  route {PROVISION_ROUTE}: JWT, scope {DEVBOX_SCOPE}")
        elif route:
            change(ctx, f"  update route {PROVISION_ROUTE}", api.update_route, RouteId=route["RouteId"], **want_route)
        else:
            change(ctx, f"  add route {PROVISION_ROUTE} (JWT, scope {DEVBOX_SCOPE})", api.create_route, **want_route)
    stage = next((st for st in api.get_stages(ApiId=api_id).get("Items") or [] if st["StageName"] == API_STAGE), None)
    want_rs = {k: API_THROTTLE[k] for k in API_THROTTLE}
    if stage and {k: (stage.get("DefaultRouteSettings") or {}).get(k) for k in want_rs} == want_rs:
        ok(
            f"  stage {API_STAGE} (auto-deploy, {API_THROTTLE['ThrottlingRateLimit']:g} requests/s, burst {API_THROTTLE['ThrottlingBurstLimit']})"
        )
    elif stage:
        change(
            ctx,
            f"  throttle stage {API_STAGE}",
            api.update_stage,
            ApiId=api_id,
            StageName=API_STAGE,
            DefaultRouteSettings=want_rs,
        )
    else:
        change(
            ctx,
            f"  add stage {API_STAGE} (auto-deploy, throttled)",
            api.create_stage,
            ApiId=api_id,
            StageName=API_STAGE,
            AutoDeploy=True,
            DefaultRouteSettings=want_rs,
            Tags=tags_map(),
        )
    if fn_arn:
        req = api_permission_request(ctx.account, s.region, api_id)
        try:
            statements = json.loads(lam.get_policy(FunctionName=PROVISIONER_FUNCTION)["Policy"]).get("Statement", [])
        except ClientError as e:
            if not is_missing(e):
                raise
            statements = []
        if permission_present(statements, req):
            ok(f"  API Gateway ({API_NAME}) may call {PROVISIONER_FUNCTION}")
        else:
            if any(st.get("Sid") == req["StatementId"] for st in statements):
                change(
                    ctx,
                    f"  drop the stale {req['StatementId']} permission",
                    lam.remove_permission,
                    FunctionName=PROVISIONER_FUNCTION,
                    StatementId=req["StatementId"],
                )
            change(ctx, f"  let API Gateway ({API_NAME}) call {PROVISIONER_FUNCTION}", lam.add_permission, **req)
    ctx.edge["apiDomain"] = urllib.parse.urlparse(endpoint).hostname or ""


# ============================================================================= 7 the boxes
def find_runtime(ctx: Ctx, name: str) -> dict | None:
    return next(
        (r for r in paged(ctx.aws.acc.list_agent_runtimes, "agentRuntimes") if r["agentRuntimeName"] == name), None
    )


def migrate_state_boxes(ctx: Ctx) -> None:
    """Boxes deploy made for named people before boxes were group-driven (.state.json's boxes) get a record, so the provisioner
    finds them by uid and their owners keep their box, its name and its folder."""
    ddb = ctx.aws.ddb
    for name, b in sorted((ctx.state.get("boxes") or {}).items()):
        if is_legacy_record(b) or not (b.get("uid") and b.get("runtimeId")) or get_box(ddb, uid_key(b["uid"])):
            continue
        try:
            env = ctx.aws.acc.get_agent_runtime(agentRuntimeId=b["runtimeId"]).get("environmentVariables") or {}
        except ClientError as e:
            if not is_missing(e):
                raise
            continue
        tier = env.get("DEVBOX_TIER") if env.get("DEVBOX_TIER") in TIER_MODELS else "Standard"
        rec = {
            "key": uid_key(b["uid"]),
            "uid": b["uid"],
            "name": name,
            "login": "",
            "tier": tier,
            "generation": int(b.get("generation") or 1),
            "createdAt": int(time.time()),
            **{
                k: b[k]
                for k in ("accessPointId", "accessPointArn", "execRoleArn", "runtimeId", "runtimeArn")
                if b.get(k)
            },
        }
        change(
            ctx,
            f"  record {name}'s box ({tier}) in {BOX_TABLE}: the provisioner finds it by their Okta uid",
            put_box,
            ddb,
            rec,
        )


def ensure_boxes(ctx: Ctx) -> None:
    section(
        "7 · The boxes: each made on its owner's first visit; deploy brings them all up to date (a new image, a tier change)"
    )
    plan, ddb = ctx.plan, ctx.aws.ddb
    try:
        ddb.describe_table(TableName=BOX_TABLE)
    except ClientError as e:
        if not is_missing(e):
            raise
        pending(ctx, f"  read the boxes from {BOX_TABLE}")
        return
    migrate_state_boxes(ctx)
    records = [] if ctx.check and Report.changes and not plan else box_records(ddb)
    if not records:
        ok(f"no boxes yet: each member of {ctx.s.okta_group} gets theirs the first time they open the workbench")

    def act(what, fn, **kw):
        """Like change(), but an AWS error goes back to advance_box, which knows the ones to wait out (a conflict, a new role)."""
        if ctx.check:
            return change(ctx, f"    {what}", fn, **kw)
        Report.changes += 1
        out = retry_iam(fn, **kw)
        did(f"    {what}")
        return out

    cl = {"efs": ctx.aws.efs, "iam": ctx.aws.iam, "acc": ctx.aws.acc}
    boxes: dict[
        str, dict
    ] = {}  # .state.json's boxes: rebuilt from the table, so a box that's gone doesn't linger there
    for rec in records:
        say(f"  {rec['name']} ({rec['tier']})")
        if not rec.get(
            "runtimeId"
        ):  # making a box is the provisioner's job: deploy doing it (as admin) would hide its failures
            say(
                f"    not made yet{': ' + rec['message'] if rec.get('message') else ''}. The provisioner makes it on "
                f"{rec['name']}'s next visit; deploy only updates boxes that exist"
            )
            continue
        if not plan:
            pending(
                ctx, f"    bring {rec['name']}'s box up to date (it needs the network, storage, gateway and box image)"
            )
            continue
        before = json.dumps({k: v for k, v in rec.items() if k != "updatedAt"}, sort_keys=True, default=str)
        for _ in range(90):  # at most about 15 minutes: a runtime update, then MMDSv2
            try:
                res = advance_box(cl, ctx.s, plan, rec, act=act)
            except ClientError as e:
                res = {"state": "failed", "step": rec.get("step", "?"), "message": f"{err_code(e)}: {err_text(e)}"}
            if res["state"] != "working" or ctx.check:
                break
            SLEEP(10)
        if res["state"] == "ready":
            ok(
                f"    ready: runtime {box_user(rec).runtime_name}, folder {box_user(rec).efs_root}, generation {rec.get('generation', 1)}"
            )
        elif res["state"] == "failed":
            bad(f"    {rec['name']}'s box: {res['message']}")
        elif res["state"] == "working" and not ctx.check:
            warn(f"    still {res['message']}: the next deploy, or {rec['name']}'s next visit, carries on")
        if rec.get("mmdsv2Rejected"):
            warn(f"    MMDSv2 isn't required: AgentCore refused it ({rec['mmdsv2Rejected']}); spike item 7")
        if (
            not ctx.check
            and json.dumps({k: v for k, v in rec.items() if k != "updatedAt"}, sort_keys=True, default=str) != before
        ):
            rec["updatedAt"] = int(time.time())
            put_box(ddb, rec)
        boxes[rec["name"]] = state_box(rec)
    if not (ctx.check and not records):
        ctx.state["boxes"] = boxes
    cps, rts = legacy_instances(ctx)
    if cps or rts:
        names = ", ".join(sorted({c["name"] for c in cps} | {r["agentRuntimeName"] for r in rts}))
        note = (
            f"The old Instances boxes are still there ({len(cps)} capacity provider(s) and {len(rts)} runtime(s): {names}); "
            "deploy never touches them, and their volumes bill while stopped. `uv run deploy/devbox.py retire-instances` "
            "deletes them."
        )
        warn(note)
        ctx.notes.append(note)


def find_capacity_provider(ctx: Ctx, name: str) -> dict | None:
    return next((c for c in paged(ctx.aws.acc.list_capacity_providers, "capacityProviders") if c["name"] == name), None)


def wait_runtime(ctx: Ctx, runtime_id: str, what: str) -> dict:
    """Wait for READY and return the runtime. A failure says its status and failureReason (not the whole runtime)."""
    acc, got = ctx.aws.acc, {}

    def probe():
        got["runtime"] = acc.get_agent_runtime(agentRuntimeId=runtime_id)
        return got["runtime"]["status"]

    wait_for(
        what,
        probe,
        lambda v: v == "READY",
        failed=failed_status,
        timeout=900,
        every=10,
        reason=lambda: acc.get_agent_runtime(agentRuntimeId=runtime_id).get("failureReason", ""),
    )
    return got["runtime"]


def mmdsv2_refused(e: ClientError) -> bool:
    """AgentCore saying no to requireMMDSV2 itself, as opposed to a conflict or a hiccup worth retrying."""
    return err_code(e) == "ValidationException" and bool(re.search(r"metadata|mmds", err_text(e), re.IGNORECASE))


def legacy_instances(ctx: Ctx) -> tuple[list[dict], list[dict]]:
    """The old Instances boxes still in the account: (capacity providers, runtimes) named devbox_<name>."""
    acc = ctx.aws.acc
    cps = [c for c in paged(acc.list_capacity_providers, "capacityProviders") if LEGACY_NAME.match(c["name"])]
    rts = [r for r in paged(acc.list_agent_runtimes, "agentRuntimes") if LEGACY_NAME.match(r["agentRuntimeName"])]
    return cps, rts


# ============================================================================= 8 edge
def find_distribution(ctx: Ctx, site: str) -> dict | None:
    cf, marker, found = ctx.aws.cf, None, None
    while True:
        dl = (cf.list_distributions(Marker=marker) if marker else cf.list_distributions())["DistributionList"]
        for d in dl.get("Items") or []:
            if d.get("Comment") == distribution_comment(site):
                found = d
        if not dl.get("IsTruncated"):
            return found
        marker = dl["NextMarker"]


def find_oac(ctx: Ctx) -> dict | None:
    cf, marker = ctx.aws.cf, None
    while True:
        ol = (cf.list_origin_access_controls(Marker=marker) if marker else cf.list_origin_access_controls())[
            "OriginAccessControlList"
        ]
        for o in ol.get("Items") or []:
            if o["Name"] == OAC_NAME:
                return o
        if not ol.get("IsTruncated"):
            return None
        marker = ol["NextMarker"]


def lambda_reason(ctx: Ctx, fn: str = EDGE_FUNCTION) -> str:
    c = ctx.aws.lam.get_function_configuration(FunctionName=fn)
    return (
        f"State {c.get('State', '?')}: {c.get('StateReason') or '-'}; "
        f"last update {c.get('LastUpdateStatus', '?')}: {c.get('LastUpdateStatusReason') or '-'}"
    )


def wait_lambda(ctx: Ctx, waiter: str = "function_updated_v2", fn: str = EDGE_FUNCTION) -> None:
    if not ctx.check:
        wait_waiter(ctx.aws.lam, waiter, f"Lambda {fn}", lambda: lambda_reason(ctx, fn), FunctionName=fn)


def ensure_edge(ctx: Ctx) -> None:
    s, lam, cf = ctx.s, ctx.aws.lam, ctx.aws.cf
    section(
        f"8 · Edge: the static Lambda behind two CloudFront distributions (workbench, webview); {API_PATH} to the provisioner"
    )
    ensure_log_group(ctx, EDGE_LOG_GROUP, 30)
    role_arn, image = ctx.roles.get(EDGE_ROLE) or f"arn:aws:iam::{ctx.account}:role/{EDGE_ROLE}", ctx.images.get("edge")
    try:
        fn = lam.get_function(FunctionName=EDGE_FUNCTION)
    except ClientError as e:
        if not is_missing(e):
            raise
        fn = None
    if not fn:
        if image:
            change(
                ctx,
                f"create Lambda {EDGE_FUNCTION} (image {image.rsplit(':', 1)[-1]}, arm64, {EDGE_MEMORY_MB} MB)",
                retry_iam,
                lam.create_function,
                **lambda_create_request(role_arn, image, lambda_env(browser_config(s, ""), "", "")),
            )
            wait_lambda(ctx, "function_active_v2")
        else:
            pending(ctx, f"create Lambda {EDGE_FUNCTION}")
    else:
        conf = fn["Configuration"]
        failed = conf.get("State") == "Failed" or conf.get("LastUpdateStatus") == "Failed"
        if failed:
            (warn if image else bad)(
                f"Lambda {EDGE_FUNCTION} is {conf.get('State')} (last update {conf.get('LastUpdateStatus')}): "
                f"{conf.get('StateReason') or conf.get('LastUpdateStatusReason') or 'no reason given'}"
            )
        if image and (fn.get("Code", {}).get("ImageUri") != image or failed):
            same = fn.get("Code", {}).get("ImageUri") == image
            change(
                ctx,
                f"{'apply' if same else 'update'} Lambda {EDGE_FUNCTION} {'image' if same else 'to image'} "
                f"{image.rsplit(':', 1)[-1]}{' again' if same else ''}",
                lam.update_function_code,
                FunctionName=EDGE_FUNCTION,
                ImageUri=image,
            )
            wait_lambda(ctx)
        elif not failed:
            ok(f"Lambda {EDGE_FUNCTION} (image {fn.get('Code', {}).get('ImageUri', '?').rsplit(':', 1)[-1]})")
        if (conf.get("MemorySize"), conf.get("Timeout"), conf.get("Role")) != (
            EDGE_MEMORY_MB,
            EDGE_TIMEOUT_S,
            role_arn,
        ):
            change(
                ctx,
                f"  set memory {EDGE_MEMORY_MB} MB, timeout {EDGE_TIMEOUT_S} s, role {EDGE_ROLE}",
                lam.update_function_configuration,
                FunctionName=EDGE_FUNCTION,
                MemorySize=EDGE_MEMORY_MB,
                Timeout=EDGE_TIMEOUT_S,
                Role=role_arn,
            )
            wait_lambda(ctx)
    exists = bool(fn) or not ctx.check

    url = None
    if exists:
        try:
            u = lam.get_function_url_config(FunctionName=EDGE_FUNCTION)
            url = u["FunctionUrl"]
            if (u["AuthType"], u.get("InvokeMode", "BUFFERED")) == ("AWS_IAM", EDGE_INVOKE_MODE):
                ok(f"  function URL (AWS_IAM, {EDGE_INVOKE_MODE}): only CloudFront can call it")
            else:
                change(
                    ctx,
                    f"  set the function URL to AWS_IAM, {EDGE_INVOKE_MODE}",
                    lam.update_function_url_config,
                    FunctionName=EDGE_FUNCTION,
                    AuthType="AWS_IAM",
                    InvokeMode=EDGE_INVOKE_MODE,
                )
        except ClientError as e:
            if not is_missing(e):
                raise
            r = change(
                ctx,
                f"  create the function URL (AWS_IAM, {EDGE_INVOKE_MODE})",
                lam.create_function_url_config,
                **function_url_request(),
            )
            url = r and r["FunctionUrl"]
    else:
        pending(ctx, "  create the function URL (AWS_IAM)")

    oac = find_oac(ctx)
    oac_id = oac and oac["Id"]
    if oac_id:
        ok(f"origin access control {OAC_NAME} ({oac_id})")
    else:
        r = change(
            ctx,
            f"create origin access control {OAC_NAME} (lambda, always sign, SigV4)",
            cf.create_origin_access_control,
            **oac_request(),
        )
        oac_id = r and r["OriginAccessControl"]["Id"]

    domains: dict[str, str] = {}
    for site in SITES:
        d = find_distribution(ctx, site)
        want = distribution_config(
            site, origin_domain(url) if url else "", oac_id or "", api_domain=ctx.edge.get("apiDomain", "")
        )
        if d:
            domains[site] = d["DomainName"]
            ctx.edge[f"{site}Id"], ctx.edge[f"{site}Arn"] = d["Id"], d["ARN"]
            if url and oac_id:
                got = cf.get_distribution_config(Id=d["Id"])
                drift = distribution_drift(got["DistributionConfig"], want)
                if drift:
                    merged = {
                        **got["DistributionConfig"],
                        **{
                            k: want[k]
                            for k in ("Origins", "DefaultCacheBehavior", "CacheBehaviors", "Enabled", "Logging")
                        },
                    }
                    change(
                        ctx,
                        f"  update distribution {site} ({', '.join(drift)})",
                        cf.update_distribution,
                        Id=d["Id"],
                        IfMatch=got["ETag"],
                        DistributionConfig=merged,
                    )
                else:
                    ok(f"distribution {site}: https://{d['DomainName']} ({d['Status']})")
        elif url and oac_id:
            # CloudFront remembers a CallerReference, so a distribution made again after an undeploy needs a new one.
            want["CallerReference"] = f"devbox-{site}-{int(time.time())}"
            r = change(
                ctx,
                f"create distribution {site} (default *.cloudfront.net certificate; deploys in a few minutes)",
                cf.create_distribution_with_tags,
                DistributionConfigWithTags={"DistributionConfig": want, "Tags": {"Items": tag_list(f"devbox-{site}")}},
            )
            if r:
                domains[site] = r["Distribution"]["DomainName"]
                ctx.edge[f"{site}Id"], ctx.edge[f"{site}Arn"] = r["Distribution"]["Id"], r["Distribution"]["ARN"]
        else:
            pending(ctx, f"create distribution {site}")

    if fn or not ctx.check:
        try:
            statements = json.loads(lam.get_policy(FunctionName=EDGE_FUNCTION)["Policy"]).get("Statement", [])
        except ClientError as e:
            if not is_missing(e):
                raise
            statements = []
        for site in SITES:
            arn = ctx.edge.get(f"{site}Arn")
            if not arn:
                continue
            for req in lambda_permission_requests(site, arn):
                if permission_present(statements, req):
                    ok(f"  CloudFront ({site}) may call {req['Action']}")
                    continue
                if any(st.get("Sid") == req["StatementId"] for st in statements):
                    change(
                        ctx,
                        f"  drop the stale {req['StatementId']} permission",
                        lam.remove_permission,
                        FunctionName=EDGE_FUNCTION,
                        StatementId=req["StatementId"],
                    )
                change(ctx, f"  let CloudFront ({site}) call {req['Action']}", lam.add_permission, **req)

    ctx.edge.update({f"{site}Domain": dom for site, dom in domains.items()})
    ctx.state["edge"] = dict(ctx.edge)
    wb, last = domains.get("workbench"), ctx.state.get("lastWorkbenchDomain")
    if wb and last and wb != last:
        note = okta_domain_change(last, wb)
        ctx.notes.append(note)
        warn(note)
    if wb:
        ctx.state["lastWorkbenchDomain"] = wb  # kept through a plain undeploy, to catch exactly this
    if fn or not ctx.check:
        env = lambda_env(
            browser_config(s, domains.get("webview", "")), domains.get("workbench", ""), domains.get("webview", "")
        )
        have = (
            {}
            if not fn
            else (lam.get_function_configuration(FunctionName=EDGE_FUNCTION).get("Environment") or {}).get(
                "Variables", {}
            )
        )
        if have == env:
            ok(f"  browser config: names nobody (the page asks {PROVISION_PATH}), both origins")
        else:
            change(
                ctx,
                f"  set the browser config (no boxes listed: the page asks {PROVISION_PATH}) and both origins",
                lam.update_function_configuration,
                FunctionName=EDGE_FUNCTION,
                Environment={"Variables": env},
            )
            wait_lambda(ctx)


# ============================================================================= commands
def make_ctx(args, *, check: bool) -> Ctx:
    env = Path(args.env)
    if not env.is_file():  # a fresh clone: devbox.env is git-ignored, the example is in the repo
        sys.exit(
            f"No settings file at {env}. Copy deploy/devbox.env.example to deploy/devbox.env and fill in your Okta domain."
        )
    s = load_settings(parse_env_file(env.read_text()))
    return Ctx(s=s, aws=Aws(s), state=load_state(), check=check)


def deploy_all(ctx: Ctx) -> None:
    ensure_images(ctx)
    ensure_iam(ctx)
    ensure_gateway(ctx)
    ensure_storage(
        ctx
    )  # before the network: the allowlist names the file system, and its policy is set before any mount target
    ensure_network(ctx)
    ensure_provisioner(ctx)  # the table, the boundary, the provisioner and its API: what makes each person's box
    ensure_boxes(ctx)  # the boxes already made: a new image or a tier change reaches them now, not on the next visit
    ensure_edge(ctx)


def cmd_deploy(ctx: Ctx) -> int:
    say(
        f"{_c('1;37')}Dev box · {'check' if ctx.check else 'deploy'}{_c('0')}"
        + (f"  {_c('33')}(check only: nothing is changed){_c('0')}" if ctx.check else "")
    )
    prerequisites(ctx, need_docker=True)
    try:
        deploy_all(ctx)
    except Stop:
        if not ctx.check:
            say()
            say(
                f"{_c('1;31')}Stopped.{_c('0')} Fix what ✗ says, then run deploy again: it picks up where it stopped. "
                "README › Troubleshooting has the known first-deploy failures."
            )
        raise
    finally:
        if not ctx.check:
            ctx.state["deployedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_state(ctx.state)
    wb = ctx.edge.get("workbenchDomain")
    problems = f"{_c('1;31')}{Report.problems} problem(s): see ✗ above.{_c('0')}"
    say()
    if ctx.check:
        if Report.problems:
            say(f"Check done: {Report.changes} change(s) to make, and {problems}")
            return 1
        if Report.changes:
            say(
                f"{_c('1;33')}Check done: {Report.changes} change(s) to make.{_c('0')} Run `uv run deploy/devbox.py deploy` to apply them."
            )
        else:
            say(f"{_c('1;32')}Check done: everything is already deployed.{_c('0')}")
        return 0
    if Report.problems:
        say(
            f"{_c('1;31')}Deployed with {Report.problems} problem(s): see ✗ above.{_c('0')} {Report.changes} change(s) made."
        )
    else:
        say(
            f"{_c('1;32')}Deployed.{_c('0')} {Report.changes} change(s) made."
            + (f" Open https://{wb}" if wb and ctx.s.okta_client_id else "")
        )
    for note in ctx.notes:
        warn(note)
    section("Next")
    say(okta_steps(ctx.s, wb))
    say()
    say(spike_checklist(ctx.s, ctx.state.get("boxes", {}), wb, ctx.images.get("box")))
    if Report.problems:
        say()
        say(f"{problems} Fix them, then run deploy again.")
        return 1
    return 0


def cmd_network(ctx: Ctx, action: str) -> int:
    say(f"{_c('1;37')}Dev box · network {action}{_c('0')}")
    prerequisites_light(ctx)
    ensure_gateway_lookup(ctx)
    ensure_storage_lookup(ctx)
    ec2, nfw = ctx.aws.ec2, ctx.aws.nfw
    vpc = find_vpc(ctx)
    if not vpc:
        die("there's no dev box network yet: run deploy first")
    if action == "allowlist":
        if not ctx.gateway:
            die(f"there's no {GATEWAY_NAME} gateway yet (the allowlist names it): run deploy first")
        section("Egress allowlist: templates/egress-allowlist.txt → Network Firewall and DNS Firewall")
        ensure_firewall_policy(ctx, ensure_rule_group(ctx))
        ensure_dns_firewall(ctx, vpc["VpcId"])
        blocked = seen_names(ctx)
        missing = unlisted(list(blocked), allowlist_domains(ctx))
        if missing:
            warn(f"{len(missing)} name(s) blocked in the last 24 h aren't on the allowlist:")
            for n in missing:
                say(f"      {n}  ({', '.join(sorted(blocked[n]))})")
            warn("add the ones the box needs to templates/egress-allowlist.txt and run `network allowlist` again")
        else:
            ok("nothing blocked in the last 24 h is missing from the allowlist")
        save_state(ctx.state)
        return 1 if Report.problems else 0
    if action == "pause":
        section("Pause: delete the firewall, the NAT gateway and its Elastic IP (the box has no way out until resume)")
        fw = find_firewall(ctx)
        nat = find_nat(ctx, vpc["VpcId"])
        addrs = ec2.describe_addresses(Filters=tag_filters(NAT_NAME))["Addresses"]
        if is_paused(vpc) and not fw and not nat and not addrs:
            ok("already paused")
            return 0
        warn("While paused a box can't start (no image pull), and a running one loses Bedrock, STS and SSO.")
        warn(
            "`network resume` takes about 10–15 minutes (the NAT, then the firewall): run it at least 15 minutes before a demo."
        )
        if not is_paused(vpc):
            change(
                ctx,
                "mark the network paused",
                ec2.create_tags,
                Resources=[vpc["VpcId"]],
                Tags=[{"Key": PAUSED_TAG, "Value": "true"}],
            )
        if fw and fw["FirewallStatus"]["Status"] != "DELETING":
            drop_firewall_routes(ctx, vpc["VpcId"], fw)
            drop_firewall_logging(ctx)
            change(ctx, f"delete firewall {FIREWALL}", nfw.delete_firewall, FirewallName=FIREWALL)
        if nat:
            change(
                ctx,
                f"delete NAT gateway {nat['NatGatewayId']}",
                ec2.delete_nat_gateway,
                NatGatewayId=nat["NatGatewayId"],
            )
        if fw:
            wait_for(
                f"firewall {FIREWALL} to go", lambda: find_firewall(ctx), lambda v: v is None, timeout=1500, every=20
            )
        if nat:
            wait_waiter(
                ec2,
                "nat_gateway_deleted",
                f"NAT gateway {nat['NatGatewayId']} to go",
                lambda: nat_reason(ctx, nat["NatGatewayId"]),
                NatGatewayIds=[nat["NatGatewayId"]],
                WaiterConfig={"Delay": 15, "MaxAttempts": 40},
            )
        # Nothing pins the NAT's public address, and resume allocates a new one: release it, so pause stops its $0.005/h too.
        for a in addrs:
            change(ctx, f"release Elastic IP {a.get('PublicIp')}", ec2.release_address, AllocationId=a["AllocationId"])
        ctx.state.setdefault("network", {})["paused"] = True
        save_state(ctx.state)
        say()
        say(
            f"{_c('1;32')}Paused.{_c('0')} The routes to the firewall are gone and the NAT's is a blackhole. `network resume` "
            "recreates them (10–15 minutes). The DNS Firewall, the security groups, the S3 gateway endpoint and the EFS files stay."
        )
        return 0
    if action == "resume":
        if is_paused(vpc):
            change(
                ctx, "mark the network running", ec2.delete_tags, Resources=[vpc["VpcId"]], Tags=[{"Key": PAUSED_TAG}]
            )
        ensure_network(ctx, paused=False)  # not the tag: a read straight after DeleteTags can still show it
        save_state(ctx.state)
        if not (ctx.net.get("nat") and ctx.net.get("firewall_endpoint")):
            die("resume didn't bring back the NAT gateway and the firewall endpoint: run `network resume` again")
        say()
        say(f"{_c('1;32')}Running.{_c('0')}")
        return 1 if Report.problems else 0
    raise ValueError(action)


def ensure_gateway_lookup(ctx: Ctx) -> None:
    """The gateway's URL (for the allowlist) without changing anything."""
    acc = ctx.aws.acc
    gw = next((g for g in paged(acc.list_gateways, "items") if g["name"] == GATEWAY_NAME), None)
    if gw:
        cur = acc.get_gateway(gatewayIdentifier=gw["gatewayId"])
        ctx.gateway = {"id": gw["gatewayId"], "arn": cur["gatewayArn"], "url": cur["gatewayUrl"]}


def logs_query(ctx: Ctx, group: str, query: str, what: str) -> list[dict[str, str]] | None:
    """A Logs Insights query over the last 24 h: its rows as {field: value}, or None if it can't be read."""
    logs = ctx.aws.logs
    now = int(time.time())
    try:
        q = logs.start_query(logGroupName=group, startTime=now - 86400, endTime=now, queryString=query)
    except ClientError as e:
        warn(f"couldn't read {what} ({err_text(e)})")
        return None
    r = wait_for(
        f"the {what} query",
        lambda: logs.get_query_results(queryId=q["queryId"]),
        lambda v: v and v["status"] in ("Complete", "Failed", "Cancelled", "Timeout"),
        timeout=120,
        every=2,
    )
    if r["status"] != "Complete":
        warn(f"the {what} query ended {r['status']}; switching anyway")
        return None
    return [{f["field"]: f.get("value", "") for f in row} for row in r.get("results", [])]


def seen_names(ctx: Ctx) -> dict[str, set[str]]:
    """Every name the two firewalls blocked in the last 24 h, and how: TLS / HTTP (Network Firewall's alert log)
    and DNS (the Resolver query log)."""
    seen: dict[str, set[str]] = {}
    for row in logs_query(ctx, FIREWALL_LOG_GROUP, seen_names_query(), "the firewall's alert log") or []:
        if row.get("name"):
            seen.setdefault(row["name"].lower().rstrip("."), set()).add("HTTP" if row.get("http_host") else "TLS")
    for row in logs_query(ctx, DNS_LOG_GROUP, dns_seen_query(), "the DNS query log") or []:
        if row.get("query_name"):
            seen.setdefault(row["query_name"].lower().rstrip("."), set()).add("DNS")
    return seen


def cmd_status(ctx: Ctx) -> int:
    say(f"{_c('1;37')}Dev box · status{_c('0')}")
    s, acc = ctx.s, ctx.aws.acc
    ctx.account = ctx.aws.sts.get_caller_identity()["Account"]
    fs = find_file_system(ctx)
    fs_id = fs and fs["FileSystemId"]
    aps = access_points(ctx, fs_id) if fs_id else []
    section(
        f"Boxes (account {ctx.account}, {s.region}; compute type {COMPUTE}, files on EFS; made on each owner's first visit)"
    )
    recs = table_boxes(ctx)
    if not recs:
        ok(f"no boxes yet: each member of {s.okta_group} and one tier group gets theirs on their first visit")
    for rec in recs:
        u = box_user({"tier": "?", **rec})
        rt = find_runtime(ctx, u.runtime_name)
        ap = next((a for a in aps if a["AccessPointId"] == rec.get("accessPointId")), None) or person_access_point(
            aps, u
        )
        parts = []
        if rt:
            cur = acc.get_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
            img = (
                cur.get("agentRuntimeArtifact", {})
                .get("containerConfiguration", {})
                .get("containerUri", "?")
                .rsplit(":", 1)[-1]
            )
            net = (cur.get("networkConfiguration") or {}).get("networkMode", "?")
            kind = "Instances" if cur.get("capacityProviderConfiguration") else f"microVM, {net} network"
            lc = cur.get("lifecycleConfiguration") or {}
            parts.append(
                f"runtime {u.runtime_name} v{cur['agentRuntimeVersion']} {cur['status']} ({kind}), image {img}, "
                f"idle {lc.get('idleRuntimeSessionTimeout', '?')} s / max {lc.get('maxLifetime', '?')} s"
            )
        else:
            parts.append(f"no runtime {u.runtime_name}")
        parts.append(
            f"EFS folder {u.efs_root} ({ap['AccessPointId']}, {ap.get('LifeCycleState')})"
            if ap
            else f"no access point {u.efs_root}"
        )
        parts.append(f"generation {rec.get('generation', '?')}")
        if rec.get("step") and rec.get("step") != "ready":
            parts.append(f"provisioning: {rec.get('step')} ({rec.get('message', '')})")
        (ok if rt and ap else warn)(f"{u.name} ({u.tier}): " + " · ".join(parts))
        if rec.get("sessionId"):
            say(f"      session {rec['sessionId']}")
    cps, rts = legacy_instances(ctx)
    if cps or rts:
        section("Old Instances boxes (`retire-instances` deletes them; deploy never touches them)")
        for c in cps:
            warn(
                f"capacity provider {c['name']} ({c['capacityProviderId']}, {c.get('status')}): its sessions' volumes bill while stopped"
            )
        for r in rts:
            warn(f"runtime {r['agentRuntimeName']} ({r['agentRuntimeId']}, {r.get('status')})")

    section("Storage (EFS)")
    if not fs:
        warn(f"no EFS file system {EFS_NAME}: deploy makes it")
    else:
        size = (fs.get("SizeInBytes") or {}).get("Value")
        ok(
            f"file system {EFS_NAME} {fs_id}: {fs['LifeCycleState']}, {'encrypted' if fs.get('Encrypted') else 'NOT encrypted'}, "
            f"{fs.get('ThroughputMode', '?')} throughput"
            + (f", {size / 1e6:.0f} MB stored" if isinstance(size, int) else "")
        )
        mts = mount_targets(ctx, fs_id)
        for m in mts:
            ok(
                f"mount target {m['MountTargetId']} in {m['SubnetId']} ({m.get('AvailabilityZoneId', '?')}, {m['LifeCycleState']}, "
                f"{m.get('IpAddress', '?')})"
            )
        if not mts:
            warn("no mount target: no box can mount its folder (deploy makes one in devbox-box)")
        for a in aps:
            pu = a.get("PosixUser") or {}
            ok(
                f"access point {(a.get('RootDirectory') or {}).get('Path')} ({a['AccessPointId']}, {a.get('LifeCycleState')}, "
                f"runs as {pu.get('Uid')}:{pu.get('Gid')})"
            )
        try:
            doc = json.loads(ctx.aws.efs.describe_file_system_policy(FileSystemId=fs_id).get("Policy") or "{}")
            if same_policy(doc, file_system_policy_static(file_system_arn(ctx.account, s.region, fs_id))):
                ok(
                    "file system policy: TLS only, no root, no anonymous NFS (each role allows only its own access point)"
                )
            else:
                warn(
                    f"file system policy: {len(doc.get('Statement', []))} statement(s), not the static one deploy sets (deploy puts it right)"
                )
        except ClientError as e:
            if not is_missing(e):
                raise
            warn("no file system policy: any NFS client that reaches the mount target can mount it (deploy sets one)")

    section("Network")
    vpc = find_vpc(ctx)
    if not vpc:
        warn("no dev box VPC")
    else:
        fw = find_firewall(ctx)
        nat = find_nat(ctx, vpc["VpcId"])
        allowlist_on = False
        try:
            pol = ctx.aws.nfw.describe_firewall_policy(FirewallPolicyName=FIREWALL_POLICY)
            allowlist_on = policy_uses(pol["FirewallPolicy"], rule_group_arn(ctx, RG_ALLOW)[0])
        except ClientError as e:
            if not is_missing(e):
                raise
        (warn if is_paused(vpc) or not allowlist_on else ok)(
            f"VPC {vpc['VpcId']}: {'PAUSED' if is_paused(vpc) else 'running'}, egress "
            + ("allowlist on" if allowlist_on else f"allowlist NOT on ({FIREWALL_POLICY}); deploy sets it")
        )
        ok(f"firewall {fw['FirewallStatus']['Status'] if fw else 'none'} · NAT {nat['State'] if nat else 'none'}")
        eps = find_s3_endpoints(ctx, vpc["VpcId"])
        (ok if eps else warn)(
            f"S3 gateway endpoint {S3_ENDPOINT_NAME}: "
            + (
                f"{eps[0]['VpcEndpointId']} ({eps[0].get('State')})"
                if eps
                else "none (image layers go through the NAT)"
            )
        )
        r53 = ctx.aws.r53r
        grp = find_named(paged(r53.list_firewall_rule_groups, "FirewallRuleGroups", "NextToken"), DNS_RULE_GROUP)
        any_list = find_named(paged(r53.list_firewall_domain_lists, "FirewallDomainLists", "NextToken"), DNS_ANY_LIST)
        if grp:
            blocks = dns_blocks_the_rest(
                paged(r53.list_firewall_rules, "FirewallRules", "NextToken", FirewallRuleGroupId=grp["Id"]),
                any_list and any_list["Id"],
            )
            attached = any(
                a.get("FirewallRuleGroupId") == grp["Id"]
                for a in paged(
                    r53.list_firewall_rule_group_associations,
                    "FirewallRuleGroupAssociations",
                    "NextToken",
                    VpcId=vpc["VpcId"],
                )
            )
            (ok if attached and blocks else warn)(
                f"DNS Firewall {DNS_RULE_GROUP}: {'NXDOMAIN' if blocks else 'NOT blocking'} for names off the allowlist, "
                f"{'on' if attached else 'NOT on'} the VPC's resolver"
            )
        else:
            warn(f"no DNS Firewall ({DNS_RULE_GROUP}): DNS queries leave unfiltered; deploy adds it")
    section("Tools gateway")
    gw = next((g for g in paged(acc.list_gateways, "items") if g["name"] == GATEWAY_NAME), None)
    if gw:
        cur = acc.get_gateway(gatewayIdentifier=gw["gatewayId"])
        ok(f"{GATEWAY_NAME} {cur['status']} {cur['gatewayUrl']}")
    else:
        warn(f"no {GATEWAY_NAME} gateway")
    section("Provisioner")
    try:
        conf = ctx.aws.lam.get_function_configuration(FunctionName=PROVISIONER_FUNCTION)
        ok(f"Lambda {PROVISIONER_FUNCTION}: {conf.get('State', '?')}, last update {conf.get('LastUpdateStatus', '?')}")
    except ClientError as e:
        if not is_missing(e):
            raise
        warn(f"no Lambda {PROVISIONER_FUNCTION}: nobody new can get a box (deploy makes it)")
    found = next(
        (a for a in paged(ctx.aws.apigw.get_apis, "Items", "NextToken", send="NextToken") if a["Name"] == API_NAME),
        None,
    )
    (ok if found else warn)(f"HTTP API {API_NAME}: " + (found["ApiEndpoint"] if found else "none"))
    section("Edge")
    for site in SITES:
        d = find_distribution(ctx, site)
        (ok if d else warn)(
            f"{site}: "
            + (
                f"https://{d['DomainName']} ({d['Status']}, {'enabled' if d.get('Enabled') else 'disabled'})"
                if d
                else "none"
            )
        )
    return 0


def delete_session(ctx: Ctx, cp_id: str, sid: str) -> None:
    """DeleteCapacityProviderSession is asynchronous and idempotent: re-issue it until the session is gone."""
    acd = ctx.aws.acd

    def probe():
        return acd.delete_capacity_provider_session(capacityProviderId=cp_id, sessionId=sid)["status"]

    wait_for(f"session {sid[:16]}… to be deleted", probe, lambda v: v in (None, "Deleted"), timeout=900, every=15)


def table_boxes(ctx: Ctx) -> list[dict]:
    """Every box's record, or what .state.json remembers when there's no table yet."""
    try:
        return box_records(ctx.aws.ddb)
    except ClientError as e:
        if not is_missing(e):
            raise
    return [dict(b, name=n) for n, b in sorted((ctx.state.get("boxes") or {}).items()) if not is_legacy_record(b)]


def cmd_reset_box(ctx: Ctx, name: str, yes: bool) -> int:
    """A microVM box's files are on EFS, and nobody may stop its session, so a reset moves the box to the next
    session generation: the next visit starts a fresh microVM, and the old one ends at its idle timeout."""
    say(f"{_c('1;37')}Dev box · reset-box {name}{_c('0')}")
    prerequisites_light(ctx)
    acc, ddb = ctx.aws.acc, ctx.aws.ddb
    rec = next((r for r in table_boxes(ctx) if r.get("name") == name), None)
    if not rec:
        die(f"no box named {name} (`status` lists them; a box is made on its owner's first visit)")
    user, uid = box_user({"tier": "?", **rec}), rec.get("uid")
    rt = find_runtime(ctx, user.runtime_name)
    if not (rt and uid):
        die(f"{name} has no microVM runtime {user.runtime_name} yet: it's made on their first visit")
    current = acc.get_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
    gen = (
        int(rec.get("generation") or 0)
        or recover_generation(uid, (current.get("environmentVariables") or {}).get("DEVBOX_SESSION_ID", ""))
        or 1
    )
    section(f"Reset {name}'s box")
    say(
        f"  This moves {name}'s box from session generation {gen} to {gen + 1}: their next visit starts a fresh microVM."
    )
    say(
        "  Whatever runs in the old session (VS Code, tmux, Claude Code) is lost. The old session can't be stopped early (the"
    )
    say(
        f"  resource policy denies StopRuntimeSession to everyone), so it ends on its own after {ctx.s.vm_idle_seconds} s idle."
    )
    say(
        f"  Their files in {MOUNT_PATH} are on EFS ({user.efs_root}) and stay: to wipe them, delete them in the box's terminal."
    )
    if not yes and input(f"  Type the box name ({name}) to go ahead: ").strip() != name:
        die("Not confirmed. Nothing was changed.")
    env = dict(current.get("environmentVariables") or {})
    env["DEVBOX_SESSION_ID"] = session_id(uid, gen + 1)
    change(
        ctx,
        f"point runtime {user.runtime_name} at generation {gen + 1}",
        acc.update_agent_runtime,
        **runtime_update_from_current(current, env),
    )
    wait_runtime(ctx, current["agentRuntimeId"], f"runtime {user.runtime_name}")
    rec.update(generation=gen + 1, sessionId=session_id(uid, gen + 1))
    try:
        if rec.get("key"):
            change(
                ctx,
                f"record generation {gen + 1} in {BOX_TABLE} (the page reads it from {PROVISION_PATH})",
                put_box,
                ddb,
                rec,
            )
    except ClientError as e:
        if not is_missing(e):
            raise
    ctx.state.setdefault("boxes", {})[name] = state_box(rec)
    save_state(ctx.state)
    say()
    say(f"{_c('1;32')}Reset.{_c('0')} {name}'s next visit starts a fresh microVM (a cold start), with the same files.")
    warn(f"If {name} has the dev box open, they must reload the page: an open tab keeps using the old session.")
    return 0


# ---- undeploy
def per_person_roles(ctx: Ctx) -> list[str]:
    """Every box's execution role: the table's and .state.json's names, and any devbox-exec-* role IAM has."""
    names = (
        {r["name"] for r in table_boxes(ctx) if r.get("name")}
        | set(ctx.state.get("boxes", {}))
        | set(ctx.state.get("instances", {}))
    )
    roles = {f"{EXEC_ROLE_PREFIX}{n}" for n in names}
    roles |= {
        r["RoleName"]
        for r in paged(ctx.aws.iam.list_roles, "Roles", "Marker")
        if r["RoleName"].startswith(EXEC_ROLE_PREFIX)
    }
    return sorted(roles)


def undeploy_all(ctx: Ctx, delete_volumes: bool) -> list[str]:
    """Remove in dependency order. Without --delete-volumes the EFS file system (everyone's files, with its access
    points and mount target) stays, and so does what it sits in (the VPC); so do the old Instances boxes' capacity
    providers (their disks). Returns what was kept, and why."""
    acc, ec2, nfw, iam, lam, cf, ecr = (
        ctx.aws.acc,
        ctx.aws.ec2,
        ctx.aws.nfw,
        ctx.aws.iam,
        ctx.aws.lam,
        ctx.aws.cf,
        ctx.aws.ecr,
    )
    kept: list[str] = []

    section("1 · Runtimes (the microVM boxes, and any old Instances ones; the files are on EFS)")
    runtimes = [
        r for r in paged(acc.list_agent_runtimes, "agentRuntimes") if r["agentRuntimeName"].startswith("devbox_")
    ]
    for r in runtimes:
        change(
            ctx, f"delete runtime {r['agentRuntimeName']}", acc.delete_agent_runtime, agentRuntimeId=r["agentRuntimeId"]
        )
    if not runtimes:
        ok("no devbox_* runtimes")
    cps = [c for c in paged(acc.list_capacity_providers, "capacityProviders") if LEGACY_NAME.match(c["name"])]
    if runtimes and not ctx.check:
        for r in runtimes:
            wait_for(
                f"runtime {r['agentRuntimeName']} to go",
                lambda r=r: acc.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"]),
                lambda v: v is None,
                timeout=900,
                every=10,
            )
        for c in cps:
            wait_for(
                f"runtime versions to detach from {c['name']}",
                lambda c=c: acc.list_agent_runtime_versions_by_capacity_provider(
                    capacityProviderId=c["capacityProviderId"]
                )["agentRuntimes"],
                lambda v: not v,
                timeout=900,
                every=15,
            )

    section("2 · The old Instances boxes' sessions and capacity providers (their disks)")
    if not cps:
        ok("no devbox_<name> capacity providers")
    elif not delete_volumes:
        for c in cps:
            kept.append(
                f"capacity provider {c['name']} and its volumes (billed while stopped; `retire-instances` deletes them)"
            )
            ok(f"kept: capacity provider {c['name']} (its sessions' volumes)")
    else:
        retire_capacity_providers(ctx, cps)

    section(f"3 · Storage: EFS file system {EFS_NAME} (everyone's files)")
    fs = find_file_system(ctx)
    if not fs:
        ok(f"no EFS file system {EFS_NAME}")
    elif not delete_volumes:
        kept.append(
            f"EFS file system {EFS_NAME} ({fs['FileSystemId']}): everyone's files, with the access points, the mount target "
            "and the file system policy (billed per GB stored; --delete-volumes removes it)"
        )
        ok(f"kept: EFS file system {EFS_NAME} ({fs['FileSystemId']}), its access points and mount target")
    else:
        delete_storage(ctx, fs["FileSystemId"])
    ddb = ctx.aws.ddb
    try:
        ddb.describe_table(TableName=BOX_TABLE)
        has_table = True
    except ClientError as e:
        if not is_missing(e):
            raise
        has_table = False
    if not has_table:
        ok(f"table {BOX_TABLE}: not there")
    elif not delete_volumes:
        kept.append(
            f"table {BOX_TABLE}: which person has which box name and folder (free at rest; --delete-volumes removes it)"
        )
        ok(f"kept: table {BOX_TABLE} (each person's box name and folder)")
    else:
        change(
            ctx,
            f"turn off {BOX_TABLE}'s deletion protection",
            ddb.update_table,
            TableName=BOX_TABLE,
            DeletionProtectionEnabled=False,
        )
        change(ctx, f"delete table {BOX_TABLE}", ddb.delete_table, TableName=BOX_TABLE)

    section("4 · Tools gateway")
    gw = next((g for g in paged(acc.list_gateways, "items") if g["name"] == GATEWAY_NAME), None)
    if gw:
        for t in paged(acc.list_gateway_targets, "items", gatewayIdentifier=gw["gatewayId"]):
            change(
                ctx,
                f"delete the {t['name']} target",
                acc.delete_gateway_target,
                gatewayIdentifier=gw["gatewayId"],
                targetId=t["targetId"],
            )
        if not ctx.check:
            wait_for(
                "the gateway's targets to go",
                lambda: paged(acc.list_gateway_targets, "items", gatewayIdentifier=gw["gatewayId"]),
                lambda v: not v,
                timeout=600,
            )
        change(ctx, f"delete gateway {GATEWAY_NAME}", acc.delete_gateway, gatewayIdentifier=gw["gatewayId"])
        if not ctx.check:
            wait_for(
                f"gateway {GATEWAY_NAME} to go",
                lambda: acc.get_gateway(gatewayIdentifier=gw["gatewayId"]),
                lambda v: v is None,
                timeout=600,
            )
    else:
        ok(f"gateway {GATEWAY_NAME}: not there")
    eng = next((e for e in paged(acc.list_policy_engines, "policyEngines") if e["name"] == POLICY_ENGINE), None)
    if eng:
        for p in paged(acc.list_policies, "policies", policyEngineId=eng["policyEngineId"]):
            change(
                ctx,
                f"delete Cedar rule {p['name']}",
                acc.delete_policy,
                policyEngineId=eng["policyEngineId"],
                policyId=p["policyId"],
            )
        if not ctx.check:
            wait_for(
                "the Cedar rules to go",
                lambda: paged(acc.list_policies, "policies", policyEngineId=eng["policyEngineId"]),
                lambda v: not v,
                timeout=600,
            )
        change(
            ctx, f"delete policy engine {POLICY_ENGINE}", acc.delete_policy_engine, policyEngineId=eng["policyEngineId"]
        )
    else:
        ok(f"policy engine {POLICY_ENGINE}: not there")

    section("5 · Edge: distributions (disabling one takes minutes), the origin access control, the Lambda")
    dists = [(site, d) for site in SITES if (d := find_distribution(ctx, site))]
    for site, d in dists:
        if d.get("Enabled"):
            got = cf.get_distribution_config(Id=d["Id"])
            change(
                ctx,
                f"disable distribution {site} ({d['DomainName']})",
                cf.update_distribution,
                Id=d["Id"],
                IfMatch=got["ETag"],
                DistributionConfig={**got["DistributionConfig"], "Enabled": False},
            )
    for site, d in dists:
        if not ctx.check:
            wait_for(
                f"distribution {site} to finish disabling",
                lambda d=d: cf.get_distribution(Id=d["Id"])["Distribution"]["Status"],
                lambda v: v == "Deployed",
                timeout=1800,
                every=30,
            )
        etag = None if ctx.check else cf.get_distribution(Id=d["Id"])["ETag"]
        change(ctx, f"delete distribution {site}", cf.delete_distribution, Id=d["Id"], IfMatch=etag)
    if not dists:
        ok("no dev box distributions")
    oac = find_oac(ctx)
    if oac:
        etag = None if ctx.check else cf.get_origin_access_control(Id=oac["Id"])["ETag"]
        change(
            ctx, f"delete origin access control {OAC_NAME}", cf.delete_origin_access_control, Id=oac["Id"], IfMatch=etag
        )
    try:
        lam.get_function(FunctionName=EDGE_FUNCTION)
        change(ctx, f"delete Lambda {EDGE_FUNCTION} and its function URL", delete_function, ctx)
    except ClientError as e:
        if not is_missing(e):
            raise
        ok(f"Lambda {EDGE_FUNCTION}: not there")

    section(f"5b · The provisioner: its HTTP API {API_NAME} and Lambda {PROVISIONER_FUNCTION}")
    apigw = ctx.aws.apigw
    apis = [a for a in paged(apigw.get_apis, "Items", "NextToken", send="NextToken") if a["Name"] == API_NAME]
    for a in apis:
        change(ctx, f"delete HTTP API {API_NAME} ({a['ApiEndpoint']})", apigw.delete_api, ApiId=a["ApiId"])
    if not apis:
        ok(f"HTTP API {API_NAME}: not there")
    try:
        lam.get_function(FunctionName=PROVISIONER_FUNCTION)
        change(ctx, f"delete Lambda {PROVISIONER_FUNCTION}", lam.delete_function, FunctionName=PROVISIONER_FUNCTION)
    except ClientError as e:
        if not is_missing(e):
            raise
        ok(f"Lambda {PROVISIONER_FUNCTION}: not there")

    section(
        "6 · Network: the firewall, the NAT and its address"
        + (", the DNS Firewall and the firewall policy" if delete_volumes else "")
    )
    vpc = find_vpc(ctx)
    fw, nat = find_firewall(ctx), vpc and find_nat(ctx, vpc["VpcId"])
    if fw and vpc:
        drop_firewall_routes(ctx, vpc["VpcId"], fw)
    if fw:
        drop_firewall_logging(ctx)
        change(ctx, f"delete firewall {FIREWALL}", nfw.delete_firewall, FirewallName=FIREWALL)
    if nat:
        change(
            ctx, f"delete NAT gateway {nat['NatGatewayId']}", ec2.delete_nat_gateway, NatGatewayId=nat["NatGatewayId"]
        )
    if fw and not ctx.check:
        wait_for(f"firewall {FIREWALL} to go", lambda: find_firewall(ctx), lambda v: v is None, timeout=1500, every=20)
    if nat and not ctx.check:
        wait_waiter(
            ec2,
            "nat_gateway_deleted",
            f"NAT gateway {nat['NatGatewayId']} to go",
            lambda: nat_reason(ctx, nat["NatGatewayId"]),
            NatGatewayIds=[nat["NatGatewayId"]],
            WaiterConfig={"Delay": 15, "MaxAttempts": 40},
        )
    for a in ec2.describe_addresses(Filters=tag_filters(NAT_NAME))["Addresses"]:
        change(ctx, f"release Elastic IP {a.get('PublicIp')}", ec2.release_address, AllocationId=a["AllocationId"])
    if vpc and not delete_volumes:
        kept.append(
            f"VPC {vpc['VpcId']}: subnets, security groups {SG_NAME} and {EFS_SG}, route tables and the S3 gateway endpoint "
            "(the EFS mount target sits in them; all free)"
        )
        kept.append(
            "the firewall policy and rule groups, and the log group "
            + FIREWALL_LOG_GROUP
            + " (free; deploy reuses them)"
        )
        kept.append(
            f"the DNS Firewall {DNS_RULE_GROUP} on the VPC, its domain lists, and query logging to {DNS_LOG_GROUP} "
            "(billed per query and per logged byte, so about nothing with no box running)"
        )
    elif delete_volumes:
        delete_dns_firewall(ctx)
        for name in (FIREWALL_POLICY,):
            try:
                nfw.describe_firewall_policy(FirewallPolicyName=name)
                change(
                    ctx,
                    f"delete firewall policy {name}",
                    retry_in_use,
                    nfw.delete_firewall_policy,
                    FirewallPolicyName=name,
                )
                if not ctx.check:
                    wait_for(
                        f"firewall policy {name} to go",
                        lambda name=name: nfw.describe_firewall_policy(FirewallPolicyName=name),
                        lambda v: v is None,
                        timeout=600,
                    )
            except ClientError as e:
                if not is_missing(e):
                    raise
        arn, _ = rule_group_arn(ctx, RG_ALLOW)
        if arn:
            change(ctx, f"delete firewall rule group {RG_ALLOW}", retry_in_use, nfw.delete_rule_group, RuleGroupArn=arn)
        delete_log_groups(ctx, FIREWALL_LOG_GROUP)
        delete_log_groups(ctx, DNS_LOG_GROUP)

    section("7 · IAM roles")
    legacy = (OPERATOR_ROLE, INSTANCE_ROLE) if delete_volumes or not cps else ()
    for name in (*per_person_roles(ctx), PROVISIONER_ROLE, EDGE_ROLE, GATEWAY_ROLE, LEGACY_EXEC_ROLE, *legacy):
        try:
            iam.get_role(RoleName=name)
        except ClientError as e:
            if not is_missing(e):
                raise
            ok(f"role {name}: not there")
            continue
        change(ctx, f"delete role {name}", delete_role, ctx, name)
    if cps and not delete_volumes:
        kept.append(
            f"roles {OPERATOR_ROLE} and {INSTANCE_ROLE} (+ instance profile): the old capacity providers launch with them"
        )
    boundary = f"arn:aws:iam::{ctx.account}:policy/{EXEC_BOUNDARY}"
    try:
        iam.get_policy(PolicyArn=boundary)
        change(
            ctx,
            f"delete the permissions boundary {EXEC_BOUNDARY} (and its old versions)",
            delete_managed_policy,
            ctx,
            boundary,
        )
    except ClientError as e:
        if not is_missing(e):
            raise
        ok(f"permissions boundary {EXEC_BOUNDARY}: not there")

    section("8 · Images and logs")
    for repo in (ECR_BOX, ECR_EDGE):
        try:
            ecr.describe_repositories(repositoryNames=[repo])
            change(
                ctx,
                f"delete ECR repository {repo} and its images",
                ecr.delete_repository,
                repositoryName=repo,
                force=True,
            )
        except ClientError as e:
            if not is_missing(e):
                raise
            ok(f"ECR repository {repo}: not there")
    delete_log_groups(ctx, EDGE_LOG_GROUP)
    delete_log_groups(ctx, PROVISIONER_LOG_GROUP)
    delete_log_groups(ctx, RUNTIME_LOG_PREFIX, prefix=True)

    if delete_volumes and vpc:
        section(
            "9 · The VPC, last: all but what AgentCore's leftover network interface holds (AWS removes it within 8 hours)"
        )
        kept += delete_vpc(ctx, vpc["VpcId"])
    return kept


def delete_storage(ctx: Ctx, fs_id: str) -> None:
    """The access points, the mount target (and wait: its network interface holds the subnet), then the file system
    and every file on it. Its policy goes with it."""
    efs = ctx.aws.efs
    for a in access_points(ctx, fs_id):
        change(
            ctx,
            f"delete access point {(a.get('RootDirectory') or {}).get('Path')} ({a['AccessPointId']})",
            efs.delete_access_point,
            AccessPointId=a["AccessPointId"],
        )
    mts = mount_targets(ctx, fs_id)
    for m in mts:
        change(
            ctx,
            f"delete the EFS mount target {m['MountTargetId']}",
            efs.delete_mount_target,
            MountTargetId=m["MountTargetId"],
        )
    if not ctx.check:
        # Not mount_targets(): it leaves out the ones still "deleting", and EFS refuses to delete the file system until
        # they're gone (live 2026-10-02: FileSystemInUse straight after DeleteMountTarget).
        wait_for(
            "the EFS mount target to go",
            lambda: efs.describe_mount_targets(FileSystemId=fs_id).get("MountTargets") or [],
            lambda v: not v,
            every=10,
            timeout=900,
        )

    def delete_fs():
        for _ in range(30):  # EFS can say "in use" for a little while after the last mount target has gone
            try:
                return efs.delete_file_system(FileSystemId=fs_id)
            except ClientError as e:
                if err_code(e) != "FileSystemInUse":
                    raise
                SLEEP(10)
        return efs.delete_file_system(FileSystemId=fs_id)

    change(ctx, f"delete EFS file system {EFS_NAME} ({fs_id}) and every file on it", delete_fs)
    if not ctx.check:
        wait_for(
            f"EFS file system {fs_id} to go",
            lambda: file_system_state(ctx, fs_id),
            lambda v: v in (None, "deleted"),
            every=10,
            timeout=900,
        )


def delete_dns_firewall(ctx: Ctx) -> None:
    """Detach from the VPC (query logging, then the rule group), then the rules, the rule group, the lists
    and the query logging configuration: each one can only go once nothing uses it."""
    r53 = ctx.aws.r53r
    grp = find_named(paged(r53.list_firewall_rule_groups, "FirewallRuleGroups", "NextToken"), DNS_RULE_GROUP)
    qlc = find_named(paged(r53.list_resolver_query_log_configs, "ResolverQueryLogConfigs", "NextToken"), DNS_QUERY_LOG)
    if qlc:
        assocs = [
            a
            for a in paged(
                r53.list_resolver_query_log_config_associations,
                "ResolverQueryLogConfigAssociations",
                "NextToken",
                Filters=[{"Name": "ResolverQueryLogConfigId", "Values": [qlc["Id"]]}],
            )
            if a.get("ResolverQueryLogConfigId") == qlc["Id"]
        ]
        for a in assocs:
            change(
                ctx,
                f"stop logging {a['ResourceId']}'s DNS queries",
                r53.disassociate_resolver_query_log_config,
                ResolverQueryLogConfigId=qlc["Id"],
                ResourceId=a["ResourceId"],
            )
        if assocs and not ctx.check:
            wait_for(
                "DNS query logging to detach",
                lambda: [
                    a
                    for a in paged(
                        r53.list_resolver_query_log_config_associations,
                        "ResolverQueryLogConfigAssociations",
                        "NextToken",
                        Filters=[{"Name": "ResolverQueryLogConfigId", "Values": [qlc["Id"]]}],
                    )
                    if a.get("ResolverQueryLogConfigId") == qlc["Id"]
                ],
                lambda v: not v,
                every=5,
                timeout=600,
            )
    if grp:
        assocs = paged(
            r53.list_firewall_rule_group_associations,
            "FirewallRuleGroupAssociations",
            "NextToken",
            FirewallRuleGroupId=grp["Id"],
        )
        for a in assocs:
            change(
                ctx,
                f"detach DNS Firewall {DNS_RULE_GROUP} from {a['VpcId']}",
                r53.disassociate_firewall_rule_group,
                FirewallRuleGroupAssociationId=a["Id"],
            )
        if assocs and not ctx.check:
            wait_for(
                f"DNS Firewall {DNS_RULE_GROUP} to detach",
                lambda: paged(
                    r53.list_firewall_rule_group_associations,
                    "FirewallRuleGroupAssociations",
                    "NextToken",
                    FirewallRuleGroupId=grp["Id"],
                ),
                lambda v: not v,
                every=5,
                timeout=600,
            )
        for rule in paged(r53.list_firewall_rules, "FirewallRules", "NextToken", FirewallRuleGroupId=grp["Id"]):
            change(
                ctx,
                f"delete DNS Firewall rule {rule.get('Name')}",
                r53.delete_firewall_rule,
                FirewallRuleGroupId=grp["Id"],
                FirewallDomainListId=rule["FirewallDomainListId"],
            )
        change(
            ctx,
            f"delete DNS Firewall rule group {DNS_RULE_GROUP}",
            retry_in_use,
            r53.delete_firewall_rule_group,
            FirewallRuleGroupId=grp["Id"],
        )
    else:
        ok(f"DNS Firewall rule group {DNS_RULE_GROUP}: not there")
    lists = paged(r53.list_firewall_domain_lists, "FirewallDomainLists", "NextToken")
    for name in (DNS_ALLOW_LIST, DNS_ANY_LIST):
        lst = find_named(lists, name)
        if lst:
            change(
                ctx,
                f"delete DNS Firewall domain list {name}",
                retry_in_use,
                r53.delete_firewall_domain_list,
                FirewallDomainListId=lst["Id"],
            )
    if qlc:
        change(
            ctx,
            f"delete Resolver query logging {DNS_QUERY_LOG}",
            retry_in_use,
            r53.delete_resolver_query_log_config,
            ResolverQueryLogConfigId=qlc["Id"],
        )


def delete_capacity_provider(ctx: Ctx, cp_id: str) -> None:
    """DELETE_FAILED isn't final: re-issuing the delete has been seen to work, so keep at it."""
    acc = ctx.aws.acc
    acc.delete_capacity_provider(capacityProviderId=cp_id)
    for _ in range(10):
        st = wait_for(
            "the capacity provider to go",
            lambda: acc.get_capacity_provider(capacityProviderId=cp_id)["status"],
            lambda v: v in (None, "DELETE_FAILED"),
            timeout=1200,
            every=15,
        )
        if st is None:
            return
        warn("  DELETE_FAILED; asking again")
        acc.delete_capacity_provider(capacityProviderId=cp_id)
    die("the capacity provider is still DELETE_FAILED after 10 tries: look at its statusReason in the console")


def delete_function(ctx: Ctx) -> None:
    lam = ctx.aws.lam
    try:
        lam.delete_function_url_config(FunctionName=EDGE_FUNCTION)
    except ClientError as e:
        if not is_missing(e):
            raise
    lam.delete_function(FunctionName=EDGE_FUNCTION)


def delete_managed_policy(ctx: Ctx, arn: str) -> None:
    iam = ctx.aws.iam
    for v in iam.list_policy_versions(PolicyArn=arn)["Versions"]:
        if not v["IsDefaultVersion"]:
            iam.delete_policy_version(PolicyArn=arn, VersionId=v["VersionId"])
    iam.delete_policy(PolicyArn=arn)


def delete_role(ctx: Ctx, name: str) -> None:
    iam = ctx.aws.iam
    for p in paged(iam.list_role_policies, "PolicyNames", "Marker", RoleName=name):
        iam.delete_role_policy(RoleName=name, PolicyName=p)
    for p in paged(iam.list_attached_role_policies, "AttachedPolicies", "Marker", RoleName=name):
        iam.detach_role_policy(RoleName=name, PolicyArn=p["PolicyArn"])
    for prof in paged(iam.list_instance_profiles_for_role, "InstanceProfiles", "Marker", RoleName=name):
        iam.remove_role_from_instance_profile(InstanceProfileName=prof["InstanceProfileName"], RoleName=name)
        iam.delete_instance_profile(InstanceProfileName=prof["InstanceProfileName"])
    iam.delete_role(RoleName=name)


def delete_log_groups(ctx: Ctx, name: str, prefix: bool = False) -> None:
    logs = ctx.aws.logs
    groups = [
        g["logGroupName"]
        for g in paged(logs.describe_log_groups, "logGroups", logGroupNamePrefix=name)
        if prefix or g["logGroupName"] == name
    ]
    for g in groups:
        change(ctx, f"delete log group {g}", logs.delete_log_group, logGroupName=g)
    if not groups:
        ok(f"log group {name}{'*' if prefix else ''}: not there")


AGENTCORE_ENI_TYPE = "agentic_ai"  # the InterfaceType AgentCore's network interfaces have (seen live 2026-10-02)
AGENTCORE_ENI_HOURS = 8  # "may persist in your VPC for up to 8 hours" after the agent is deleted (agentcore-vpc.html)


def agentcore_interfaces(ctx: Ctx, vpc_id: str) -> list[dict]:
    return [
        n
        for n in paged(
            ctx.aws.ec2.describe_network_interfaces,
            "NetworkInterfaces",
            "NextToken",
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}],
        )
        if n.get("InterfaceType") == AGENTCORE_ENI_TYPE
    ]


def delete_vpc(ctx: Ctx, vpc_id: str) -> list[str]:
    """Everything in the VPC, then the VPC. AgentCore's network interface (shared by the runtimes on one subnet and
    security group) outlives them: AWS removes it on its own within 8 hours (agentcore-vpc.html), and there's no supported
    way to remove it sooner. So undeploy doesn't wait: it deletes what the interface doesn't hold and leaves its subnet,
    its security group and the VPC for a later `undeploy --delete-volumes` (or a deploy, which reuses them). Returns what
    it left, and why."""
    ec2 = ctx.aws.ec2
    left: list[str] = []

    def dependent(what: str, fn, *a, **kw):
        # A network interface can outlive what made it: the EFS mount target's for a minute, AgentCore's (shared by
        # the runtimes on this subnet and security group) for up to 8 hours after the last runtime is deleted.
        for i in range(20):
            try:
                return fn(*a, **kw)
            except ClientError as e:
                if err_code(e) != "DependencyViolation":
                    raise
                if i == 19:
                    die(
                        f"{what} is still in use ({err_text(e)}). AgentCore's network interfaces can stay up to 8 hours after a "
                        "runtime is deleted (docs: agentcore-vpc.html): run `undeploy --delete-volumes` again later, it picks up "
                        "where it stopped"
                    )
                SLEEP(15)

    eps = [
        e
        for e in paged(
            ec2.describe_vpc_endpoints, "VpcEndpoints", "NextToken", Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        if e.get("State", "").lower() not in ("deleted", "deleting")
    ]
    if eps:
        change(
            ctx,
            f"delete VPC endpoint(s) {', '.join(e['VpcEndpointId'] for e in eps)} (the S3 gateway endpoint)",
            ec2.delete_vpc_endpoints,
            VpcEndpointIds=[e["VpcEndpointId"] for e in eps],
        )
    for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]:
        if any(a.get("Main") for a in rt.get("Associations", [])):
            continue
        for a in rt.get("Associations", []):
            change(
                ctx,
                f"disassociate route table {rt['RouteTableId']}",
                ec2.disassociate_route_table,
                AssociationId=a["RouteTableAssociationId"],
            )
        change(ctx, f"delete route table {rt['RouteTableId']}", ec2.delete_route_table, RouteTableId=rt["RouteTableId"])
    enis = agentcore_interfaces(ctx, vpc_id)
    held_sgs = {g["GroupId"] for n in enis for g in n.get("Groups", [])}
    held_subnets = {n["SubnetId"] for n in enis}
    eni_ids = ", ".join(n["NetworkInterfaceId"] for n in enis)
    sgs = [
        sg
        for sg in ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]
        if sg["GroupName"] != "default"
    ]
    for (
        sg
    ) in sgs:  # devbox-box and devbox-efs name each other (NFS): a group can't go while another group's rule names it
        rules = [
            r
            for r in ec2.describe_security_group_rules(Filters=[{"Name": "group-id", "Values": [sg["GroupId"]]}])[
                "SecurityGroupRules"
            ]
            if (r.get("ReferencedGroupInfo") or {}).get("GroupId")
        ]
        for egress, revoke in ((True, ec2.revoke_security_group_egress), (False, ec2.revoke_security_group_ingress)):
            ids = [r["SecurityGroupRuleId"] for r in rules if r["IsEgress"] == egress]
            if ids:
                change(
                    ctx,
                    f"remove {sg['GroupName']}'s {'outbound' if egress else 'inbound'} rules that name another security group",
                    revoke_rules,
                    revoke,
                    sg["GroupId"],
                    ids,
                )
    for sg in sgs:
        if sg["GroupId"] in held_sgs:
            ok(f"left: security group {sg['GroupName']} (AgentCore's network interface {eni_ids} still uses it)")
            continue
        change(
            ctx,
            f"delete security group {sg['GroupName']}",
            dependent,
            f"security group {sg['GroupName']}",
            ec2.delete_security_group,
            GroupId=sg["GroupId"],
        )
    for sn in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]:
        if sn["SubnetId"] in held_subnets:
            ok(f"left: subnet {sn['CidrBlock']} (AgentCore's network interface {eni_ids} is in it)")
            continue
        change(
            ctx,
            f"delete subnet {sn['CidrBlock']}",
            dependent,
            f"subnet {sn['CidrBlock']}",
            ec2.delete_subnet,
            SubnetId=sn["SubnetId"],
        )
    for igw in ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])[
        "InternetGateways"
    ]:
        change(
            ctx,
            f"detach and delete internet gateway {igw['InternetGatewayId']}",
            detach_delete_igw,
            ctx,
            igw["InternetGatewayId"],
            vpc_id,
        )
    if enis:
        names = ", ".join(sg["GroupName"] for sg in sgs if sg["GroupId"] in held_sgs) or "none"
        left.append(
            f"VPC {vpc_id}, its security group {names} and the subnet AgentCore's network interface {eni_ids} is in "
            f"(all free). AWS removes the interface within {AGENTCORE_ENI_HOURS} hours of the last runtime's deletion; "
            "then `undeploy --delete-volumes` deletes the rest. A deploy before that just reuses them"
        )
        ok(f"left for later: VPC {vpc_id} (it can't go while AgentCore's network interface is in it)")
        return left
    change(ctx, f"delete VPC {vpc_id}", dependent, f"VPC {vpc_id}", ec2.delete_vpc, VpcId=vpc_id)
    return left


def detach_delete_igw(ctx: Ctx, igw_id: str, vpc_id: str) -> None:
    ctx.aws.ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    ctx.aws.ec2.delete_internet_gateway(InternetGatewayId=igw_id)


def retire_capacity_providers(ctx: Ctx, cps: list[dict]) -> None:
    """Each old capacity provider's sessions (DeleteCapacityProviderSession deletes the instance and its EBS volume),
    then the capacity provider itself. The session ids come from .state.json's uid and generation; without them,
    deleting the capacity provider deletes its sessions and volumes anyway."""
    known = ctx.state.get("instances", {})
    for c in cps:
        name = c["name"][len("devbox_") :]
        rec = known.get(name, {})
        uid = rec.get("uid") or ctx.uids.get(name)
        if not uid:
            warn(
                f"{name}'s uid is unknown, so their session ids are too; deleting the capacity provider removes them anyway"
            )
        else:
            for g in range(1, int(rec.get("generation") or 1) + 1):
                change(
                    ctx,
                    f"  delete {name}'s session generation {g} (its instance and EBS volume)",
                    delete_session,
                    ctx,
                    c["capacityProviderId"],
                    session_id(uid, g),
                )
        change(
            ctx,
            f"delete capacity provider {c['name']} (and every session and volume left on it)",
            delete_capacity_provider,
            ctx,
            c["capacityProviderId"],
        )


def role_references(ctx: Ctx, skip_runtimes: set[str], skip_cps: set[str]) -> dict[str, list[str]]:
    """Every role ARN (and instance profile ARN) the account's runtimes and capacity providers name, and who names it,
    leaving out the ones about to be deleted."""
    acc, refs = ctx.aws.acc, {}
    for r in paged(acc.list_agent_runtimes, "agentRuntimes"):
        if r["agentRuntimeId"] in skip_runtimes:
            continue
        cur = acc.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
        refs.setdefault(cur.get("roleArn", ""), []).append(f"runtime {r['agentRuntimeName']}")
    for c in paged(acc.list_capacity_providers, "capacityProviders"):
        if c["capacityProviderId"] in skip_cps:
            continue
        full = acc.get_capacity_provider(capacityProviderId=c["capacityProviderId"])
        op = (full.get("permissionsConfiguration") or {}).get("capacityProviderOperatorRoleArn", "")
        lp = (
            ((full.get("computeConfiguration") or {}).get("ec2Configuration") or {}).get("launchTemplateSource") or {}
        ).get("launchParameters") or {}
        refs.setdefault(op, []).append(f"capacity provider {c['name']}")
        refs.setdefault(lp.get("instanceProfileArn", ""), []).append(f"capacity provider {c['name']}")
    return refs


def retire_instances(ctx: Ctx) -> None:
    """The old Instances boxes, and nothing else: their runtimes devbox_<name>, their sessions and volumes, their capacity
    providers, and the roles only they use. The microVM runtimes devbox_vm_<name>, EFS and the network stay."""
    acc = ctx.aws.acc
    section(f"1 · The Instances runtimes devbox_<name> (the microVM runtimes {VM_RUNTIME_PREFIX}<name> stay)")
    legacy = []
    for r in paged(acc.list_agent_runtimes, "agentRuntimes"):
        if not LEGACY_NAME.match(r["agentRuntimeName"]):
            continue
        cur = acc.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
        if not cur.get("capacityProviderConfiguration"):
            warn(
                f"runtime {r['agentRuntimeName']} doesn't run on a capacity provider: not an Instances box, left alone"
            )
            continue
        legacy.append(r)
        change(
            ctx,
            f"delete runtime {r['agentRuntimeName']} ({r['agentRuntimeId']})",
            acc.delete_agent_runtime,
            agentRuntimeId=r["agentRuntimeId"],
        )
    if not legacy:
        ok("no Instances runtimes")
    cps = [c for c in paged(acc.list_capacity_providers, "capacityProviders") if LEGACY_NAME.match(c["name"])]
    if legacy and not ctx.check:
        for r in legacy:
            wait_for(
                f"runtime {r['agentRuntimeName']} to go",
                lambda r=r: acc.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"]),
                lambda v: v is None,
                timeout=900,
                every=10,
            )
        for c in cps:
            wait_for(
                f"runtime versions to detach from {c['name']}",
                lambda c=c: acc.list_agent_runtime_versions_by_capacity_provider(
                    capacityProviderId=c["capacityProviderId"]
                )["agentRuntimes"],
                lambda v: not v,
                timeout=900,
                every=15,
            )

    section("2 · Their sessions and EBS volumes (DeleteCapacityProviderSession), then the capacity providers")
    if cps:
        retire_capacity_providers(ctx, cps)
    else:
        ok("no devbox_<name> capacity providers")

    section("3 · The Instances roles, once nothing uses them")
    refs = role_references(ctx, {r["agentRuntimeId"] for r in legacy}, {c["capacityProviderId"] for c in cps})
    iam = ctx.aws.iam
    for name in (OPERATOR_ROLE, INSTANCE_ROLE, LEGACY_EXEC_ROLE):
        try:
            iam.get_role(RoleName=name)
        except ClientError as e:
            if not is_missing(e):
                raise
            ok(f"role {name}: not there")
            continue
        users = refs.get(f"arn:aws:iam::{ctx.account}:role/{name}", []) + (
            refs.get(f"arn:aws:iam::{ctx.account}:instance-profile/{name}", []) if name == INSTANCE_ROLE else []
        )
        if users:
            warn(f"kept: role {name}, still used by {', '.join(sorted(set(users)))}")
            continue
        change(
            ctx,
            f"delete role {name}" + (" and its instance profile" if name == INSTANCE_ROLE else ""),
            delete_role,
            ctx,
            name,
        )


def cmd_retire_instances(ctx: Ctx) -> int:
    say(
        f"{_c('1;37')}Dev box · retire-instances{_c('0')}  {_c('31')}(the old Instances boxes and their disks){_c('0')}"
    )
    prerequisites_light(ctx)
    ctx.check = True
    retire_instances(ctx)
    if not Report.changes:
        ctx.state.pop("instances", None)
        save_state(ctx.state)
        say()
        say(f"{_c('1;32')}Nothing to retire.{_c('0')} No Instances runtimes, capacity providers or roles are left.")
        return 0
    planned = Report.changes
    say()
    say(
        f"{_c('1;31')}This deletes the {planned} item(s) above{_c('0')} from account {ctx.account}, including every Instances box's "
        "EBS volume and anything on it (it can't be undone)."
    )
    say(
        f"  kept: the microVM boxes ({VM_RUNTIME_PREFIX}<name>), the EFS file system with everyone's folders, the network, the "
        "gateway and the edge."
    )
    if input(f"Type the account ID ({ctx.account}) to go ahead: ").strip() != ctx.account:
        die("Not confirmed. Nothing was deleted.")
    ctx.check, Report.changes = False, 0
    retire_instances(ctx)
    ctx.state.pop("instances", None)
    save_state(ctx.state)
    say()
    say(f"{_c('1;32')}Retired.{_c('0')} {Report.changes} item(s) deleted. The microVM boxes are all that's left.")
    return 1 if Report.problems else 0


def cmd_undeploy(ctx: Ctx, delete_volumes: bool, yes: bool) -> int:
    say(
        f"{_c('1;37')}Dev box · undeploy{_c('0')}"
        + (f"  {_c('31')}(--delete-volumes: everyone's files go too){_c('0')}" if delete_volumes else "")
    )
    prerequisites_light(ctx)
    ctx.check = True
    kept = undeploy_all(ctx, delete_volumes)
    if not Report.changes:
        say()
        say(f"{_c('1;32')}Nothing to remove.{_c('0')}")
        return 0
    planned = Report.changes
    say()
    say(f"{_c('1;31')}This removes the {planned} item(s) above{_c('0')} from account {ctx.account}.")
    for k in kept:
        say(f"  kept: {k}")
    if not yes and input(f"Type the account ID ({ctx.account}) to go ahead: ").strip() != ctx.account:
        die("Not confirmed. Nothing was removed.")
    ctx.check, Report.changes = False, 0
    left = undeploy_all(ctx, delete_volumes)
    vpc_left = delete_volumes and bool(find_vpc(ctx))
    if delete_volumes and not vpc_left:
        STATE_FILE.unlink(missing_ok=True)
    elif vpc_left:  # only the VPC's leftovers: remember the account and the old domain (the next deploy's Okta note)
        last = (ctx.state.get("edge") or {}).get("workbenchDomain") or ctx.state.get("lastWorkbenchDomain")
        save_state({"account": ctx.account, **({"lastWorkbenchDomain": last} if last else {})})
    else:
        for rec in [*ctx.state.get("boxes", {}).values(), *ctx.state.get("instances", {}).values()]:
            rec.pop("runtimeArn", None)
            rec.pop("runtimeId", None)
            rec.pop("execRoleArn", None)
        ctx.state.pop("gateway", None)
        last = (ctx.state.pop("edge", None) or {}).get("workbenchDomain") or ctx.state.get("lastWorkbenchDomain")
        if last:
            ctx.state["lastWorkbenchDomain"] = last  # the next deploy says what to change in Okta
        save_state(ctx.state)
    say()
    say(
        f"{_c('1;32')}Undeployed.{_c('0')} {Report.changes} item(s) removed."
        + ((" Left for AWS to finish: " + "; ".join(k for k in left if k.startswith("VPC "))) if vpc_left else "")
        + (
            ""
            if delete_volumes
            else " The EFS file system (everyone's files), its access points and mount target stay, and so does the VPC they sit in. "
            "A later `deploy` brings the rest back, and each new runtime mounts the same folder (spike item 13). "
            "The workbench gets a new CloudFront domain, so Okta's redirect URIs and Trusted Origin must change: deploy prints how."
        )
    )
    return 0


def prerequisites_light(ctx: Ctx) -> None:
    """network, reset-box and undeploy need only the AI account (and what .state.json remembers)."""
    section("0 · Account (read-only)")
    for e in ctx.s.errors:
        bad(f"devbox.env: {e}")
    if Report.problems:
        die("Fix the problems above, then run again.")
    try:
        ctx.account = ctx.aws.sts.get_caller_identity()["Account"]
    except Exception as e:  # noqa: BLE001 - no credentials, expired SSO session, unknown profile
        die(f"profile {ctx.s.ai_profile} can't sign in ({type(e).__name__}): refresh its credentials")
    if ctx.state.setdefault("account", ctx.account) != ctx.account:
        die(f".state.json belongs to account {ctx.state['account']}, but {ctx.s.ai_profile} is {ctx.account}")
    ok(f"{ctx.s.ai_profile} → account {ctx.account}")


# ============================================================================= main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="devbox.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--env", default=str(ENV_FILE), help="settings file (default: deploy/devbox.env)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    sub.add_parser("check", help="read-only: prerequisites, and what deploy would change")
    sub.add_parser("deploy", help="create or update everything (safe to re-run; never deletes the old Instances boxes)")
    net = sub.add_parser("network", help="egress firewall: allowlist | pause | resume")
    net.add_argument("action", choices=["allowlist", "pause", "resume"])
    un = sub.add_parser("undeploy", help="remove it (lists everything, then asks for the account id)")
    un.add_argument(
        "--delete-volumes",
        action="store_true",
        help="also delete the EFS file system (everyone's files) and any old capacity providers and their disks",
    )
    un.add_argument("--yes", action="store_true", help="don't ask (scripted runs)")
    rb = sub.add_parser(
        "reset-box", help="move one person's box to a new session (a fresh microVM; their EFS files stay)"
    )
    rb.add_argument("user")
    rb.add_argument("--yes", action="store_true", help="don't ask (scripted runs)")
    sub.add_parser(
        "retire-instances",
        help="delete the old Instances boxes: runtimes devbox_<name>, their sessions and volumes, "
        "the capacity providers and their roles (lists them, then asks for the account id)",
    )
    sub.add_parser("status", help="what is deployed (read-only)")
    args = ap.parse_args(argv)
    # Only the two admin profiles named in devbox.env, from your normal ~/.aws/config.
    for var in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
    ):
        os.environ.pop(var, None)

    try:
        ctx = make_ctx(args, check=args.cmd in ("check", "status"))
        if args.cmd in ("check", "deploy"):
            return cmd_deploy(ctx)
        if args.cmd == "network":
            return cmd_network(ctx, args.action)
        if args.cmd == "undeploy":
            return cmd_undeploy(ctx, args.delete_volumes, args.yes)
        if args.cmd == "reset-box":
            return cmd_reset_box(ctx, args.user, args.yes)
        if args.cmd == "retire-instances":
            return cmd_retire_instances(ctx)
        if args.cmd == "status":
            return cmd_status(ctx)
    except Stop:
        return 1
    except BotoCoreError as e:  # no credentials, a waiter that gave up, an endpoint that can't be reached
        say(f"  {_c('31')}✗ {type(e).__name__}: {e}{_c('0')}")
        return 1
    except KeyboardInterrupt:
        say("\nInterrupted.")
        return 130
    return 64


if __name__ == "__main__":
    sys.exit(main())
