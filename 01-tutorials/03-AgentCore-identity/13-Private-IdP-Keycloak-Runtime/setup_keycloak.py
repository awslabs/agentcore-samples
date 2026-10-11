"""
Setup script for Keycloak realm and client configuration.
Waits for Keycloak to boot, then configures:
  - Disables SSL requirement on master realm
  - Creates 'orion' realm with SSL disabled
  - Creates 'content-export-adapter' client with client_credentials grant
"""

import argparse
import json
import time
import urllib.request
import urllib.error
import urllib.parse


def wait_for_keycloak(base_url: str, timeout: int = 300):
    """Wait for Keycloak to be ready."""
    print(f"Waiting for Keycloak at {base_url}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{base_url}/realms/master")
            urllib.request.urlopen(req, timeout=5)
            print("✅ Keycloak is ready")
            return
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    raise TimeoutError(f"Keycloak not ready after {timeout}s")


def get_admin_token(base_url: str, password: str) -> str:
    """Get admin access token."""
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": "admin",
        "password": password,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]


def api_call(base_url: str, token: str, method: str, path: str, body: dict = None):
    """Make an authenticated API call to Keycloak Admin REST API."""
    url = f"{base_url}/admin{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        return urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        if e.code == 409:  # Conflict = already exists
            return None
        raise


def setup_keycloak(base_url: str, password: str, realm: str = "orion",
                   client_id: str = "content-export-adapter",
                   client_secret: str = "test-secret-12345"):
    """Configure Keycloak with realm and client."""
    wait_for_keycloak(base_url)
    token = get_admin_token(base_url, password)
    print(f"✅ Admin token obtained")

    # Disable SSL on master realm
    api_call(base_url, token, "PUT", "/realms/master", {"sslRequired": "none"})
    print("✅ Master realm SSL disabled")

    # Create realm
    api_call(base_url, token, "POST", "/realms", {
        "realm": realm,
        "enabled": True,
        "sslRequired": "none",
    })
    print(f"✅ Realm '{realm}' created")

    # Create client
    api_call(base_url, token, "POST", f"/realms/{realm}/clients", {
        "clientId": client_id,
        "enabled": True,
        "serviceAccountsEnabled": True,
        "clientAuthenticatorType": "client-secret",
        "secret": client_secret,
        "directAccessGrantsEnabled": True,
        "publicClient": False,
    })
    print(f"✅ Client '{client_id}' created (secret: {client_secret})")

    # Verify token endpoint works
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/realms/{realm}/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    print(f"✅ Token endpoint verified (token length: {len(resp['access_token'])})")
    print(f"\nDiscovery URL: {base_url}/realms/{realm}/.well-known/openid-configuration")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Configure Keycloak for AgentCore")
    parser.add_argument("--url", required=True, help="Keycloak base URL (e.g., http://localhost:8080)")
    parser.add_argument("--password", required=True, help="Keycloak admin password")
    parser.add_argument("--realm", default="orion", help="Realm name (default: orion)")
    parser.add_argument("--client-id", default="content-export-adapter", help="Client ID")
    parser.add_argument("--client-secret", default="test-secret-12345", help="Client secret")
    args = parser.parse_args()

    setup_keycloak(args.url, args.password, args.realm, args.client_id, args.client_secret)
