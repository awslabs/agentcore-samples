"""
Headless Browser Automation with Browser-Use SDK and AgentCore Browser Tool.

Demonstrates using the open-source Browser-Use SDK to drive an AgentCore
Browser session:
  1. Start a persistent BrowserClient session
  2. Create a BrowserProfile with auth headers and timeout
  3. Initialize the Browser-Use Agent with ChatAnthropicBedrock (Claude Haiku)
  4. Execute a natural language browser task
  5. Stop the session

Prerequisites:
    pip install -r ../requirements.txt
    # Patch browser_use to forward AgentCore auth headers:
    python patch_browser_use.py

IAM permissions required:
    bedrock-agentcore:StartBrowserSession
    bedrock-agentcore:StopBrowserSession
    bedrock-agentcore:ConnectBrowserAutomationStream

Usage:
    python getting_started.py
    python getting_started.py --task "Search for a coffee maker on amazon.com and extract details of the first one"
"""

import argparse
import asyncio
import os
import ssl
from contextlib import suppress

# macOS Python ships without system CA certs; wire in certifi so that
# WebSocket/TLS connections to AWS endpoints work out of the box.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    ssl._create_default_https_context = ssl.create_default_context
except ImportError:
    pass

from boto3.session import Session
from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm import ChatAnthropicBedrock
from rich.console import Console

from bedrock_agentcore.tools.browser_client import BrowserClient

console = Console()

DEFAULT_TASK = "Search for a coffee maker on amazon.com and extract details of the first one"


# ── Helpers ────────────────────────────────────────────────────────────────────


async def run_browser_task(browser_session: Browser, llm: ChatAnthropicBedrock, task: str) -> None:
    """Execute a natural language browser task using Browser-Use."""
    console.print(f"\n[bold blue]Executing task:[/bold blue] {task}")
    agent = Agent(task=task, llm=llm, browser_session=browser_session)
    with console.status("[bold green]Running browser automation...[/bold green]", spinner="dots"):
        await agent.run()
    console.print("[bold green]Task completed successfully![/bold green]")


# ── Main ───────────────────────────────────────────────────────────────────────


async def main_async(task: str, region: str) -> None:
    boto_session = Session()
    region = region or boto_session.region_name or "us-west-2"

    console.print("=" * 60)
    console.print("AgentCore Browser Tool — Browser-Use Getting Started")
    console.print("=" * 60)
    console.print(f"  Region: {region}")
    console.print(f"  Task:   {task}")

    client = BrowserClient(region)
    console.print("\n[cyan]Starting browser session...[/cyan]")
    client.start()
    ws_url, headers = client.generate_ws_headers()
    console.print("  Browser session ready.")

    browser_session = None
    try:
        browser_profile = BrowserProfile(
            headers=headers,
            timeout=150000,  # 150 seconds
        )
        browser_session = Browser(
            cdp_url=ws_url,
            browser_profile=browser_profile,
            keep_alive=True,
        )
        console.print("[cyan]Initializing browser session...[/cyan]")
        await browser_session.start()

        llm = ChatAnthropicBedrock(
            model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
            aws_region=region,
        )
        console.print("  Browser-Use agent ready.\n")

        await run_browser_task(browser_session, llm, task)

    except Exception as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        import traceback

        traceback.print_exc()
    finally:
        if browser_session:
            console.print("\n[yellow]Closing browser session...[/yellow]")
            with suppress(Exception):
                await browser_session.close()
            console.print("  Browser session closed.")
        client.stop()
        console.print("  AgentCore session stopped.")

    console.print("\n" + "=" * 60)
    console.print("Demo complete!")
    console.print("=" * 60)


def parse_args():
    parser = argparse.ArgumentParser(description="Browser-Use SDK demo with AgentCore Browser Tool")
    parser.add_argument(
        "--task",
        default=DEFAULT_TASK,
        help="Natural language browser task to execute",
    )
    parser.add_argument(
        "--region",
        default=Session().region_name or "us-west-2",
        help="AWS region",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    asyncio.run(main_async(args.task, args.region))


if __name__ == "__main__":
    main()
