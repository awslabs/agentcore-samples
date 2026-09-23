"""
Setup Cedar policies for the Healthcare Appointment Agent.

Creates a policy engine and deploys four Cedar policies that demonstrate
deterministic, tool-level access control:

  1. Identity-Based   — patients can only read their own records
  2. Scope-Based R/W  — OAuth scopes gate read vs write tools
  3. Time-Based       — getSlots restricted to clinic hours (9 AM–9 PM UTC)
  4. Forbid           — hard deny on patient booking (overrides all permits)

How it works:
  - The gateway extracts JWT claims (role, patient_id, scope) from the OAuth token
  - Cedar evaluates each tool call against the policies
  - Default-deny: if no permit matches, the request is denied
  - Forbid-overrides-permit: a forbid rule always wins

Usage:
  python policy/setup_policy.py                # deploy policies
  python policy/setup_policy.py --cleanup      # remove everything
"""

import argparse
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ENGINE_NAME = "HealthcarePolicyEngine"


def load_config():
    """Load .env configuration from project root."""
    config_file = Path(__file__).parent.parent / ".env"
    if not config_file.exists():
        print("❌ .env not found — run init_env.py first")
        sys.exit(1)

    config = {}
    with open(config_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip('"')
    return config


def get_session(config):
    """Create boto3 session with optional profile support."""
    profile = config.get("awscred_profile_name")
    region = config.get("region") or config.get("aws_default_region", "us-east-1")
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


# ── Policy Engine helpers ───────────────────────────────────────────────


def find_engine(client):
    """Return (engine_id, engine_arn) if the engine already exists, else (None, None)."""
    for eng in client.list_policy_engines().get("policyEngines", []):
        if eng["name"] == ENGINE_NAME:
            return eng["policyEngineId"], eng["policyEngineArn"]
    return None, None


def create_engine(client):
    """Create the policy engine (or return existing)."""
    eid, earn = find_engine(client)
    if eid:
        print(f"⚠️  Engine '{ENGINE_NAME}' already exists: {eid}")
        return eid, earn

    print(f"\n📝 Creating policy engine: {ENGINE_NAME} ...")
    resp = client.create_policy_engine(
        name=ENGINE_NAME,
        description="Cedar policies for healthcare appointment agent — identity, scope, time, forbid",
    )
    eid, earn = resp["policyEngineId"], resp["policyEngineArn"]
    print(f"✅ Engine created: {eid}")

    # Wait for engine to become ACTIVE before creating policies
    print("   ⏳ Waiting for engine to become ACTIVE ...")
    for _ in range(30):
        time.sleep(2)
        eng = client.get_policy_engine(policyEngineId=eid)
        status = eng.get("status", "UNKNOWN")
        if status == "ACTIVE":
            print("   ✅ Engine is ACTIVE")
            break
    else:
        print(f"   ⚠️  Engine status: {status} — proceeding anyway")

    return eid, earn


def attach_engine(client, gateway_id, engine_arn, mode="ENFORCE"):
    """Attach the engine to the gateway.

    Mode options:
      - ENFORCE: Cedar policies actively block denied requests (production use)
      - LOG_ONLY: Cedar policies evaluate but only log decisions without blocking
    """
    print(f"\n🔒 Attaching engine to gateway {gateway_id} (mode={mode}) ...")
    gw = client.get_gateway(gatewayIdentifier=gateway_id)

    pe = gw.get("policyEngineConfiguration", {})
    if pe and pe.get("arn") == engine_arn:
        print(f"   Already attached (mode: {pe.get('mode')})")
        return

    client.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gw["name"],
        roleArn=gw["roleArn"],
        protocolType=gw["protocolType"],
        authorizerType=gw["authorizerType"],
        authorizerConfiguration=gw.get("authorizerConfiguration", {}),
        policyEngineConfiguration={"arn": engine_arn, "mode": mode},
    )
    print("   ⏳ Waiting 10s for propagation ...")
    time.sleep(10)
    print("   ✅ Attached")


# ── Individual policy creators ──────────────────────────────────────────


def _create_policy(client, engine_id, name, description, statement):
    """Helper: create a single policy, skip if it already exists."""
    try:
        resp = client.create_policy(
            policyEngineId=engine_id,
            name=name,
            description=description,
            definition={"cedar": {"statement": statement}},
            validationMode="IGNORE_ALL_FINDINGS",
        )
        pid = resp["policyId"]
        print(f"   ✅ {name}: {pid}")
        return pid
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"   ⚠️  {name}: already exists")
            return None
        raise


def create_identity_policies(client, engine_id, gateway_arn):
    """Policy 1 — Identity-Based Access (patient-scoped)."""
    print("\n📋 Policy 1: Identity-Based Access")

    _create_policy(
        client,
        engine_id,
        "IdentityGetPatient",
        "Patient can read their own patient record (patient_id matches claim)",
        f"""permit(
  principal,
  action == AgentCore::Action::"Target1___getPatient",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("role") &&
  principal.getTag("role") == "patient" &&
  context.input has patient_id &&
  principal.hasTag("patient_id") &&
  context.input.patient_id == principal.getTag("patient_id")
}};""",
    )

    _create_policy(
        client,
        engine_id,
        "IdentitySearchImmunization",
        "Patient can search their own immunization records (search_value matches claim)",
        f"""permit(
  principal,
  action == AgentCore::Action::"Target1___searchImmunization",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("role") &&
  principal.getTag("role") == "patient" &&
  context.input has search_value &&
  principal.hasTag("patient_id") &&
  context.input.search_value == principal.getTag("patient_id")
}};""",
    )


def create_scope_policies(client, engine_id, gateway_arn):
    """Policy 2 — Scope-Based Read/Write Separation (OAuth scopes)."""
    print("\n📋 Policy 2: Scope-Based Read/Write Separation")

    _create_policy(
        client,
        engine_id,
        "ScopeReadTools",
        "Tokens with healthcare.read scope can use read tools (getPatient, searchImmunization, getSlots)",
        f"""permit(
  principal,
  action in [
    AgentCore::Action::"Target1___getPatient",
    AgentCore::Action::"Target1___searchImmunization",
    AgentCore::Action::"Target1___getSlots"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("scope") &&
  principal.getTag("scope") like "*healthcare.read*"
}};""",
    )

    _create_policy(
        client,
        engine_id,
        "ScopeWriteTools",
        "Tokens with healthcare.write scope can book appointments",
        f"""permit(
  principal,
  action == AgentCore::Action::"Target1___bookAppointment",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("scope") &&
  principal.getTag("scope") like "*healthcare.write*"
}};""",
    )


def create_time_policies(client, engine_id, gateway_arn):
    """Use Case 3 — Time-Based Access (Clinic Hours).

    Uses context.system.now — a system-provided timestamp injected by the
    gateway. The agent cannot manipulate this value. Cedar's toTime() and
    duration() extension functions compare the current time-of-day against
    the allowed window (9 AM – 9 PM UTC).

    This matches the blog's natural language prompt:
      "Allow users to get slots only between 9am and 9pm UTC"
    """
    print("\n📋 Use Case 3: Time-Based Access — Clinic Hours")

    _create_policy(
        client,
        engine_id,
        "ClinicHoursGetSlots",
        "Allow getSlots only between 9 AM and 9 PM UTC (uses gateway system clock)",
        f"""permit(
  principal,
  action == AgentCore::Action::"Target1___getSlots",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  (!(((context.system.now).toTime()) < (duration("9h")))) &&
  ((((context.system.now).toTime()) <= (duration("21h"))))
}};""",
    )


def create_forbid_policies(client, engine_id, gateway_arn):
    """Policy 4 — Forbid Rules (Hard Boundaries)."""
    print("\n📋 Policy 4: Forbid Rules — Hard Boundaries")

    _create_policy(
        client,
        engine_id,
        "ForbidPatientBooking",
        "Hard deny: patients cannot book appointments (forbid overrides permit)",
        f"""forbid(
  principal,
  action == AgentCore::Action::"Target1___bookAppointment",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("role") &&
  principal.getTag("role") == "patient"
}};""",
    )


# ── Cleanup ─────────────────────────────────────────────────────────────


def cleanup(client, gateway_id):
    """Detach engine from gateway, delete all policies, delete engine."""
    print("\n🧹 Cleaning up policy engine ...")

    gw = client.get_gateway(gatewayIdentifier=gateway_id)
    pe = gw.get("policyEngineConfiguration", {})
    eid, _ = find_engine(client)

    if pe and pe.get("arn") and eid:
        print("   Detaching engine from gateway ...")
        client.update_gateway(
            gatewayIdentifier=gateway_id,
            name=gw["name"],
            roleArn=gw["roleArn"],
            protocolType=gw["protocolType"],
            authorizerType=gw["authorizerType"],
            authorizerConfiguration=gw.get("authorizerConfiguration", {}),
        )
        time.sleep(5)

    if not eid:
        print("   Engine not found — nothing to delete")
        return

    policies = client.list_policies(policyEngineId=eid).get("policies", [])
    for p in policies:
        print(f"   Deleting policy: {p['name']} ({p['policyId']})")
        client.delete_policy(policyEngineId=eid, policyId=p["policyId"])

    if policies:
        print("   ⏳ Waiting 10s for policy deletion to propagate ...")
        time.sleep(10)

    print(f"   Deleting engine: {eid}")
    client.delete_policy_engine(policyEngineId=eid)
    print("   ✅ Cleanup complete")


# ── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Setup Cedar policies for Healthcare Appointment Agent",
    )
    parser.add_argument("--cleanup", action="store_true", help="Remove engine and all policies")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 Healthcare Policy Engine — Setup")
    print("=" * 70)

    config = load_config()
    session = get_session(config)
    client = session.client("bedrock-agentcore-control")

    gateway_id = config.get("gateway_id")
    gateway_arn = config.get("gateway_arn")
    if not gateway_id or not gateway_arn:
        print("❌ gateway_id / gateway_arn not found in .env")
        sys.exit(1)

    print(f"   Gateway ID:  {gateway_id}")
    print(f"   Gateway ARN: {gateway_arn}")

    if args.cleanup:
        cleanup(client, gateway_id)
        return

    engine_id, engine_arn = create_engine(client)

    create_identity_policies(client, engine_id, gateway_arn)
    create_scope_policies(client, engine_id, gateway_arn)
    create_time_policies(client, engine_id, gateway_arn)
    create_forbid_policies(client, engine_id, gateway_arn)

    attach_engine(client, gateway_id, engine_arn)

    policies = client.list_policies(policyEngineId=engine_id).get("policies", [])
    print("\n" + "=" * 70)
    print("✅ POLICY SETUP COMPLETE")
    print("=" * 70)
    print(f"   Engine: {ENGINE_NAME} ({engine_id})")
    print(f"   Policies: {len(policies)}")
    for p in policies:
        print(f"     • {p['name']}")
    print("\n   Mode: ENFORCE")
    print("\n📋 Policies deployed:")
    print("   1. Identity-based — patients read only own data")
    print("   2. Scope-based R/W — OAuth scopes gate read vs write tools")
    print("   3. Time-based — getSlots restricted to clinic hours (9 AM–9 PM UTC)")
    print("   4. Forbid — hard deny patient booking")
    print("\n▶️  Next: python policy/test_policy.py")
    print("=" * 70)


if __name__ == "__main__":
    main()
