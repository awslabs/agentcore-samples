"""
Pay for Content — Browser Use Case (Amazon Bedrock AgentCore Runtime).

App-backend script that:
1. Provisions the Amazon Bedrock AgentCore payments resource stack (once per user):
   CredentialProvider → PaymentManager → PaymentConnector → EmbeddedCryptoWallet Instrument
2. Verifies wallet USDC balance
3. Creates a payment session with a spend limit
4. Enables Payment Manager observability (vended log delivery to CloudWatch)
5. Deploys the agent to AgentCore Runtime via the AgentCore CLI (Container build)
6. Invokes the deployed agent with the paywall URL and payment context
7. Verifies session spend

Usage:
    python pay_for_content_browser.py

Prerequisites:
    - bash setup_roles.sh        (creates the four IAM roles — once per account)
    - cp .env.sample .env        (fill in CDP credentials, role ARNs, CONTENT_DISTRIBUTION_URL)
    - npm install -g @aws/agentcore@0.30.0
    - AWS CDK v2 installed
    - Content provider deployed: cd content-provider && PAY_TO=0x<wallet> bash deploy.sh
      then set CONTENT_DISTRIBUTION_URL in .env to the printed CloudFront URL

Subsequent runs (skip provisioning):
    Set MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID, SESSION_ID in .env
    and the script will skip Steps 3 and 4 automatically.
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.session import Session
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
SESSION_ID = os.environ.get("SESSION_ID", "")

# Session config
USER_ID = os.environ.get("USER_ID", "test-user-12345")
SESSION_MAX_SPEND = os.environ.get("SESSION_MAX_SPEND", "1.00")
SESSION_EXPIRY_MINUTES = int(os.environ.get("SESSION_EXPIRY_MINUTES", "60"))

# Network / blockchain
# base-sepolia:  eip155:84532                              (default, e2e tested)
# solana-devnet: solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1  (placeholder, not yet tested)
NETWORK_ALIAS = os.environ.get("NETWORK", "base-sepolia")
NETWORK_MAP = {
    "base-sepolia": {
        "caip2": "eip155:84532",
        "botocore_net": "ETHEREUM",
        "usdc_address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    },
    "solana-devnet": {
        "caip2": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
        "botocore_net": "SOLANA",
        "usdc_address": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",  # pragma: allowlist secret
    },
    "base-mainnet": {
        "caip2": "eip155:8453",
        "botocore_net": "ETHEREUM",
        "usdc_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
}
if NETWORK_ALIAS not in NETWORK_MAP:
    raise ValueError(f"Unknown NETWORK '{NETWORK_ALIAS}'. Valid: {list(NETWORK_MAP)}")
ACTIVE_NETWORK = NETWORK_MAP[NETWORK_ALIAS]

# Content provider
CONTENT_DISTRIBUTION_URL = os.environ.get("CONTENT_DISTRIBUTION_URL", "")
PAYWALL_DEMO_URL = f"{CONTENT_DISTRIBUTION_URL}/article/paywall-demo"

# AgentCore Runtime deployment
AGENT_NAME = os.environ.get("AGENT_NAME", "PayForContentBrowserAgent")
PROJECT_NAME = os.environ.get("AGENT_PROJECT_NAME", "payforcontent")
RUNTIME_DIR = PROJECT_NAME

print(f"Region:        {REGION}")
print(f"CP:            {CP_ENDPOINT}")
print(f"DP:            {DP_ENDPOINT}")
print(f"Network:       {NETWORK_ALIAS} ({ACTIVE_NETWORK['caip2']})")
print(f"Content URL:   {CONTENT_DISTRIBUTION_URL}")
print(f"Payment limit: ${SESSION_MAX_SPEND} USD")
if MANAGER_ARN:
    print(f"Manager ARN:   {MANAGER_ARN} (loaded from .env — Step 3 will be skipped)")
if SESSION_ID:
    print(f"Session ID:    {SESSION_ID} (loaded from .env — Step 4 will be skipped)")


# ── Step 2 — AWS Clients ───────────────────────────────────────────────────────
print("\n== Step 2: Initialize AWS Clients ==")

base_session = Session(region_name=REGION)
sts = base_session.client("sts")
ACCOUNT_ID = sts.get_caller_identity()["Account"]
print(f"AWS account: {ACCOUNT_ID}")


def assume_role(role_arn: str, session_name: str) -> Session:
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]
    sess = Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=REGION,
    )
    assumed_arn = sess.client("sts").get_caller_identity()["Arn"]
    print(f"  → {assumed_arn}")
    return sess


print("Assuming ControlPlaneRole...")
cp_session = assume_role(CONTROL_PLANE_ROLE_ARN, f"cp-setup-{int(datetime.now(timezone.utc).timestamp())}")
cp_client = cp_session.client("bedrock-agentcore-control", endpoint_url=CP_ENDPOINT)
print("CP client ready")

print("Assuming ManagementRole...")
mgmt_session = assume_role(MANAGEMENT_ROLE_ARN, f"payments-mgmt-{int(datetime.now(timezone.utc).timestamp())}")
mgmt_client = mgmt_session.client("bedrock-agentcore", endpoint_url=DP_ENDPOINT)
print("Management client ready")


# ── Step 3 — Provision Embedded Wallet Resources ──────────────────────────────
if MANAGER_ARN and PAYMENT_CONNECTOR_ID and PAYMENT_INSTRUMENT_ID:
    print("\n== Step 3: Skipped (MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID in .env) ==")
    CREDENTIAL_PROVIDER_ARN = None
    WALLET_HUB_URL = ""
else:
    print("\n== Step 3: Provision Embedded Wallet Resources ==")

    # 3a. Create Credential Provider
    # For StripePrivy: credentialProviderVendor="StripePrivy",
    # replace coinbaseCdpConfiguration with stripePlatformConfiguration.
    # IMPORTANT (Coinbase CDP): Delegated Signing must be enabled in your CDP project
    # before ProcessPayment will succeed. Go to portal.cdp.coinbase.com → your project
    # → Wallet → Embedded Wallets → Policies → enable Delegated signing.
    cred_resp = cp_client.create_payment_credential_provider(
        name=f"CoinbaseCdp{int(time.time())}",
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
    print(f"Credential Provider: {CREDENTIAL_PROVIDER_ARN}")

    # 3b. Create Payment Manager
    mgr_resp = cp_client.create_payment_manager(
        name=f"PayMgr{int(time.time())}",
        description="AgentCore payments - Pay for Content Browser use case",
        authorizerType="AWS_IAM",
        roleArn=RESOURCE_RETRIEVAL_ROLE_ARN,
        clientToken=str(uuid.uuid4()),
    )
    MANAGER_ARN = mgr_resp["paymentManagerArn"]
    MANAGER_ID = mgr_resp["paymentManagerId"]
    print(f"Payment Manager ARN: {MANAGER_ARN}")
    print(f"Manager ID:          {MANAGER_ID}")

    # 3c. Create Payment Connector
    conn_resp = cp_client.create_payment_connector(
        paymentManagerId=MANAGER_ID,
        name=f"CoinbaseConn{int(time.time())}",
        description="Coinbase CDP connector for embedded wallet",
        type="CoinbaseCDP",
        credentialProviderConfigurations=[{"coinbaseCDP": {"credentialProviderArn": CREDENTIAL_PROVIDER_ARN}}],
        clientToken=str(uuid.uuid4()),
    )
    PAYMENT_CONNECTOR_ID = conn_resp["paymentConnectorId"]
    print(f"Payment Connector ID: {PAYMENT_CONNECTOR_ID}")

    # 3d. Create Embedded Crypto Wallet Instrument
    # EMBEDDED_CRYPTO_WALLET: AgentCore provisions the wallet — no pre-existing CDP
    # wallet needed. The linkedAccounts email ties the wallet to a user identity.
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
    WALLET_HUB_URL = wallet_details.get("redirectUrl", "")

    print(f"Payment Instrument ID: {PAYMENT_INSTRUMENT_ID}")
    print(f"Wallet Address:        {wallet_address}")
    print(f"Network:               {ACTIVE_NETWORK['caip2']}")
    if WALLET_HUB_URL:
        print(f"WalletHub URL:         {WALLET_HUB_URL}")
    print()
    print("Save these values to .env for future runs:")
    print(f"  MANAGER_ARN={MANAGER_ARN}")
    print(f"  PAYMENT_CONNECTOR_ID={PAYMENT_CONNECTOR_ID}")
    print(f"  PAYMENT_INSTRUMENT_ID={PAYMENT_INSTRUMENT_ID}")

    # WalletHub — fund wallet and grant signing permission
    print()
    print("ACTION REQUIRED: Complete wallet setup before continuing.")
    print("  1. Open the WalletHub URL printed above.")
    print("     Log in with your WALLET_EMAIL and click 'Grant signing permission'.")
    print("  2. Fund the wallet with testnet USDC:")
    print("     - Base Sepolia: https://faucet.circle.com → select Base Sepolia → paste wallet address")
    print(f"     - Wallet address: {wallet_address}")
    print("  3. After funding and granting permission, re-run this script.")
    print("     Set MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID in .env to skip provisioning.")
    raise SystemExit(0)


# ── Step 3e — Verify Wallet Balance ───────────────────────────────────────────
print("\n== Step 3e: Verify Wallet Balance ==")

# Briefly assume ProcessPaymentRole to call GetPaymentInstrumentBalance.
# The deployed agent on Runtime uses this same role automatically.
print("Assuming ProcessPaymentRole for balance check...")
balance_check_session = assume_role(
    PROCESS_PAYMENT_ROLE_ARN, f"balance-check-{int(datetime.now(timezone.utc).timestamp())}"
)
balance_check_client = balance_check_session.client("bedrock-agentcore", endpoint_url=DP_ENDPOINT)

try:
    balance_resp = balance_check_client.get_payment_instrument_balance(
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=PAYMENT_CONNECTOR_ID,
        paymentInstrumentId=PAYMENT_INSTRUMENT_ID,
        userId=USER_ID,
        chain="BASE_SEPOLIA",
        token="USDC",
    )
    token_balance = balance_resp.get("tokenBalance", {})
    if token_balance:
        amount_units = int(token_balance.get("amount", 0))
        decimals = token_balance.get("decimals", 6)
        readable = amount_units / (10**decimals)
        print(
            f"Wallet balance: {readable:.6f} {token_balance.get('token', 'USDC')} on {token_balance.get('chain', 'unknown')}"
        )
    else:
        print("Balance returned empty — faucet may still be pending. Continue if wallet is funded.")
    print(f"Instrument ID: {PAYMENT_INSTRUMENT_ID}")
except Exception as e:  # noqa: BLE001 - sample continues if the balance probe fails
    print(f"GetPaymentInstrumentBalance failed: {e}")
    print("Verify bedrock-agentcore:GetPaymentInstrumentBalance is in the ProcessPaymentRole policy.")
    print("Continue to Step 4 if the wallet is funded.")


# ── Step 4 — Create Payment Session ───────────────────────────────────────────
if SESSION_ID:
    print(f"\n== Step 4: Skipped (SESSION_ID={SESSION_ID} in .env) ==")
else:
    print("\n== Step 4: Create Payment Session ==")

    session_response = mgmt_client.create_payment_session(
        paymentManagerArn=MANAGER_ARN,
        userId=USER_ID,
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
        limits={
            "maxSpendAmount": {
                "value": SESSION_MAX_SPEND,
                "currency": "USD",
            }
        },
        clientToken=str(uuid.uuid4()),
    )
    payment_session = session_response["paymentSession"]
    SESSION_ID = payment_session["paymentSessionId"]

    print(f"Session ID:      {SESSION_ID}")
    print(f"Payment limit:   ${SESSION_MAX_SPEND} USD")
    print(f"Expires:         {SESSION_EXPIRY_MINUTES} minutes from now")
    if "availableLimits" in payment_session:
        available = payment_session["availableLimits"]["availableSpendAmount"]
        print(f"Available:       {available['value']} {available['currency']}")


# ── Step 4b — Enable Payment Manager Observability ────────────────────────────
print("\n== Step 4b: Enable Payment Manager Observability ==")

# Opt-in vended log delivery per the AgentCore Payments observability doc.
# Makes the Payment Manager show up in AgentCore Observability → Payments dashboard
# with sessions, transactions, and per-API metrics (idempotent).
logs = boto3.client("logs", region_name=REGION)
manager_suffix = MANAGER_ARN.split("/")[-1].split("-")[0]
LG_NAME = f"/aws/vendedlogs/bedrock-agentcore/{manager_suffix}"
LG_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:{LG_NAME}"

# Step 0 — log group
try:
    logs.create_log_group(logGroupName=LG_NAME)
    logs.put_retention_policy(logGroupName=LG_NAME, retentionInDays=30)
    print(f"Created log group {LG_NAME}")
except logs.exceptions.ResourceAlreadyExistsException:
    print(f"Log group exists: {LG_NAME}")

# Step 1 — application logs delivery source
try:
    logs.put_delivery_source(
        name="payforcontent-payments-logs",
        resourceArn=MANAGER_ARN,
        logType="APPLICATION_LOGS",
    )
    print("Logs delivery source created")
except logs.exceptions.ConflictException:
    print("Logs delivery source exists")

# Step 2 — traces delivery source
try:
    logs.put_delivery_source(
        name="payforcontent-payments-traces",
        resourceArn=MANAGER_ARN,
        logType="TRACES",
    )
    print("Traces delivery source created")
except logs.exceptions.ConflictException:
    print("Traces delivery source exists")

# Step 3a — CloudWatch Logs delivery destination
try:
    logs.put_delivery_destination(
        name="payforcontent-payments-logs-dest",
        deliveryDestinationType="CWL",
        deliveryDestinationConfiguration={"destinationResourceArn": LG_ARN},
    )
    print("Logs delivery destination created")
except logs.exceptions.ConflictException:
    print("Logs delivery destination exists")

# Step 3b — X-Ray traces destination
try:
    logs.put_delivery_destination(
        name="payforcontent-payments-traces-dest",
        deliveryDestinationType="XRAY",
    )
    print("Traces delivery destination created")
except logs.exceptions.ConflictException:
    print("Traces delivery destination exists")

# Step 4a — wire logs source → destination
try:
    logs.create_delivery(
        deliverySourceName="payforcontent-payments-logs",
        deliveryDestinationArn=f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:delivery-destination:payforcontent-payments-logs-dest",
    )
    print("Logs delivery created")
except logs.exceptions.ConflictException:
    print("Logs delivery exists")

# Step 4b — wire traces source → destination
try:
    logs.create_delivery(
        deliverySourceName="payforcontent-payments-traces",
        deliveryDestinationArn=f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:delivery-destination:payforcontent-payments-traces-dest",
    )
    print("Traces delivery created")
except logs.exceptions.ConflictException:
    print("Traces delivery exists")

print("Payment Manager observability enabled.")
print("After first invoke, AgentCore Observability → Payments will populate.")


# ── Step 5 — Deploy Agent to AgentCore Runtime ─────────────────────────────────
print("\n== Step 5: Deploy Agent to AgentCore Runtime ==")


def run_cmd(cmd, cwd=None):
    """Run a CLI command and surface stdout/stderr on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, check=False)
    if result.returncode != 0:
        print("stdout:", result.stdout[-500:])
        print("stderr:", result.stderr[-500:])
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


# 5a. Scaffold the project (idempotent)
if not os.path.isdir(RUNTIME_DIR):
    print(f"Scaffolding {RUNTIME_DIR}/ ...")
    run_cmd(
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

    # Add the agent as a Container build
    run_cmd(
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
        cwd=RUNTIME_DIR,
    )

# 5b. Copy the agent code, requirements, and Dockerfile into the scaffold
# Container build: Playwright bundles a Node.js driver binary that needs its
# executable bit preserved — requires Container build, not CodeZip.
agent_dst = os.path.join(RUNTIME_DIR, "app", AGENT_NAME)
os.makedirs(agent_dst, exist_ok=True)
shutil.copy("agent/payment_agent.py", os.path.join(agent_dst, "main.py"))
shutil.copy("agent/requirements.txt", os.path.join(agent_dst, "requirements.txt"))
shutil.copy("agent/Dockerfile", os.path.join(agent_dst, "Dockerfile"))
print(f"Copied agent + Dockerfile into {agent_dst}/")

# 5c. Pin executionRoleArn + Python 3.13 in the runtime config
# Python 3.13: Strands + anyio hits a weakref.NoneType bug on 3.14.
config_path = os.path.join(RUNTIME_DIR, "agentcore", "agentcore.json")
with open(config_path) as f:
    project_config = json.load(f)

found = False
for runtime in project_config.get("runtimes", []):
    if runtime.get("name") == AGENT_NAME:
        runtime["executionRoleArn"] = PROCESS_PAYMENT_ROLE_ARN
        runtime["runtimeVersion"] = "PYTHON_3_13"
        found = True
        break
if not found:
    raise RuntimeError(f"Could not find runtime '{AGENT_NAME}' in {config_path}")

with open(config_path, "w") as f:
    json.dump(project_config, f, indent=2)
print(f"executionRoleArn = {PROCESS_PAYMENT_ROLE_ARN}")
print("runtimeVersion   = PYTHON_3_13")

# 5d. Set the deployment target (account + region)
targets_path = os.path.join(RUNTIME_DIR, "agentcore", "aws-targets.json")
with open(targets_path, "w") as f:
    json.dump(
        [
            {
                "name": "default",
                "description": "Pay for Content (Browser Use) — Runtime deployment",
                "account": ACCOUNT_ID,
                "region": REGION,
            }
        ],
        f,
        indent=2,
    )
print(f"Deployment target: {ACCOUNT_ID} / {REGION}")

# 5e. Install CDK npm deps
cdk_dir = os.path.join(RUNTIME_DIR, "agentcore", "cdk")
if not os.path.isdir(os.path.join(cdk_dir, "node_modules")):
    print(f"Installing CDK npm deps in {cdk_dir}/ ...")
    run_cmd(["npm", "install", "--silent"], cwd=cdk_dir)
    print("CDK deps installed")

# 5f. Deploy — CodeBuild builds the Docker image, pushes to ECR, creates AgentRuntime
print("Deploying to AgentCore Runtime — this can take 5-10 minutes (CodeBuild)...")
run_cmd(["agentcore", "deploy", "--yes"], cwd=RUNTIME_DIR)
print("Agent deployed")

# Capture the deployed agent runtime ARN
status_proc = subprocess.run(
    ["agentcore", "status", "--type", "agent", "--json"],
    cwd=RUNTIME_DIR,
    capture_output=True,
    text=True,
    check=True,
)
# The agentcore CLI may append a non-JSON upgrade notice after the JSON
# document. Locate the first JSON value and let the decoder consume just that.
stdout = status_proc.stdout
start = next(
    (i for i, ch in enumerate(stdout) if ch in "{["),
    -1,
)
if start < 0:
    raise RuntimeError(f"Could not find JSON in agentcore status output:\n{stdout}")
status, _ = json.JSONDecoder().raw_decode(stdout[start:])
entries = status if isinstance(status, list) else status.get("resources", [])

AGENT_RUNTIME_ARN = None
for entry in entries:
    name = entry.get("name") or entry.get("agentName")
    if name == AGENT_NAME:
        AGENT_RUNTIME_ARN = (
            entry.get("identifier") or entry.get("agentRuntimeArn") or entry.get("runtimeArn") or entry.get("arn")
        )
        break

if not AGENT_RUNTIME_ARN:
    print("Raw status output:")
    print(json.dumps(status, indent=2))
    raise RuntimeError("Could not locate agent runtime ARN in status output")

print(f"Agent Runtime ARN: {AGENT_RUNTIME_ARN}")


# ── Step 6 — Invoke the Deployed Agent ────────────────────────────────────────
print("\n== Step 6: Invoke the Deployed Agent ==")

invoke_payload = {
    "prompt": (
        f"Please retrieve the premium article from {PAYWALL_DEMO_URL}. "
        f"Pay for it using x402 and give me a summary of what it contains."
    ),
    "paywall_url": PAYWALL_DEMO_URL,
    "payment_manager_arn": MANAGER_ARN,
    "user_id": USER_ID,
    "payment_session_id": SESSION_ID,
    "payment_instrument_id": PAYMENT_INSTRUMENT_ID,
}

response = mgmt_client.invoke_agent_runtime(
    agentRuntimeArn=AGENT_RUNTIME_ARN,
    payload=json.dumps(invoke_payload).encode("utf-8"),
    contentType="application/json",
    accept="application/json",
)

result_bytes = (
    response["response"].read() if hasattr(response.get("response"), "read") else response.get("response", b"")
)
result = json.loads(result_bytes.decode("utf-8")) if result_bytes else {}
print(result.get("response", result))

# Verify the payment was recorded
session_check = mgmt_client.get_payment_session(
    paymentManagerArn=MANAGER_ARN,
    paymentSessionId=SESSION_ID,
    userId=USER_ID,
)
session_data = session_check["paymentSession"]
print("\nSession verified:")
print(f"  Session ID:  {session_data['paymentSessionId']}")
if "availableLimits" in session_data:
    remaining = session_data["availableLimits"]["availableSpendAmount"]
    print(f"  Remaining:   {remaining['value']} {remaining['currency']}")

print()
print("Done. View the trace at: https://console.aws.amazon.com/cloudwatch/home#gen-ai-observability:agent-core")
print(f"Cleanup: cd {RUNTIME_DIR} && agentcore remove all -y")
