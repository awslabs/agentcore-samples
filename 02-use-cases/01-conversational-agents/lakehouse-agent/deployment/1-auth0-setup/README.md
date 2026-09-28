# Auth0 Setup for Lakehouse Agent

This directory contains scripts to configure Auth0 as the identity provider for the Lakehouse Agent.

## Prerequisites

### 1. Auth0 Tenant

You need an Auth0 tenant. A free Auth0 account works for development:
- Sign up at [auth0.com](https://auth0.com/signup)

### 2. Management API Authorization (One-Time Bootstrap Step)

**This is a one-time manual step that cannot be automated.**

The setup script needs Management API credentials to create resources in Auth0. You must authorize an M2M application for the Auth0 Management API before running the script.

#### Option A: Use the Default App (Simplest)

Every Auth0 tenant comes with a **Default App** pre-created. You can authorize it for the Management API:

1. Go to **Auth0 Dashboard** → **Applications** → **APIs**
2. Click on **Auth0 Management API**
3. Go to **Machine to Machine Applications** tab
4. Find **Default App** in the list and toggle it **ON** (Authorized)
5. Click the dropdown arrow and select **All** permissions
6. Click **Update**
7. Go to **Applications** → **Applications** → **Default App**
8. Copy the **Client ID** and **Client Secret**

#### Option B: Create a Dedicated M2M App

If you prefer a dedicated app for management operations:

1. Go to **Auth0 Dashboard** → **Applications** → **Applications**
2. Click **Create Application**
3. Enter name: `lakehouse-management-api` (or any name you prefer)
4. Select type: **Machine to Machine**
5. Click **Create**
6. On the **Authorize Machine to Machine Application** screen:
   - Select **Auth0 Management API** from the dropdown
   - Toggle **All** permissions (or select specific ones - see below)
   - Click **Authorize**
7. Go to the **Settings** tab of the new application
8. Copy the **Client ID** and **Client Secret**

#### Required Management API Permissions

If you don't want to grant all permissions, the minimum required are:

**Clients (Applications):**
- `read:clients`
- `create:clients`
- `update:clients`
- `delete:clients`

**Client Grants:**
- `read:client_grants`
- `create:client_grants`
- `update:client_grants`
- `delete:client_grants`

**Resource Servers (APIs):**
- `read:resource_servers`
- `create:resource_servers`
- `update:resource_servers`

**Roles:**
- `read:roles`
- `create:roles`
- `update:roles`
- `delete:roles`

**Users:**
- `read:users`
- `create:users`
- `update:users`

**Role Members:**
- `read:role_members`
- `create:role_members`

### 3. Configure Environment Variables

Add the Management API credentials to your `.env` file (in the `deployment/` directory):

```bash
AUTH0_DOMAIN=your-tenant.us.auth0.com    # Your Auth0 domain (no https://)
AUTH0_CLIENT_ID=<client-id-from-step-above>
AUTH0_CLIENT_SECRET=<client-secret-from-step-above>
```

### 4. AWS Credentials

AWS credentials configured for SSM Parameter Store writes.

## What the Setup Script Creates

Once the bootstrap credentials are configured, `setup_auth0.py` automates everything else:

1. **Regular Web Application** (`lakehouse-agent-app`) - for user login (authorization_code flow)
2. **Custom API** (`lakehouse-agent-api`) - defines the audience (`api://lakehouse-api`) and scopes
3. **M2M Application** (`lakehouse-obo-exchange-client`) - for OBO token exchange (RFC 8693)
4. **Roles** - policyholders, adjusters, administrators
5. **Test Users** - assigned to roles with default password `TempPass123!`
6. **User-Delegated Client Grant** - enables OBO between the exchange client and the API

## Usage

```bash
cd deployment/1-auth0-setup

# Ensure .env is configured with Management API credentials
# Run setup
../../.venv/bin/python setup_auth0.py
```

## SSM Parameters Created

- `/app/lakehouse-agent/auth0-domain`
- `/app/lakehouse-agent/auth0-app-client-id`
- `/app/lakehouse-agent/auth0-app-client-secret` (SecureString)
- `/app/lakehouse-agent/auth0-obo-client-id`
- `/app/lakehouse-agent/auth0-obo-client-secret` (SecureString)
- `/app/lakehouse-agent/auth0-resource-server-audience`
- `/app/lakehouse-agent/auth0-discovery-url`
- `/app/lakehouse-agent/auth0-user-<label>-sub` (one per test user)

## Test Users

| Email | Role |
|-------|------|
| policyholder001@example.com | policyholders |
| policyholder002@example.com | policyholders |
| adjuster001@example.com | adjusters |
| adjuster002@example.com | adjusters |
| admin@example.com | administrators |

Default password: `TempPass123!`

## Verification

```bash
../../.venv/bin/python verify_auth0_setup.py
```

## Cleanup

```bash
../../.venv/bin/python cleanup_auth0.py
```

> **Note:** Cleanup deletes the applications, API, roles, and users created by setup. It does NOT delete the Management API M2M application you created manually - you must delete that yourself if desired.

## Troubleshooting

### 401: Unauthorized

The Management API credentials in `.env` are invalid or the application was deleted.

**Solution:** Create a new Management API M2M application (see Prerequisites step 2) and update `.env`.

### 403: Client not authorized to access resource server

The M2M application exists but is not authorized for the Auth0 Management API.

**Solution:** 
1. Go to Auth0 Dashboard → Applications → APIs → Auth0 Management API
2. Go to Machine to Machine Applications tab
3. Find your application and toggle it ON
4. Grant the required permissions

### Duplicate Applications/APIs

The setup script is idempotent - it reuses existing resources by name. If you see duplicates, you may have created them manually with different names.

**Solution:** Delete the duplicates via Auth0 Dashboard, or run `cleanup_auth0.py` and re-run setup.
