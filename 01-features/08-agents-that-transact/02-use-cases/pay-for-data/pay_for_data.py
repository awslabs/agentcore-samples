"""
Pay for Data — Heurist Finance Agent (AgentCore Runtime).

App-backend script that:
1. Provisions the AgentCore payments resource stack (once per user):
   CredentialProvider → PaymentManager → PaymentConnector → EmbeddedCryptoWallet Instrument
2. Verifies wallet USDC balance
3. Creates a payment session with a spend limit
4. Enables Payment Manager observability (vended log delivery)
5. Syncs the Heurist tool catalog and deploys the agent to AgentCore Runtime
6. Invokes the deployed agent with a research prompt and payment context
7. Verifies session spend and prints artifact URLs

Usage:
    python pay_for_data.py

Prerequisites:
    - cp .env.sample .env        (fill in CDP credentials and IAM role ARNs)
    - npm install -g @aws/agentcore@0.30.0
    - AWS CDK v2 installed

Subsequent runs (skip provisioning):
    Set MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID in .env
    and the script will skip Step 3 automatically.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# ── Configuration ─────────────────────────────────────────────────────────────
load_dotenv(override=True)

REGION = os.environ.get("AWS_REGION", "us-west-2")

CP_ENDPOINT = os.environ.get(
    "CP_ENDPOINT",
    f"https://bedrock-agentcore-control.{REGION}.amazonaws.com",
)
DP_ENDPOINT = os.environ.get(
    "DP_ENDPOINT",
    f"https://bedrock-agentcore.{REGION}.amazonaws.com",
)

# Coinbase CDP credentials
CDP_API_KEY_NAME = os.environ["CDP_API_KEY_NAME"]
CDP_API_KEY_PRIVATE_KEY = os.environ["CDP_API_KEY_PRIVATE_KEY"]
CDP_WALLET_SECRET = os.environ["CDP_WALLET_SECRET"]

WALLET_EMAIL = os.environ.get("WALLET_EMAIL", "")

# IAM roles
MANAGEMENT_ROLE_ARN = os.environ["MANAGEMENT_ROLE_ARN"]
PROCESS_PAYMENT_ROLE_ARN = os.environ["PROCESS_PAYMENT_ROLE_ARN"]
CONTROL_PLANE_ROLE_ARN = os.environ["CONTROL_PLANE_ROLE_ARN"]
RESOURCE_RETRIEVAL_ROLE_ARN = os.environ["RESOURCE_RETRIEVAL_ROLE_ARN"]

# Provisioned resource IDs (populated by Step 3 — skip provisioning on re-runs)
MANAGER_ARN = os.environ.get("MANAGER_ARN", "")
PAYMENT_CONNECTOR_ID = os.environ.get("PAYMENT_CONNECTOR_ID", "")
PAYMENT_INSTRUMENT_ID = os.environ.get("PAYMENT_INSTRUMENT_ID", "")

# Session config
USER_ID = os.environ.get("USER_ID", "heurist-demo-user")
SESSION_MAX_SPEND = os.environ.get("SESSION_MAX_SPEND", "0.25")
SESSION_EXPIRY_MINUTES = int(os.environ.get("SESSION_EXPIRY_MINUTES", "60"))

# Network / blockchain
NETWORK_ALIAS = os.environ.get("NETWORK", "base-mainnet")
NETWORK_MAP = {
    "base-sepolia": {
        "caip2": "eip155:84532",
        "botocore_net": "ETHEREUM",
        "chain_enum": "BASE_SEPOLIA",
    },
    "base-mainnet": {
        "caip2": "eip155:8453",
        "botocore_net": "ETHEREUM",
        "chain_enum": "BASE_MAINNET",
    },
}
if NETWORK_ALIAS not in NETWORK_MAP:
    raise ValueError(f"Unknown NETWORK '{NETWORK_ALIAS}'. Valid: {list(NETWORK_MAP)}")
ACTIVE_NETWORK = NETWORK_MAP[NETWORK_ALIAS]

# Bedrock model
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Heurist catalog
HEURIST_AGENT_IDS = os.environ.get(
    "HEURIST_AGENT_IDS",
    "ExaSearchDigestAgent,YahooFinanceAgent,FredMacroAgent,SecEdgarAgent",
)

# AgentCore Runtime
AGENT_NAME = "HeuristFinanceAgent"
PROJECT_NAME = "payfordata"


# ── Helpers ───────────────────────────────────────────────────────────────────
def assume_role(role_arn: str, session_name: str) -> Session:
    """Assume an IAM role and return a boto3 Session."""
    base = Session(region_name=REGION)
    sts = base.client("sts")
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]
    sess = Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=REGION,
    )
    print(f"  → {sess.client('sts').get_caller_identity()['Arn']}")
    return sess


def run(cmd, **kw):
    """Run a subprocess command, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)
    if result.returncode != 0:
        print("stdout:", result.stdout[-500:])
        print("stderr:", result.stderr[-500:])
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


# ── Step 1: Initialize AWS clients ───────────────────────────────────────────
print("=" * 60)
print("Step 1: Initialize AWS clients")
print("=" * 60)

base_session = Session(region_name=REGION)
sts = base_session.client("sts")
ACCOUNT_ID = sts.get_caller_identity()["Account"]
print(f"Account: {ACCOUNT_ID}, Region: {REGION}, Network: {NETWORK_ALIAS}")

print("\nAssuming ControlPlaneRole...")
cp_session = assume_role(CONTROL_PLANE_ROLE_ARN, f"cp-setup-{int(datetime.now(timezone.utc).timestamp())}")
cp_client = cp_session.client("bedrock-agentcore-control", endpoint_url=CP_ENDPOINT)

print("Assuming ManagementRole...")
mgmt_session = assume_role(MANAGEMENT_ROLE_ARN, f"heurist-mgmt-{int(datetime.now(timezone.utc).timestamp())}")
mgmt_client = mgmt_session.client("bedrock-agentcore", endpoint_url=DP_ENDPOINT)
print("✅ Clients ready\n")


# ── Step 2: Create S3 artifacts bucket ────────────────────────────────────────
print("=" * 60)
print("Step 2: Create S3 artifacts bucket")
print("=" * 60)

ARTIFACTS_BUCKET = os.environ.get(
    "ARTIFACTS_BUCKET",
    f"heurist-finance-artifacts-{ACCOUNT_ID}-{REGION}",
)
s3 = boto3.client("s3", region_name=REGION)
try:
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=ARTIFACTS_BUCKET)
    else:
        s3.create_bucket(
            Bucket=ARTIFACTS_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    s3.put_public_access_block(
        Bucket=ARTIFACTS_BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print(f"✅ Created bucket: {ARTIFACTS_BUCKET}")
except s3.exceptions.BucketAlreadyOwnedByYou:
    print(f"  ↻ Bucket exists: {ARTIFACTS_BUCKET}")
print()


# ── Step 3: Provision embedded wallet resources ───────────────────────────────
print("=" * 60)
print("Step 3: Provision embedded wallet resources")
print("=" * 60)

if MANAGER_ARN and PAYMENT_CONNECTOR_ID and PAYMENT_INSTRUMENT_ID:
    print("  ↻ Reusing from .env:")
    print(f"    Manager:    {MANAGER_ARN}")
    print(f"    Connector:  {PAYMENT_CONNECTOR_ID}")
    print(f"    Instrument: {PAYMENT_INSTRUMENT_ID}")
else:
    # 3a. Credential Provider
    cred_resp = cp_client.create_payment_credential_provider(
        name=f"HeuristCdp{int(time.time())}",
        credentialProviderVendor="CoinbaseCDP",
        providerConfigurationInput={
            "coinbaseCdpConfiguration": {
                "apiKeyId": CDP_API_KEY_NAME,
                "apiKeySecret": CDP_API_KEY_PRIVATE_KEY,
                "walletSecret": CDP_WALLET_SECRET,
            }
        },
    )
    CREDENTIAL_PROVIDER_ARN = cred_resp["credentialProviderArn"]
    print(f"✅ Credential Provider: {CREDENTIAL_PROVIDER_ARN}")

    # 3b. Payment Manager
    mgr_resp = cp_client.create_payment_manager(
        name=f"HeuristPayMgr{int(time.time())}",
        description="AgentCore payments - Heurist Finance Agent",
        authorizerType="AWS_IAM",
        roleArn=RESOURCE_RETRIEVAL_ROLE_ARN,
        clientToken=str(uuid.uuid4()),
    )
    MANAGER_ARN = mgr_resp["paymentManagerArn"]
    print(f"✅ Payment Manager: {MANAGER_ARN}")

    # 3c. Payment Connector
    MANAGER_ID = MANAGER_ARN.split("/")[-1]
    conn_resp = cp_client.create_payment_connector(
        paymentManagerId=MANAGER_ID,
        name=f"HeuristConn{int(time.time())}",
        description="Coinbase CDP connector for Heurist Finance Agent",
        type="CoinbaseCDP",
        credentialProviderConfigurations=[{"coinbaseCDP": {"credentialProviderArn": CREDENTIAL_PROVIDER_ARN}}],
        clientToken=str(uuid.uuid4()),
    )
    PAYMENT_CONNECTOR_ID = conn_resp["paymentConnectorId"]
    print(f"✅ Payment Connector: {PAYMENT_CONNECTOR_ID}")

    # 3d. Embedded Crypto Wallet Instrument
    linked_accounts = []
    if WALLET_EMAIL:
        linked_accounts = [{"email": {"emailAddress": WALLET_EMAIL}}]

    inst_resp = mgmt_client.create_payment_instrument(
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=PAYMENT_CONNECTOR_ID,
        userId=USER_ID,
        paymentInstrumentType="EMBEDDED_CRYPTO_WALLET",
        paymentInstrumentDetails={
            "embeddedCryptoWallet": {
                "network": ACTIVE_NETWORK["botocore_net"],
                "linkedAccounts": linked_accounts,
            }
        },
        clientToken=str(uuid.uuid4()),
    )
    instrument = inst_resp["paymentInstrument"]
    PAYMENT_INSTRUMENT_ID = instrument["paymentInstrumentId"]
    wallet_details = instrument.get("paymentInstrumentDetails", {}).get("embeddedCryptoWallet", {})
    wallet_address = wallet_details.get("walletAddress", "<pending>")
    wallet_hub_url = wallet_details.get("redirectUrl", "")

    print(f"✅ Payment Instrument: {PAYMENT_INSTRUMENT_ID}")
    print(f"   Wallet: {wallet_address} on {ACTIVE_NETWORK['caip2']}")
    if wallet_hub_url:
        print(f"   WalletHub: {wallet_hub_url}")
    print("\n📋 Save to .env for future runs:")
    print(f"   MANAGER_ARN={MANAGER_ARN}")
    print(f"   PAYMENT_CONNECTOR_ID={PAYMENT_CONNECTOR_ID}")
    print(f"   PAYMENT_INSTRUMENT_ID={PAYMENT_INSTRUMENT_ID}")
    print("\n⚠️  Fund the wallet and grant signing delegation via WalletHub before continuing.")
    sys.exit(0)

print()


# ── Step 4: Verify wallet balance ─────────────────────────────────────────────
print("=" * 60)
print("Step 4: Verify wallet balance")
print("=" * 60)

print("Assuming ProcessPaymentRole for balance check...")
pp_session = assume_role(
    PROCESS_PAYMENT_ROLE_ARN,
    f"balance-check-{int(datetime.now(timezone.utc).timestamp())}",
)
pp_client = pp_session.client("bedrock-agentcore", endpoint_url=DP_ENDPOINT)

try:
    balance_resp = pp_client.get_payment_instrument_balance(
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=PAYMENT_CONNECTOR_ID,
        paymentInstrumentId=PAYMENT_INSTRUMENT_ID,
        userId=USER_ID,
        chain=ACTIVE_NETWORK["chain_enum"],
        token="USDC",
    )
    token_balance = balance_resp.get("tokenBalance", {})
    if token_balance:
        amount_units = int(token_balance.get("amount", 0))
        decimals = token_balance.get("decimals", 6)
        readable = amount_units / (10**decimals)
        print(f"✅ Balance: {readable:.6f} USDC on {token_balance.get('chain', ACTIVE_NETWORK['chain_enum'])}")
        if readable == 0:
            print("   ⚠️  Balance is 0 — fund the wallet before continuing.")
            sys.exit(1)
except Exception as e:  # noqa: BLE001 - sample continues if the balance probe fails
    print(f"⚠️  Balance check failed: {e}")
    print("   Continuing — ensure the wallet is funded.")
print()


# ── Step 5: Deploy to AgentCore Runtime ───────────────────────────────────────
print("=" * 60)
print("Step 5: Deploy to AgentCore Runtime")
print("=" * 60)

# 5a. Scaffold
if not os.path.isdir(PROJECT_NAME):
    print(f"Scaffolding {PROJECT_NAME}/ ...")
    run(
        [
            "agentcore",
            "create",
            "--name",
            AGENT_NAME,
            "--project-name",
            PROJECT_NAME,
            "--defaults",
            "--no-agent",
            "--skip-git",
            "--skip-python-setup",
            "--skip-install",
            "--json",
        ]
    )
    run(
        [
            "agentcore",
            "add",
            "agent",
            "--type",
            "byo",
            "--name",
            AGENT_NAME,
            "--build",
            "Container",
            "--language",
            "Python",
            "--framework",
            "Strands",
            "--model-provider",
            "Bedrock",
            "--code-location",
            f"app/{AGENT_NAME}",
            "--entrypoint",
            "main.py",
            "--network-mode",
            "PUBLIC",
            "--protocol",
            "HTTP",
            "--idle-timeout",
            "600",
            "--max-lifetime",
            "1800",
            "--json",
        ],
        cwd=PROJECT_NAME,
    )
else:
    print(f"  ↻ {PROJECT_NAME}/ exists — skipping scaffold")

# 5b. Stage agent code
build_ctx = f"{PROJECT_NAME}/app/{AGENT_NAME}"
os.makedirs(build_ctx, exist_ok=True)

# Sync catalog cache
print("Syncing Heurist catalog...")
sys.path.insert(0, "agent")
from catalog import fetch_live_catalog

HEURIST_CATALOG_URL = os.environ.get("HEURIST_CATALOG_URL", "https://mesh.heurist.xyz/x402/agents?details=true")
fetch_live_catalog(catalog_url=HEURIST_CATALOG_URL)
print("  ✅ Catalog synced")

for fname in (
    "main.py",
    "catalog.py",
    "config.py",
    "sync_registry.py",
    "catalog_live_cache.json",
    "requirements.txt",
    "Dockerfile",
):
    src = os.path.join("agent", fname)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(build_ctx, fname))

# 5c. Write container .env
runtime_env = f"""CI_ARTIFACTS_BUCKET={ARTIFACTS_BUCKET}
CI_ARTIFACTS_PREFIX=heurist-finance-artifacts
CI_ARTIFACTS_TTL=3600
AWS_REGION={REGION}
BEDROCK_MODEL_ID={BEDROCK_MODEL_ID}
AGENT_NAME={AGENT_NAME}
HEURIST_AGENT_IDS={HEURIST_AGENT_IDS}
BYPASS_TOOL_CONSENT=true
AGENT_MAX_TOKENS=32000
"""
with open(f"{build_ctx}/.env", "w") as f:
    f.write(runtime_env)

# 5d. Pin execution role and runtime version
config_path = os.path.join(PROJECT_NAME, "agentcore", "agentcore.json")
with open(config_path) as f:
    project_config = json.load(f)
for runtime in project_config.get("runtimes", []):
    if runtime.get("name") == AGENT_NAME:
        runtime["executionRoleArn"] = PROCESS_PAYMENT_ROLE_ARN
        runtime["runtimeVersion"] = "PYTHON_3_13"
        break
with open(config_path, "w") as f:
    json.dump(project_config, f, indent=2)

# 5e. Set deployment target
targets_path = os.path.join(PROJECT_NAME, "agentcore", "aws-targets.json")
with open(targets_path, "w") as f:
    json.dump(
        [
            {
                "name": "default",
                "description": "Heurist Finance Agent",
                "account": ACCOUNT_ID,
                "region": REGION,
            }
        ],
        f,
        indent=2,
    )

# 5f. Install CDK deps
cdk_dir = os.path.join(PROJECT_NAME, "agentcore", "cdk")
if os.path.isdir(cdk_dir) and not os.path.isdir(os.path.join(cdk_dir, "node_modules")):
    run(["npm", "install", "--silent"], cwd=cdk_dir)

# 5g. Deploy
print("Deploying (5–10 min on first run)...")
run(["agentcore", "deploy", "--yes"], cwd=PROJECT_NAME)
print("✅ Deployed")

# 5h. Capture runtime ARN
status_proc = subprocess.run(
    ["agentcore", "status", "--type", "agent", "--json"],
    cwd=PROJECT_NAME,
    capture_output=True,
    text=True,
    check=True,
)
status = json.loads(status_proc.stdout)
entries = status if isinstance(status, list) else status.get("resources", [])
AGENT_RUNTIME_ARN = None
for entry in entries:
    name = entry.get("name") or entry.get("agentName")
    if name == AGENT_NAME:
        AGENT_RUNTIME_ARN = entry.get("agentRuntimeArn") or entry.get("runtimeArn") or entry.get("arn")
        break
if not AGENT_RUNTIME_ARN:
    raise RuntimeError("Could not locate agent runtime ARN")
print(f"   ARN: {AGENT_RUNTIME_ARN}")
print()


# ── Step 6: Create payment session and invoke ─────────────────────────────────
print("=" * 60)
print("Step 6: Create payment session and invoke")
print("=" * 60)

session_response = mgmt_client.create_payment_session(
    paymentManagerArn=MANAGER_ARN,
    userId=USER_ID,
    expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    limits={"maxSpendAmount": {"value": SESSION_MAX_SPEND, "currency": "USD"}},
    clientToken=str(uuid.uuid4()),
)
SESSION_ID = session_response["paymentSession"]["paymentSessionId"]
print(f"✅ Session: {SESSION_ID} (budget: ${SESSION_MAX_SPEND})")

invoke_payload = {
    "prompt": (
        "Use FredMacroAgent to fetch the latest US GDP growth rate and unemployment rate. "
        "Use Code Interpreter to create a bar chart comparing them and a markdown summary. "
        "Save both as artifacts."
    ),
    "payment_manager_arn": MANAGER_ARN,
    "user_id": USER_ID,
    "payment_session_id": SESSION_ID,
    "payment_instrument_id": PAYMENT_INSTRUMENT_ID,
}

# Invoke with cold-start retry
invoke_client = mgmt_session.client(
    "bedrock-agentcore",
    endpoint_url=DP_ENDPOINT,
    config=Config(read_timeout=900, connect_timeout=10, retries={"max_attempts": 0}),
)

MAX_RETRIES = 3
result = None
for attempt in range(1, MAX_RETRIES + 1):
    print(f"\nInvoking {AGENT_NAME} (attempt {attempt}/{MAX_RETRIES})...")
    t0 = time.time()
    try:
        response = invoke_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            payload=json.dumps(invoke_payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        raw = response.get("response", b"")
        result_bytes = raw.read() if hasattr(raw, "read") else raw
        result = json.loads(result_bytes.decode("utf-8")) if result_bytes else {}
        print(f"✅ Response in {time.time() - t0:.0f}s")
        break
    except ClientError as e:
        msg = e.response.get("Error", {}).get("Message", "")
        if "initialization time exceeded" in msg.lower() and attempt < MAX_RETRIES:
            print("  ⏳ Container cold-starting — retrying in 15s...")
            time.sleep(15)
        else:
            raise

if not result:
    print("❌ All invoke attempts failed.")
    sys.exit(1)

print("\n── Response ──────────────────────────────────────────────────")
print(result.get("response", result))

artifacts = result.get("artifacts", [])
if artifacts:
    print("\n── Artifacts ─────────────────────────────────────────────────")
    for a in artifacts:
        print(f"  {a['name']}  (expires in {a['expires_in']}s)")
        print(f"  {a['url']}")
print()


# ── Step 7: Verify session spend ──────────────────────────────────────────────
print("=" * 60)
print("Step 7: Verify session spend")
print("=" * 60)

session_check = mgmt_client.get_payment_session(
    paymentManagerArn=MANAGER_ARN,
    paymentSessionId=SESSION_ID,
    userId=USER_ID,
)
session_data = session_check["paymentSession"]
budget = session_data.get("limits", {}).get("maxSpendAmount", {})
budget_val = float(budget.get("value", 0))
available = session_data.get("availableLimits", {}).get("availableSpendAmount", {})
avail_val = float(available.get("value", budget_val)) if available.get("value") else budget_val
spent = budget_val - avail_val

print(f"  Budget:    ${budget_val:.4f} {budget.get('currency', 'USD')}")
print(f"  Remaining: ${avail_val:.4f}")
print(f"  Spent:     ${spent:.4f} USD")
print("\n✅ Done.")
