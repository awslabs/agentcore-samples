"""Evaluate coding-tool authorization requests locally. Never dispatch a tool."""

from pathlib import Path

from cedarpy import PolicySet, is_authorized

GATEWAY = "arn:aws:bedrock-agentcore:us-east-1:111111111111:gateway/example-only"
POLICIES = Path(__file__).with_name("policies")


def load_policies():
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(POLICIES.glob("*.cedar")))
    return PolicySet.from_str(source.replace("<gateway_arn>", GATEWAY))


def decide(policies, action, arguments, gateway=GATEWAY):
    request = {
        "principal": {"type": "Demo::User", "id": "developer"},
        "action": {"type": "AgentCore::Action", "id": action},
        "resource": {"type": "AgentCore::Gateway", "id": gateway},
        "context": {"input": arguments},
    }
    result = is_authorized(request, policies, entities=[])
    if result.diagnostics.errors:
        raise ValueError(f"Cedar evaluation error: {result.diagnostics.errors}")
    if result.allowed:
        return "ALLOW"
    return "EXPLICIT DENY" if result.diagnostics.reasons else "DEFAULT DENY"


if __name__ == "__main__":
    policies = load_policies()
    for action, args in [
        ("FileSystemTarget___read_file", {"path": "/workspace/src/example.py"}),
        ("FileSystemTarget___read_file", {"path": "/other/example.py"}),
        ("ShellTarget___execute", {"command": "npm test"}),
        ("HttpTarget___http_request", {}),
    ]:
        print(f"{decide(policies, action, args):14} {action} {args}")
