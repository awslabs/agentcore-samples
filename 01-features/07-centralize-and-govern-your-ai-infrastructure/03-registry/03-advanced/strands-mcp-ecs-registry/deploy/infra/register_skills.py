"""register_skills.py — Add new AGENT_SKILLS records to an existing registry.

Registers one or more skill directories into an existing AWS Agent Registry.
Reads the SKILL.md from each directory and publishes + approves the record.

Designed to be run after the registry already exists (set up by setup.py).
Safe to re-run: skips skill names that already exist (by checking list_registry_records).

Usage:
    python register_skills.py \\
        --registry-arn arn:aws:agent-registry:us-east-1:123:registry/abc123 \\
        --region us-east-1 \\
        [--skill-name multi-quarter-trend-analysis]   # omit to register all new skills

Skills registered:
    multi-quarter-trend-analysis   — trend analysis across all quarters
    revenue-growth-analyst         — top-line revenue growth deep-dive
    cost-efficiency-analyzer       — cost structure and expense efficiency
    executive-financial-briefing   — one-page CEO/CFO/board briefing
"""

import argparse
import json
import os
import time

from boto3.session import Session

# All skills in my_skills/ that this script can register
NEW_SKILLS = [
    "multi-quarter-trend-analysis",
    "revenue-growth-analyst",
    "cost-efficiency-analyzer",
    "executive-financial-briefing",
]

# Skill descriptions for the registry record (should mirror description in SKILL.md)
SKILL_DESCRIPTIONS = {
    "multi-quarter-trend-analysis": (
        "Analyzes financial trends across multiple quarters. Use for trend analysis, "
        "directional performance, trajectory, or multi-period comparison across 3+ quarters. "
        "Shows quarter-over-quarter movement for Gross Margin, EBITDA Margin, OpEx Ratio, "
        "and Revenue Growth. Use for 'how are we trending' or 'show me the trend'."
    ),
    "revenue-growth-analyst": (
        "Deep-dives into revenue growth patterns and top-line performance. Use when the user "
        "asks specifically about revenue growth, sales growth, revenue acceleration, growth "
        "trajectory, or wants to understand what is driving revenue changes. "
        "Covers QoQ growth rates, cumulative growth, momentum assessment, and benchmarking."
    ),
    "cost-efficiency-analyzer": (
        "Analyzes cost structure, cost efficiency, and expense management. Use when the user "
        "asks about costs, COGS, operating expenses, cost ratios, cost control, spending "
        "efficiency, or margin compression from the cost side. Also use for 'are we spending "
        "too much', 'cost breakdown', or 'how efficient are our operations'."
    ),
    "executive-financial-briefing": (
        "Generates a concise executive-level financial briefing for CEO, CFO, or board. "
        "Use when the user asks for a summary, briefing, executive summary, board update, "
        "financial overview, financial health check, or 'how is the business doing'. "
        "Covers full P&L in one page with action items."
    ),
}


def separator(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def get_existing_skill_names(registry_client, registry_id: str) -> set[str]:
    """Return the set of skill names already in the registry."""
    existing = set()
    try:
        resp = registry_client.list_registry_records(registryId=registry_id)
        for rec in resp.get("registryRecords", []):
            if rec.get("recordType") == "SKILL":
                existing.add(rec["name"])
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: could not list existing records: {e}")
    return existing


def wait_for_record(registry_client, registry_id, record_id, target="DRAFT"):
    for _ in range(20):
        r = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = r["status"]
        if status == target:
            return
        if "FAILED" in status:
            raise RuntimeError(f"Record failed: {status}")
        time.sleep(5)
    raise TimeoutError(f"Record did not reach {target} within 100s")


def register_skill(registry_client, registry_id: str, skill_name: str, skills_root: str) -> str:
    skill_md_path = os.path.join(skills_root, skill_name, "SKILL.md")
    if not os.path.exists(skill_md_path):
        raise FileNotFoundError(f"SKILL.md not found: {skill_md_path}")

    with open(skill_md_path, encoding="utf-8") as f:
        skill_md = f.read()

    description = SKILL_DESCRIPTIONS.get(skill_name, f"Skill: {skill_name}")

    resp = registry_client.create_registry_record(
        registryId=registry_id,
        name=skill_name,
        description=description,
        recordType="SKILL",
        descriptors={
            "agentSkillsDefinition": {
                "data": json.dumps({"packages": []}),
                "additionalData": {"skillMd": {"data": skill_md}},
            }
        },
        recordVersion="1.0",
    )
    record_id = resp["recordArn"].split("/")[-1]
    print(f"  Created record: {record_id}")
    wait_for_record(registry_client, registry_id, record_id, "DRAFT")
    return record_id


def approve_record(registry_client, registry_id: str, record_id: str):
    registry_client.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
    print("  Submitted → PENDING_APPROVAL")
    time.sleep(2)
    registry_client.update_registry_record_status(
        registryId=registry_id,
        recordId=record_id,
        status="APPROVED",
        statusReason="Approved by admin during skill registration",
    )
    print("  Approved → APPROVED")


def main():
    parser = argparse.ArgumentParser(description="Register new skills into an existing registry")
    parser.add_argument("--registry-arn", required=True, help="ARN of the existing registry")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--skill-name",
        default="",
        help="Register only this skill (default: all new skills)",
    )
    parser.add_argument(
        "--skills-root",
        default="",
        help="Path to my_skills/ root (default: ../../my_skills relative to this script)",
    )
    args = parser.parse_args()

    registry_arn = args.registry_arn
    registry_id = registry_arn.split("/")[-1]

    skills_root = args.skills_root or os.path.join(os.path.dirname(__file__), "..", "..", "my_skills")
    skills_root = os.path.abspath(skills_root)

    session = Session(region_name=args.region)
    registry_client = session.client("agent-registry-control")

    # Determine which skills to register
    to_register = [args.skill_name] if args.skill_name else NEW_SKILLS

    separator("Checking existing records")
    existing = get_existing_skill_names(registry_client, registry_id)
    print(f"  Existing AGENT_SKILLS records: {existing or '(none)'}")

    registered = []
    skipped = []

    for skill_name in to_register:
        separator(f"Registering: {skill_name}")
        if skill_name in existing:
            print(f"  SKIP — '{skill_name}' already exists in registry.")
            skipped.append(skill_name)
            continue

        print(f"  Skills root: {skills_root}")
        record_id = register_skill(registry_client, registry_id, skill_name, skills_root)
        approve_record(registry_client, registry_id, record_id)
        registered.append(skill_name)
        print(f"  ✅ {skill_name} registered and approved.")

    separator("Summary")
    print(f"  Registered: {registered or '(none)'}")
    print(f"  Skipped (already exist): {skipped or '(none)'}")
    if registered:
        print("\n  Note: Search index takes ~60–100s to reflect new records.")
        print("  Redeploy the agent ECS service if it was running before these skills were added.")


if __name__ == "__main__":
    main()
