"""Files the root entrypoint writes from the runtime environment. Pure functions, so they can be
tested without a box. Values come from the deploy (trusted), but they end up in INI and JSON
files that decide who the box signs in as, so each one is checked before it's written. The checks
are whole-string matches: with re.match and "$", a trailing newline would slip through and start
a new INI line."""

import json
import os
import re
import tempfile

SSO_SESSION = "devbox"
AWS_PROFILE = "devbox"
REGION = "us-east-1"
TOOLS_SERVER = "web-search"
MCP_PROXY = "/opt/mcp-proxy/bin/mcp-proxy-for-aws"
DEVBOX_CLAUDE = "/usr/local/bin/devbox-claude"
PROJECTS = "/mnt/workspace/projects"
_VERSION = re.compile(r"\d+\.\d+\.\d+")

_START_URL = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?")
_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-\d{1,2}")
_ACCOUNT = re.compile(r"\d{12}")
_ROLE = re.compile(r"[A-Za-z0-9+=,.@_-]{1,64}")
_GATEWAY_URL = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~%/-]*)?")
# Model ids, inference profile ids and ARNs, including a "[1m]" style suffix.
_MODEL_ID = re.compile(r"[A-Za-z0-9._:/\-\[\]]{1,256}")
_ALIASES = ("opus", "sonnet", "haiku")


class ConfigError(ValueError):
    pass


def aws_config(env):
    """The root-owned AWS_CONFIG_FILE: one IdC session and one profile, the owner's tier role."""
    fields = {
        "DEVBOX_SSO_START_URL": _START_URL,
        "DEVBOX_SSO_REGION": _REGION,
        "DEVBOX_ACCOUNT_ID": _ACCOUNT,
        "DEVBOX_SSO_ROLE": _ROLE,
    }
    bad = [name for name, rule in fields.items() if not rule.fullmatch(env.get(name, ""))]
    if bad:
        raise ConfigError("missing or invalid: " + ", ".join(bad))
    return (
        f"[sso-session {SSO_SESSION}]\n"
        f"sso_start_url = {env['DEVBOX_SSO_START_URL']}\n"
        f"sso_region = {env['DEVBOX_SSO_REGION']}\n"
        "sso_registration_scopes = sso:account:access\n"
        f"[profile {AWS_PROFILE}]\n"
        f"sso_session = {SSO_SESSION}\n"
        f"sso_account_id = {env['DEVBOX_ACCOUNT_ID']}\n"
        f"sso_role_name = {env['DEVBOX_SSO_ROLE']}\n"
        f"region = {REGION}\n"
    )


def tier_dropin(models_json):
    """managed-settings.d/20-tier.json from DEVBOX_MODELS.

    Accepts either an alias map {"opus": "<id>", "sonnet": "<id>", ...} or settings-shaped JSON
    {"availableModels": [...], "model": "...", "env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "..."}}
    (the ANTHROPIC_DEFAULT_* keys may also sit at the top level)."""
    try:
        data = json.loads(models_json)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"DEVBOX_MODELS is not JSON: {err}") from None
    if not isinstance(data, dict):
        raise ConfigError("DEVBOX_MODELS must be a JSON object")
    env = {}
    sources = [data, data.get("env") if isinstance(data.get("env"), dict) else {}]
    for source in sources:
        for alias in _ALIASES:
            key = f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL"
            for name in (key, alias):
                value = source.get(name)
                if isinstance(value, str) and _MODEL_ID.fullmatch(value):
                    env[key] = value
    listed = data.get("availableModels")
    if listed is not None:
        if not isinstance(listed, list) or not all(isinstance(m, str) and _MODEL_ID.fullmatch(m) for m in listed):
            raise ConfigError("availableModels must be a list of model names")
        available = list(dict.fromkeys(listed))
    else:
        available = [a for a in _ALIASES if f"ANTHROPIC_DEFAULT_{a.upper()}_MODEL" in env]
    if not available:
        raise ConfigError("DEVBOX_MODELS names no models")
    model = data.get("model")
    if not (isinstance(model, str) and model in available):
        model = "sonnet" if "sonnet" in available else available[0]
    return {"model": model, "availableModels": available, "enforceAvailableModels": True, "env": env}


def managed_mcp(gateway_url):
    """The one MCP server Claude may use. Literal values, never ${VAR}: a variable would be read
    from each user's environment, so the user could point it anywhere."""
    if not gateway_url:
        return {"mcpServers": {}}
    if not _GATEWAY_URL.fullmatch(gateway_url):
        raise ConfigError("DEVBOX_TOOLS_GATEWAY_URL is not an https URL")
    return {
        "mcpServers": {
            TOOLS_SERVER: {
                "type": "stdio",
                "command": MCP_PROXY,
                "args": [
                    gateway_url,
                    "--service",
                    "bedrock-agentcore",
                    "--profile",
                    AWS_PROFILE,
                    "--region",
                    REGION,
                    "--disable-telemetry",
                ],
                "env": {"AWS_CONFIG_FILE": "/etc/devbox/aws-config", "AWS_PROFILE": AWS_PROFILE, "AWS_REGION": REGION},
            }
        }
    }


def folder_open_tasks():
    """projects/.vscode/tasks.json: open the resumable Claude Code terminal when the folder opens
    (the workbench sets task.allowAutomaticTasks to "on")."""
    return {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "Claude Code",
                "type": "process",
                "command": DEVBOX_CLAUDE,
                "isBackground": True,
                "problemMatcher": [],
                "presentation": {
                    "reveal": "always",
                    "panel": "dedicated",
                    "focus": True,
                    "echo": False,
                    "close": False,
                },
                "runOptions": {"runOn": "folderOpen", "instanceLimit": 1},
            }
        ],
    }


def claude_onboarding(claude_version=None):
    """What dev's ~/.claude.json needs so the folder-open terminal opens at Claude's prompt, not at
    the first-run theme picker, security notes and folder-trust question (whose default answer
    exits). Trust on the projects folder covers every folder under it. dev_helper adds only the
    keys that are missing, so nothing Claude or the person set is ever changed."""
    seed = {
        "numStartups": 1,
        "hasCompletedOnboarding": True,
        "theme": "dark",
        "officialMarketplaceAutoInstallAttempted": True,
        "projects": {PROJECTS: {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
    }
    if isinstance(claude_version, str) and _VERSION.fullmatch(claude_version):
        seed["lastOnboardingVersion"] = claude_version
    return seed


def sandbox_dropin(strict_ok, weak_ok):
    """managed-settings.d/30-sandbox.json, only when bwrap can make a user namespace here."""
    if not (strict_ok or weak_ok):
        return None
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "network": {"allowManagedDomainsOnly": True},
    }
    if not strict_ok:
        sandbox["enableWeakerNestedSandbox"] = True
    return {"sandbox": sandbox}


def write_root_file(path, text, mode=0o644):
    """Atomic write, so Claude Code never reads half a file (a malformed managed file stops it)."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path, data, mode=0o644):
    write_root_file(path, json.dumps(data, indent=2) + "\n", mode)
