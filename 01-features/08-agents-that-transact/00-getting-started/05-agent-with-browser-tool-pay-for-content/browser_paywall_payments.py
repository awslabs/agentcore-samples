"""
Strands Agent with Browser Tool Accesses Paid Content

Pattern: browse_with_payment custom tool uses AgentCore Browser (BrowserClient) +
Playwright + PaymentManager.generate_payment_header() to handle x402 payments inside
the browser session — enabling payment retries that preserve session state.

Architecture:
    Strands Agent
      └── browse_with_payment tool
            │
            ├── 1. BrowserClient.start() → managed cloud Chromium
            ├── 2. Playwright connects to AgentCore Browser (WebSocket)
            ├── 3. page.goto(url) → response interceptor detects 402
            ├── 4. Extract x402 requirements from response
            ├── 5. PaymentManager.generate_payment_header() → signed proof
            ├── 6. page.route() injects proof header
            ├── 7. page.goto(url) retries → 200 + content
            └── 8. Return content to agent

Why a custom tool instead of the plugin?
    The AgentCorePaymentsPlugin handles 402 at the tool output level — works for APIs.
    For browser-rendered content, the 402 happens inside the browser session and the
    retry must happen in the same session (preserving cookies, auth tokens, DOM context).
    The tool must handle the payment flow internally.

NOTE: This pattern requires an x402-enabled endpoint returning HTTP 402.
The tutorial uses the Coinbase CDP discovery endpoint as a demonstration target.
A future use case sample will provide a deployable x402 paywall server.

Usage:
    python browser_paywall_payments.py

Prerequisites:
    - Tutorial 00 completed (.env exists with the payment manager + instrument)
    - Wallet funded with testnet USDC
    - pip install -r requirements.txt
    - python -m playwright install chromium
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_tutorial_env, print_summary

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

# ── Step 1: Load Config ───────────────────────────────────────────────────────
config = load_tutorial_env()
PAYMENT_MANAGER_ARN = config["payment_manager_arn"]
REGION = config["region"]
USER_ID = config["user_id"]

# load_tutorial_env resolves instrument_id to the configured provider
# (CREDENTIAL_PROVIDER_TYPE), so single- and multi-provider .env files both work.
INSTRUMENT_ID = config["instrument_id"]
PROVIDER = config.get("active_provider") or config.get("provider_type", "unknown")

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Per-user spending budget for the browser tool's payment session.
SESSION_BUDGET_USD = "1.00"
SESSION_EXPIRY_MINUTES = 60

print_summary(
    "Config",
    payment_manager_arn=PAYMENT_MANAGER_ARN,
    provider=PROVIDER,
    instrument_id=INSTRUMENT_ID,
)

# ── Step 2: Create Payment Session ────────────────────────────────────────────
from bedrock_agentcore.payments import PaymentManager  # noqa: E402

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

# Verify instrument is ACTIVE
instr = manager.get_payment_instrument(user_id=USER_ID, payment_instrument_id=INSTRUMENT_ID)
instr_status = instr.get("status", "UNKNOWN")
assert instr_status == "ACTIVE", f"Instrument is {instr_status} — fund and delegate in Tutorial 00/03 first"
print(f"Instrument {INSTRUMENT_ID} is {instr_status}")

# A spending session is per-user, so the SDK mints one here in application code,
# scoped to the user we serve and capped at SESSION_BUDGET_USD for SESSION_EXPIRY_MINUTES.
session = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": SESSION_BUDGET_USD, "currency": "USD"}},
    expiry_time_in_minutes=SESSION_EXPIRY_MINUTES,
)
SESSION_ID = session["paymentSessionId"]
print(f"Created payment session {SESSION_ID} (budget ${SESSION_BUDGET_USD}, {SESSION_EXPIRY_MINUTES} min)")

# ── Step 3-4: Build the browse_with_payment Tool ──────────────────────────────
from playwright.async_api import async_playwright  # noqa: E402
from strands import tool  # noqa: E402

from bedrock_agentcore.tools.browser_client import BrowserClient  # noqa: E402


def _format_result(status: int, content: str, paid: bool, url: str) -> str:
    """Render the tool result as a single text string for Strands."""
    header = f"URL: {url}\nHTTP status: {status}\nPaid: {paid}"
    return f"{header}\n\n{content[:5000]}"


@tool
def browse_with_payment(url: str) -> str:
    """Navigate to a URL using a managed cloud browser. If the endpoint returns
    402 Payment Required, automatically pay via AgentCore payments and retry.

    Uses AgentCore Browser (managed Chromium) + Playwright for navigation.
    Payment is signed via PaymentManager.generate_payment_header().

    Args:
        url: The URL to navigate to and retrieve content from.

    Returns:
        A text summary containing the URL, HTTP status, paid flag, and page content.
    """

    async def _browse():
        browser_client = BrowserClient(region=REGION)
        try:
            browser_client.start()
            ws_url, ws_headers = browser_client.generate_ws_headers()
            print("  Browser session started")

            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(
                    endpoint_url=ws_url,
                    headers=ws_headers,
                    timeout=30000,
                )
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()

                # First navigation
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status = response.status if response else 0
                print(f"  HTTP {status}")

                # If 402: extract requirements, pay, retry
                if status == 402:
                    print("  402 Payment Required — processing payment...")
                    body = await response.text()
                    resp_headers = await response.all_headers()

                    # Generate payment proof via AgentCore
                    payment_header = manager.generate_payment_header(
                        user_id=USER_ID,
                        payment_instrument_id=INSTRUMENT_ID,
                        payment_session_id=SESSION_ID,
                        payment_required_request={
                            "statusCode": 402,
                            "headers": resp_headers,
                            "body": body,
                        },
                    )
                    print("  Payment signed")

                    # Inject payment header only on main navigation request
                    async def add_payment_headers(route, request):
                        if request.is_navigation_request():
                            headers = {**request.headers, **payment_header}
                            await route.continue_(headers=headers)
                        else:
                            await route.continue_()

                    await page.route("**/*", add_payment_headers)
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    status = response.status if response else 0
                    print(f"  Retry: HTTP {status}")

                    if status == 200:
                        content = await page.inner_text("body")
                        await browser.close()
                        return _format_result(200, content, paid=True, url=url)
                    else:
                        await browser.close()
                        return _format_result(
                            status,
                            f"Payment retry failed with HTTP {status}",
                            paid=True,
                            url=url,
                        )

                # No payment needed
                content = await page.inner_text("body")
                await browser.close()
                return _format_result(status, content, paid=False, url=url)

        finally:
            browser_client.stop()
            print("  Browser session closed")

    return asyncio.run(_browse())


print("browse_with_payment tool created")
print("Uses: BrowserClient + Playwright + PaymentManager.generate_payment_header()")

# ── Step 5: Create the Agent ──────────────────────────────────────────────────
from strands import Agent  # noqa: E402
from strands.models import BedrockModel  # noqa: E402

SYSTEM_PROMPT = """You are a content retrieval agent with browser access and payment capabilities.

Use the browse_with_payment tool to navigate to URLs and retrieve content.
If a page requires payment, the tool handles it automatically.
Summarize the content you retrieve.
Always report what you paid and what content you received."""

agent = Agent(
    model=BedrockModel(model_id=MODEL_ID, streaming=True),
    tools=[browse_with_payment],
    system_prompt=SYSTEM_PROMPT,
)
print("Agent created with browse_with_payment tool")

# ── Step 6: Agent Browses a Paid Endpoint ────────────────────────────────────
print("\n── Step 6: Agent Browses Paid Endpoint ──")
TARGET_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=technology+trends&limit=3"

print(f"Target: {TARGET_URL}")
print("Budget: $1.00 USD")
print("Browser: AgentCore managed Chromium\n")

start = time.time()
result = agent(f"Browse to this URL and retrieve the content: {TARGET_URL}\nSummarize what you find.")
elapsed = time.time() - start

print(f"\nCompleted in {elapsed:.1f}s")
print(result.message)

# ── Step 7: Verify Session Spend ─────────────────────────────────────────────
print("\n── Step 7: Verify Session Spend ──")
session_info = manager.get_payment_session(
    user_id=USER_ID,
    payment_session_id=SESSION_ID,
)
print_summary(
    "Session Spend",
    session_id=SESSION_ID,
    available=session_info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
    budget_limit=session_info.get("limits", {}).get("maxSpendAmount", "N/A"),
)

print(
    f"\nView traces: https://{REGION}.console.aws.amazon.com/cloudwatch/home?"
    f"region={REGION}#gen-ai-observability/agent-core"
)
print("\nDone. Sessions expire automatically.")
print("Next: python ../06-research-agent-with-payment-memory/research_agent_with_memory.py")
