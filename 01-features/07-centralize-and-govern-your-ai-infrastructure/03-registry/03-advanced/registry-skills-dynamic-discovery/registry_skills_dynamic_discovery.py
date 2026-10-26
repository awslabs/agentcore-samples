"""
Publishing and Discovering Agent Skills with AWS Agent Registry

Demonstrates the AGENT_SKILLS descriptor type:
  1. Create an Agent Registry (autoApproval: false)
  2. Register an Agent Skill record (PDF processing) with SKILL.md + skill definition
  3. Approve the skill record
  4. Build a Strands Agent with a search_and_load_skill tool that discovers and
     downloads skills from the registry at runtime
  5. Execute a task using the dynamically loaded skill

Usage:
    python registry_skills_dynamic_discovery.py

Prerequisites:
    - boto3 >= 1.42.87
    - strands-agents, strands-agents-tools installed (pip install -r requirements.txt)
    - AWS credentials configured
    - skill_registry/pdf_SKILL.md file present in the same directory
    - AWS_DEFAULT_REGION set (or defaults to session region)
"""

import json
import os
import time

from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────
boto_session = Session()
AWS_REGION = boto_session.region_name

registry_client = boto_session.client("agent-registry-control", region_name=AWS_REGION)
search_client = boto_session.client("agent-registry", region_name=AWS_REGION)

print(f"Session ready | Region: {AWS_REGION}")


# ── ANSI colors ───────────────────────────────────────────────────────────────
class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


# ── Helpers ───────────────────────────────────────────────────────────────────


def wait_for_record_draft(registry_id, record_id, interval=3):
    while True:
        resp = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = resp["status"]
        if status == "DRAFT":
            return resp
        if status.endswith("_FAILED"):
            raise Exception(f"Record failed: {status}")  # noqa: TRY002
        time.sleep(interval)


def wait_for_registry(registry_id, interval=5):
    while True:
        resp = registry_client.get_registry(registryId=registry_id)
        status = resp["status"]
        if status == "READY":
            print(f"  {C.GREEN}✅ Registry Status: {status}{C.RESET}")
            resp.pop("ResponseMetadata", None)
            print(json.dumps(resp, indent=2, default=str))
            return resp
        if status.endswith("_FAILED"):
            print(f"  {C.RED}❌ Registry Status: {status}{C.RESET}")
            raise Exception(f"Registry failed: {status} - {resp.get('statusReason')}")  # noqa: TRY002
        print(f"  {C.YELLOW}⏳ Registry Status: {status}{C.RESET}")
        time.sleep(interval)


def pretty_print_response(response):
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(data, indent=2, default=str))


def load_skill_md(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ── 1. Create Agent Registry ──────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 1. Create Agent Registry ==={C.RESET}")

create_registry_response = registry_client.create_registry(
    name="Skills_Registry",
    description="Registry for Skills",
    approvalConfiguration={"autoApprovalRules": []},
)

REGISTRY_ARN = create_registry_response["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

wait_for_registry(REGISTRY_ID)

print(f"  {C.GREEN}✅ Registry created!{C.RESET}")
print(f"  {C.BOLD}ARN:{C.RESET}  {C.CYAN}{REGISTRY_ARN}{C.RESET}")
print(f"  {C.BOLD}ID:{C.RESET}   {C.CYAN}{REGISTRY_ID}{C.RESET}")

# ── 2. Register Agent Skill Record ────────────────────────────────────────────
print(f"\n{C.BOLD}=== 2. Register an Agent Skill ==={C.RESET}")

skill_definition_schema = json.dumps(
    {
        "repository": {
            "url": "https://github.com/anthropics/skills/tree/main/skills/pdf",
            "source": "github",
        },
        "packages": [
            {"registryType": "pypi", "identifier": "pypdf", "version": "5.1.0"},
            {"registryType": "pypi", "identifier": "reportlab", "version": "4.4.0"},
        ],
    }
)

# Load SKILL.md from the skill_registry sub-folder
script_dir = os.path.dirname(os.path.abspath(__file__))
skill_md_path = os.path.join(script_dir, "skill_registry", "pdf_SKILL.md")

skill_record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="PDF_Processing_Skill",
    description=(
        "Use this skill whenever the user wants to do anything with PDF files. "
        "This includes reading or extracting text/tables from PDFs, combining or merging "
        "multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks, "
        "creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting "
        "images, and OCR on scanned PDFs to make them searchable. "
        "If the user mentions a .pdf file or asks to produce one, use this skill."
    ),
    recordType="SKILL",
    descriptors={
        "agentSkillsDefinition": {
            "data": skill_definition_schema,
            "additionalData": {"skillMd": {"data": load_skill_md(skill_md_path)}},
        }
    },
    recordVersion="1.0",
)

SKILL_RECORD_ARN = skill_record_response["recordArn"]
SKILL_RECORD_ID = SKILL_RECORD_ARN.split("/")[-1]
print(f"  {C.GREEN}✅ Skill Record created: {C.CYAN}{SKILL_RECORD_ID}{C.RESET}")
wait_for_record_draft(REGISTRY_ID, SKILL_RECORD_ID)

# List records
records_response = registry_client.list_registry_records(registryId=REGISTRY_ID)
print(f"\n{C.BOLD}=== Registry Records ==={C.RESET}")
print(f"Found {len(records_response['registryRecords'])} record(s):\n")
for rec in records_response["registryRecords"]:
    status = rec["status"]
    sc = C.GREEN if status == "APPROVED" else C.YELLOW if status in ("DRAFT", "PENDING_APPROVAL") else C.RED
    print(
        f"  {sc}[{status}]{C.RESET} {rec['name']} | {C.CYAN}{rec['recordType']}{C.RESET} | {C.DIM}{rec['recordId']}{C.RESET}"
    )

# ── 3. Approve Skill Record ───────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 3. Approve the Skill Record ==={C.RESET}")

registry_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=SKILL_RECORD_ID)
print(f"  {C.YELLOW}⏳ Skill record → PENDING_APPROVAL{C.RESET}")

registry_client.update_registry_record_status(
    registryId=REGISTRY_ID,
    recordId=SKILL_RECORD_ID,
    statusReason="Approved by admin",
    status="APPROVED",
)
print(f"  {C.GREEN}✅ Skill record → APPROVED{C.RESET}")

# ── 4. Dynamic Skill Discovery and Execution ──────────────────────────────────
print(f"\n{C.BOLD}=== 4. Dynamic Skill Discovery and Execution ==={C.RESET}")

# Wait for search index to update
print(f"  {C.YELLOW}⏳ Waiting 100s for search index...{C.RESET}")
time.sleep(100)

import sys

sys.path.insert(0, script_dir)
if "utils" in sys.modules:
    del sys.modules["utils"]

from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import file_read
from utils.python_exec_tool import python_exec, run_shell
from utils.skill_loader import load_skill_from_registry


@tool
def search_and_load_skill(query: str) -> str:
    """Search the AWS Agent Registry for a skill and load it locally.

    Performs semantic search, downloads the top matching skill package
    (SKILL.md + supporting files), installs dependencies, and returns
    the skill instructions.

    Args:
        query: Natural language description of the skill needed (e.g., 'PDF processing').

    Returns:
        The skill's SKILL.md content with instructions for completing the task.
    """
    response = search_client.search_discoverable_registry_records(
        registryIds=[REGISTRY_ARN],
        searchQuery=query,
        maxResults=5,
    )
    response.pop("ResponseMetadata", None)

    records = response.get("registryRecords", [])
    if not records:
        return f"No skills found for query: {query}"

    print(f"Found {len(records)} skill(s) matching '{query}':")
    for i, rec in enumerate(records):
        print(f"  {i + 1}. {rec.get('name', 'unknown')} [{rec.get('recordType', '')}]")
    print(f"\nLoading top result: {records[0].get('name', 'unknown')}...")

    skill_dir, skill_md = load_skill_from_registry(response, record_index=0)

    abs_dir = os.path.abspath(skill_dir)
    skill_name = records[0].get("name", "unknown")
    return (
        f"Skill '{skill_name}' loaded into {abs_dir}.\n\n"
        f"SKILL.md instructions:\n\n{skill_md}\n\n"
        f"Use working_dir='{os.getcwd()}' when running code."
    )


print(f"  {C.GREEN}✅ search_and_load_skill tool ready.{C.RESET}")

# ── 4.2 Create the Agent ──────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 4.2 Create the Agent ==={C.RESET}")

MODEL_ID = "us.anthropic.claude-sonnet-4-6"

model = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
agent = Agent(
    model=model,
    tools=[search_and_load_skill, file_read, python_exec, run_shell],
    system_prompt=(
        "You are an agent with access to the AWS Agent Registry. "
        "When asked to perform a task, search the registry for a relevant skill. "
        "If a skill is found and loaded, you MUST follow its SKILL.md instructions exactly "
        "to complete the task. Use only the libraries and approach described in the skill instructions. "
        "Do NOT improvise or use alternative libraries."
    ),
)

print(f"  {C.GREEN}✅ Agent ready with dynamic skill discovery.{C.RESET}")
print(f"  {C.BOLD}Available tools:{C.RESET} {C.CYAN}{agent.tool_names}{C.RESET}")

# ── 4.3 Execute a Task ────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 4.3 Execute a Task with Dynamic Skill Discovery ==={C.RESET}")

agent("Create a simple PDF with title 'Hello from Agent Skills' and save it in the current directory")

# ── 5. Cleanup ────────────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 5. Cleanup ==={C.RESET}")

records = registry_client.list_registry_records(registryId=REGISTRY_ID)
for rec in records.get("registryRecords", []):
    registry_client.delete_registry_record(registryId=REGISTRY_ID, recordId=rec["recordId"])
    print(f"  {C.GREEN}✅ Deleted record: {C.DIM}{rec['recordId']}{C.RESET}")

registry_client.delete_registry(registryId=REGISTRY_ID)
print(f"  {C.GREEN}✅ Deleted registry: {C.DIM}{REGISTRY_ID}{C.RESET}")

print(f"\n  {C.GREEN}✅ Registry Skills demo complete!{C.RESET}")
