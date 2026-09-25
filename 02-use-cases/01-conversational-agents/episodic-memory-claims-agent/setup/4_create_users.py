"""
Create demo users in Cognito User Pool.

Idempotent — skips users that already exist. Run after 0_setup_infra.sh
creates the User Pool.

Usage:
    python setup/create_users.py
"""

import json
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.config import load_config

DEMO_USERS = [
    {
        "username": "bob-policyholder",
        "password": "DemoPass1!",
        "actor_id": "PH-1001",
        "name": "Bob Thompson",
        "group": "policyholders",
    },
    {
        "username": "alice-policyholder",
        "password": "DemoPass2!",
        "actor_id": "PH-1042",
        "name": "Alice Martinez",
        "group": "policyholders",
    },
    {
        "username": "charlie-policyholder",
        "password": "DemoPass3!",
        "actor_id": "PH-1087",
        "name": "Charlie Davis",
        "group": "policyholders",
    },
    {
        "username": "david-policyholder",
        "password": "DemoPass4!",
        "actor_id": "PH-2001",
        "name": "David Park",
        "group": "policyholders",
    },
    {
        "username": "sarah-policyholder",
        "password": "DemoPass5!",
        "actor_id": "PH-2050",
        "name": "Sarah Chen",
        "group": "policyholders",
    },
    {
        "username": "marcus-policyholder",
        "password": "DemoPass6!",
        "actor_id": "PH-3001",
        "name": "Marcus Rivera",
        "group": "policyholders",
    },
    {
        "username": "lisa-policyholder",
        "password": "DemoPass7!",
        "actor_id": "PH-3050",
        "name": "Lisa Nguyen",
        "group": "policyholders",
    },
    {
        "username": "dana-adjuster",
        "password": "AdjustPass1!",
        "actor_id": "",
        "name": "Dana Reyes",
        "group": "adjusters",
    },
    {"username": "amy-admin", "password": "AdminPass1!", "actor_id": "", "name": "Amy Lin", "group": "admins"},
]


def main():
    config = load_config()
    region = config["region"]
    pool_id = config["cognito"]["pool_id"]

    cognito = boto3.client("cognito-idp", region_name=region)

    print(f"Creating users in pool {pool_id} ({region})")
    print()

    for u in DEMO_USERS:
        attrs = [{"Name": "name", "Value": u["name"]}]
        if u["actor_id"]:
            attrs.append({"Name": "custom:actor_id", "Value": u["actor_id"]})

        try:
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=u["username"],
                TemporaryPassword=u["password"],
                UserAttributes=attrs,
                MessageAction="SUPPRESS",
            )
            cognito.admin_set_user_password(
                UserPoolId=pool_id,
                Username=u["username"],
                Password=u["password"],
                Permanent=True,
            )
            print(f"  created: {u['username']} ({u['actor_id'] or u['group']})")
        except cognito.exceptions.UsernameExistsException:
            print(f"  exists:  {u['username']}")
        except Exception as e:  # noqa: BLE001 - log and continue creating remaining users
            print(f"  ERROR:   {u['username']} — {e}")

        # Ensure user is in their group (idempotent — runs even if user already existed)
        group_map = {"policyholders": "policyholders", "adjusters": "adjuster", "admins": "admin"}
        cognito_group = group_map.get(u["group"], "")
        if cognito_group:
            try:
                cognito.admin_add_user_to_group(
                    UserPoolId=pool_id,
                    Username=u["username"],
                    GroupName=cognito_group,
                )
            except Exception as e:  # noqa: BLE001 - best-effort add-to-group; continue on error
                print(f"  (add-to-group note: {e})")

    # Update config.json with all users (for hydration scripts)
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        cfg["users"] = [
            {"username": u["username"], "password": u["password"], "actor_id": u["actor_id"]}
            for u in DEMO_USERS
            if u["actor_id"]
        ]
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"\n  Updated {config_path} with {len(cfg['users'])} policyholder users")


if __name__ == "__main__":
    main()
