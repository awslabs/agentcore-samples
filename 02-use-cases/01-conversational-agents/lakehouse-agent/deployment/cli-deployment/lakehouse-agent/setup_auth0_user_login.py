#!/usr/bin/env python3
"""
Set up Auth0 for user login in the Lakehouse Agent.

This script:
1. Creates an Auth0 Application for user login (Regular Web Application)
2. Creates test users in Auth0 (matching the Cognito/Okta personas)
3. Stores Auth0 configuration in SSM Parameter Store
4. Updates the IDP_PROVIDER flag to "auth0"

Prerequisites:
- Auth0 account with a tenant (you already have: dev-a3xj8d6if5f4qnwz.us.auth0.com)
- Auth0 Management API credentials (can be obtained from Auth0 Dashboard → Applications → APIs → Auth0 Management API → Machine to Machine Applications)

Usage:
    # Set environment variables
    export AUTH0_DOMAIN="dev-a3xj8d6if5f4qnwz.us.auth0.com"
    export AUTH0_MGMT_CLIENT_ID="your-m2m-client-id"
    export AUTH0_MGMT_CLIENT_SECRET="your-m2m-client-secret"
    
    # Run the script
    python setup_auth0_user_login.py

Note: The gateway is already configured to accept Auth0 tokens via the M2M setup.
      This script only adds user login capability.
"""

import json
import os
import sys

import boto3
import requests

# Auth0 Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "dev-a3xj8d6if5f4qnwz.us.auth0.com")
AUTH0_MGMT_CLIENT_ID = os.environ.get("AUTH0_MGMT_CLIENT_ID")
AUTH0_MGMT_CLIENT_SECRET = os.environ.get("AUTH0_MGMT_CLIENT_SECRET")

# The audience the gateway expects (already configured in the gateway)
AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "https://lakehouse-api")

# AWS Configuration
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Streamlit callback URL
REDIRECT_URI = "http://localhost:8501/"

# Test users to create
TEST_USERS = [
    {
        "email": "policyholder001@example.com",
        "name": "John Doe",
        "password": "Welcome123!",
        "groups": ["policyholders"],
    },
    {
        "email": "policyholder002@example.com",
        "name": "Jane Smith",
        "password": "Welcome123!",
        "groups": ["policyholders"],
    },
    {
        "email": "adjuster001@example.com",
        "name": "Mike Johnson",
        "password": "Welcome123!",
        "groups": ["adjusters"],
    },
    {
        "email": "adjuster002@example.com",
        "name": "Sarah Wilson",
        "password": "Welcome123!",
        "groups": ["adjusters"],
    },
    {
        "email": "admin@example.com",
        "name": "Admin User",
        "password": "Welcome123!",
        "groups": ["admins"],
    },
]


def get_management_token() -> str:
    """Get Auth0 Management API access token."""
    if not AUTH0_MGMT_CLIENT_ID or not AUTH0_MGMT_CLIENT_SECRET:
        raise ValueError(
            "AUTH0_MGMT_CLIENT_ID and AUTH0_MGMT_CLIENT_SECRET must be set.\n"
            "Create a Machine-to-Machine application in Auth0 Dashboard → Applications,\n"
            "then authorize it for the Auth0 Management API with the following scopes:\n"
            "  - create:clients, read:clients, update:clients\n"
            "  - create:users, read:users, update:users\n"
            "  - create:resource_servers, read:resource_servers"
        )
    
    response = requests.post(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "client_id": AUTH0_MGMT_CLIENT_ID,
            "client_secret": AUTH0_MGMT_CLIENT_SECRET,
            "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def create_or_get_api(mgmt_token: str) -> dict:
    """Create or get the API (Resource Server) for the lakehouse agent."""
    headers = {"Authorization": f"Bearer {mgmt_token}", "Content-Type": "application/json"}
    
    # Check if API already exists
    response = requests.get(
        f"https://{AUTH0_DOMAIN}/api/v2/resource-servers",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    
    for api in response.json():
        if api.get("identifier") == AUTH0_API_AUDIENCE:
            print(f"✅ API already exists: {api['name']} ({api['identifier']})")
            return api
    
    # Create API
    response = requests.post(
        f"https://{AUTH0_DOMAIN}/api/v2/resource-servers",
        headers=headers,
        json={
            "name": "Lakehouse API",
            "identifier": AUTH0_API_AUDIENCE,
            "signing_alg": "RS256",
            "scopes": [
                {"value": "claims.query", "description": "Query claims data"},
                {"value": "claims.notes", "description": "Access claim notes"},
            ],
            "allow_offline_access": True,
            "token_lifetime": 86400,
            "token_lifetime_for_web": 7200,
        },
        timeout=30,
    )
    response.raise_for_status()
    api = response.json()
    print(f"✅ Created API: {api['name']} ({api['identifier']})")
    return api


def create_or_get_application(mgmt_token: str) -> dict:
    """Create or get the Regular Web Application for user login."""
    headers = {"Authorization": f"Bearer {mgmt_token}", "Content-Type": "application/json"}
    app_name = "Lakehouse Agent - User Login"
    
    # Check if application already exists
    response = requests.get(
        f"https://{AUTH0_DOMAIN}/api/v2/clients",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    
    for client in response.json():
        if client.get("name") == app_name:
            print(f"✅ Application already exists: {client['name']} ({client['client_id']})")
            # Update callback URLs if needed
            if REDIRECT_URI not in (client.get("callbacks") or []):
                requests.patch(
                    f"https://{AUTH0_DOMAIN}/api/v2/clients/{client['client_id']}",
                    headers=headers,
                    json={
                        "callbacks": [REDIRECT_URI],
                        "allowed_logout_urls": [REDIRECT_URI],
                        "web_origins": ["http://localhost:8501"],
                    },
                    timeout=30,
                )
                print(f"   Updated callback URLs to include {REDIRECT_URI}")
            return client
    
    # Create Regular Web Application
    response = requests.post(
        f"https://{AUTH0_DOMAIN}/api/v2/clients",
        headers=headers,
        json={
            "name": app_name,
            "description": "Lakehouse Agent Streamlit UI - User Login with Authorization Code + PKCE",
            "app_type": "regular_web",
            "callbacks": [REDIRECT_URI],
            "allowed_logout_urls": [REDIRECT_URI],
            "web_origins": ["http://localhost:8501"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_post",
            "oidc_conformant": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    client = response.json()
    print(f"✅ Created application: {client['name']} ({client['client_id']})")
    return client


def get_database_connection(mgmt_token: str) -> str:
    """Get the default Username-Password-Authentication connection ID."""
    headers = {"Authorization": f"Bearer {mgmt_token}"}
    
    response = requests.get(
        f"https://{AUTH0_DOMAIN}/api/v2/connections",
        headers=headers,
        params={"strategy": "auth0"},
        timeout=30,
    )
    response.raise_for_status()
    
    for conn in response.json():
        if conn.get("name") == "Username-Password-Authentication":
            return conn["id"]
    
    raise ValueError("Username-Password-Authentication connection not found")


def create_or_get_user(mgmt_token: str, connection_id: str, user_data: dict) -> dict:
    """Create or get a test user."""
    headers = {"Authorization": f"Bearer {mgmt_token}", "Content-Type": "application/json"}
    email = user_data["email"]
    
    # Check if user exists
    response = requests.get(
        f"https://{AUTH0_DOMAIN}/api/v2/users-by-email",
        headers=headers,
        params={"email": email},
        timeout=30,
    )
    response.raise_for_status()
    
    users = response.json()
    if users:
        user = users[0]
        print(f"   User already exists: {email}")
        # Update user metadata with groups
        requests.patch(
            f"https://{AUTH0_DOMAIN}/api/v2/users/{user['user_id']}",
            headers=headers,
            json={
                "app_metadata": {"groups": user_data["groups"]},
            },
            timeout=30,
        )
        return user
    
    # Create user
    response = requests.post(
        f"https://{AUTH0_DOMAIN}/api/v2/users",
        headers=headers,
        json={
            "email": email,
            "name": user_data["name"],
            "password": user_data["password"],
            "connection": "Username-Password-Authentication",
            "email_verified": True,
            "app_metadata": {"groups": user_data["groups"]},
        },
        timeout=30,
    )
    if response.status_code == 409:
        print(f"   User already exists: {email}")
        return {}
    response.raise_for_status()
    user = response.json()
    print(f"   Created user: {email}")
    return user


def store_ssm_parameters(client_id: str, client_secret: str):
    """Store Auth0 configuration in SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    
    params = [
        ("/app/lakehouse-agent/auth0-domain", AUTH0_DOMAIN, "String"),
        ("/app/lakehouse-agent/auth0-client-id", client_id, "String"),
        ("/app/lakehouse-agent/auth0-client-secret", client_secret, "SecureString"),
        ("/app/lakehouse-agent/auth0-audience", AUTH0_API_AUDIENCE, "String"),
    ]
    
    for name, value, param_type in params:
        ssm.put_parameter(
            Name=name,
            Value=value,
            Type=param_type,
            Overwrite=True,
        )
        display_value = "***" if param_type == "SecureString" else value
        print(f"   {name} = {display_value}")


def set_idp_provider():
    """Set IDP_PROVIDER to auth0 in SSM."""
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    ssm.put_parameter(
        Name="/app/lakehouse-agent/idp-provider",
        Value="auth0",
        Type="String",
        Overwrite=True,
    )
    print("✅ IDP_PROVIDER = 'auth0' (persisted to SSM)")


def main():
    print("=" * 60)
    print("Auth0 User Login Setup for Lakehouse Agent")
    print("=" * 60)
    print(f"\nAuth0 Domain: {AUTH0_DOMAIN}")
    print(f"API Audience: {AUTH0_API_AUDIENCE}")
    print(f"AWS Region:   {AWS_REGION}")
    print()
    
    # Get Management API token
    print("1. Getting Auth0 Management API token...")
    try:
        mgmt_token = get_management_token()
        print("   ✅ Token obtained")
    except ValueError as e:
        print(f"   ❌ {e}")
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"   ❌ Failed to get token: {e}")
        print(f"   Response: {e.response.text[:500] if e.response else 'N/A'}")
        sys.exit(1)
    
    # Create or get API
    print("\n2. Creating/verifying API (Resource Server)...")
    try:
        api = create_or_get_api(mgmt_token)
    except requests.HTTPError as e:
        print(f"   ❌ Failed: {e}")
        print(f"   Response: {e.response.text[:500] if e.response else 'N/A'}")
        sys.exit(1)
    
    # Create or get application
    print("\n3. Creating/verifying Regular Web Application...")
    try:
        app = create_or_get_application(mgmt_token)
    except requests.HTTPError as e:
        print(f"   ❌ Failed: {e}")
        print(f"   Response: {e.response.text[:500] if e.response else 'N/A'}")
        sys.exit(1)
    
    # Create test users
    print("\n4. Creating test users...")
    try:
        conn_id = get_database_connection(mgmt_token)
        for user_data in TEST_USERS:
            create_or_get_user(mgmt_token, conn_id, user_data)
        print("   ✅ All test users ready")
    except requests.HTTPError as e:
        print(f"   ⚠️ User creation may have partially failed: {e}")
        # Continue anyway - users might already exist
    
    # Store SSM parameters
    print("\n5. Storing configuration in SSM Parameter Store...")
    try:
        store_ssm_parameters(app["client_id"], app["client_secret"])
        print("   ✅ SSM parameters stored")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        sys.exit(1)
    
    # Set IDP_PROVIDER
    print("\n6. Setting IDP_PROVIDER to 'auth0'...")
    try:
        set_idp_provider()
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("✅ Auth0 User Login Setup Complete!")
    print("=" * 60)
    print(f"""
Next steps:
1. Start the Streamlit app:
   cd streamlit-ui
   streamlit run streamlit_app.py

2. Log in with one of the test users:
   - policyholder001@example.com / Welcome123!
   - policyholder002@example.com / Welcome123!
   - adjuster001@example.com / Welcome123!
   - adjuster002@example.com / Welcome123!
   - admin@example.com / Welcome123!

The app will now use Auth0 for user login.
The gateway is already configured to accept Auth0 tokens.
""")


if __name__ == "__main__":
    main()
