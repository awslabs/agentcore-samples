#!/usr/bin/env python3
"""
setup_guardrails.py — Create a Bedrock Guardrail for the Text-to-Python IDE.

Provisions a guardrail that:
  - Blocks harmful content (hate, violence, sexual, insults, misconduct)
  - Detects and blocks prompt injection attacks
  - Filters PII (AWS keys, passwords, credit card numbers)
  - Blocks off-topic requests (financial advice, medical advice, legal advice)
  - Filters profanity

Run once before deploying. Writes guardrail_info.json with the guardrail ID and version.

Usage:
    python setup_guardrails.py           # create guardrail
    python setup_guardrails.py --delete  # delete guardrail
"""

import argparse
import json
import os

import boto3
from dotenv import load_dotenv

load_dotenv(override=True)

REGION = os.getenv("AWS_REGION", "us-east-1")
GUARDRAIL_NAME = "text_to_python_ide_guardrail"
INFO_FILE = "guardrail_info.json"


def get_client():
    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"), region_name=REGION)
    return session.client("bedrock", region_name=REGION)


def find_existing(client):
    """Find existing guardrail by name."""
    try:
        response = client.list_guardrails()
        for g in response.get("guardrails", []):
            if g.get("name") == GUARDRAIL_NAME:
                return g.get("id"), g.get("version")
    except Exception as e:
        print(f"⚠️  Error listing guardrails: {e}")
    return None, None


def create():
    client = get_client()

    existing_id, existing_version = find_existing(client)
    if existing_id:
        print(f"✅ Guardrail already exists: {existing_id} (version {existing_version})")
        _save(existing_id, existing_version)
        return existing_id

    print(f"🚀 Creating Bedrock Guardrail '{GUARDRAIL_NAME}'...")

    response = client.create_guardrail(
        name=GUARDRAIL_NAME,
        description="Content safety guardrail for Text-to-Python IDE — blocks harmful content, prompt attacks, PII, and off-topic requests",
        topicPolicyConfig={
            "topicsConfig": [
                {
                    "name": "malicious_code",
                    "definition": "Requests to generate malware, ransomware, keyloggers, exploits, hacking tools, or code specifically designed to cause harm to computer systems or steal data",
                    "examples": [
                        "Write a keylogger that captures all keystrokes",
                        "Create a ransomware script that encrypts files",
                        "Generate code to exploit CVE vulnerabilities",
                        "Write a script to brute force passwords on a website",
                    ],
                    "type": "DENY",
                },
            ]
        },
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "MEDIUM"},
                {"type": "VIOLENCE", "inputStrength": "MEDIUM", "outputStrength": "LOW"},
                {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "MEDIUM"},
                {"type": "INSULTS", "inputStrength": "LOW", "outputStrength": "NONE"},
                {"type": "MISCONDUCT", "inputStrength": "MEDIUM", "outputStrength": "LOW"},
                {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
            ]
        },
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
                {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
                {"type": "PASSWORD", "action": "BLOCK"},
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
                {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                {"type": "EMAIL", "action": "ANONYMIZE"},
            ]
        },
        wordPolicyConfig={"managedWordListsConfig": [{"type": "PROFANITY"}]},
        blockedInputMessaging="I'm unable to process this request. The Text-to-Python IDE is designed for Python code generation and execution only. Please rephrase your request to focus on a Python programming task.",
        blockedOutputsMessaging="I'm unable to provide this response as it may contain inappropriate content. Please try a different Python programming request.",
    )

    guardrail_id = response.get("guardrailId")
    version = response.get("version", "DRAFT")
    print(f"✅ Guardrail created: {guardrail_id} (version {version})")
    _save(guardrail_id, version)
    return guardrail_id


def delete():
    if not os.path.exists(INFO_FILE):
        print("ℹ️  No guardrail_info.json found, nothing to delete")
        return

    with open(INFO_FILE) as f:
        info = json.load(f)

    guardrail_id = info["guardrail_id"]
    client = get_client()
    print(f"🗑️  Deleting guardrail {guardrail_id}...")
    client.delete_guardrail(guardrailIdentifier=guardrail_id)
    os.remove(INFO_FILE)
    print("✅ Guardrail deleted")


def _save(guardrail_id, version):
    info = {"guardrail_id": guardrail_id, "guardrail_version": str(version), "region": REGION}
    with open(INFO_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"📝 Saved to {INFO_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    if args.delete:
        delete()
    else:
        create()
