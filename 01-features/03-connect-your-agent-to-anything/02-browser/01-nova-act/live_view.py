"""
Live View and CAPTCHA Handling with Nova Act and AgentCore Browser Tool.

Demonstrates browser automation with a live DCV viewer and CAPTCHA handling:
  1. Start a browser session
  2. Launch a local BrowserViewerServer (DCV-based) to watch the browser live
  3. Execute multi-step Nova Act tasks
  4. Detect and wait for human CAPTCHA resolution before continuing

Prerequisites:
    pip install -r ../requirements.txt
    The interactive_tools/ directory must be available (contains BrowserViewerServer).
    export NOVA_ACT_API_KEY=<your-nova-act-api-key>

Usage:
    # Live view (single prompt)
    python live_view.py \\
        --prompt "Search for AI news" \\
        --starting-page "https://www.google.com/" \\
        --nova-act-key $NOVA_ACT_API_KEY

    # Multi-step with CAPTCHA handling (pass JSON array of steps)
    python live_view.py \\
        --steps '["Search for AI news and press enter", "Extract the first result title"]' \\
        --starting-page "https://www.google.com/" \\
        --nova-act-key $NOVA_ACT_API_KEY \\
        --captcha
"""

import argparse
import json
import os
import sys
import time

from boto3.session import Session
from nova_act import NovaAct, BOOL_SCHEMA, ActAgentError
from rich.console import Console
from rich.panel import Panel

from bedrock_agentcore.tools.browser_client import browser_session

console = Console()

# Allow importing BrowserViewerServer from the interactive_tools sibling directory
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "interactive_tools")
if os.path.isdir(_TOOLS_DIR):
    sys.path.insert(0, _TOOLS_DIR)


# ── Helpers ────────────────────────────────────────────────────────────────────


def contains_human_validation_error(err) -> bool:
    """Recursively check whether an error represents a HumanValidationError."""
    if err is None:
        return False
    if isinstance(err, str) and "HumanValidationError" in err:
        return True
    if hasattr(err, "message"):
        return contains_human_validation_error(err.message)
    return "HumanValidationError" in str(err)


def run_steps_with_captcha(nova_act: NovaAct, steps: list) -> list:
    """Execute a list of steps, pausing for human CAPTCHA resolution when detected."""
    results = []
    for step_index, step in enumerate(steps):
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                console.print(f"\n[blue]Step {step_index + 1}/{len(steps)}:[/blue] {step}")
                result = nova_act.act(step)
                console.print(f"[bold green]Step {step_index + 1} result:[/bold green] {result}")
                results.append(result)
                break

            except ActAgentError as err:
                if contains_human_validation_error(err):
                    console.print("[yellow]CAPTCHA detected — please solve it in the browser.[/yellow]")
                    for attempt in range(8):
                        time.sleep(10)
                        try:
                            captcha_result = nova_act.act("Is there a captcha on the screen?", schema=BOOL_SCHEMA)
                            if captcha_result.matches_schema and not captcha_result.parsed_response:
                                console.print("[green]CAPTCHA solved, retrying step...[/green]")
                                break
                            console.print(f"[yellow]CAPTCHA still present ({attempt + 1}/8)[/yellow]")
                        except Exception:
                            time.sleep(5)
                    else:
                        console.print("[red]Max CAPTCHA wait reached, continuing anyway.[/red]")
                        retry_count += 1
                else:
                    console.print(f"[red]Error on step {step_index + 1}:[/red] {err}")
                    retry_count += 1
                    time.sleep(5)

            except Exception as exc:
                console.print(f"[red]Unexpected error on step {step_index + 1}:[/red] {exc}")
                retry_count += 1
                time.sleep(5)

        if retry_count >= max_retries:
            console.print(f"[bold red]Step {step_index + 1} failed after {max_retries} attempts.[/bold red]")
            results.append(None)

    return results


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Live-view browser demo with Nova Act and AgentCore Browser Tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt", help="Single natural language instruction")
    group.add_argument(
        "--steps",
        help='JSON array of steps, e.g. \'["Step 1", "Step 2"]\'',
    )
    parser.add_argument("--starting-page", required=True, help="Starting URL")
    parser.add_argument(
        "--nova-act-key",
        default=os.getenv("NOVA_ACT_API_KEY"),
        help="Nova Act API key (env: NOVA_ACT_API_KEY)",
    )
    parser.add_argument("--region", default=Session().region_name or "us-west-2")
    parser.add_argument(
        "--captcha",
        action="store_true",
        help="Enable CAPTCHA detection and human-resolution loop",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the live viewer server (default: 8000)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.nova_act_key:
        console.print("[red]ERROR:[/red] --nova-act-key is required (or set NOVA_ACT_API_KEY).")
        raise SystemExit(1)

    # Resolve steps list
    if args.steps:
        try:
            steps = json.loads(args.steps)
        except json.JSONDecodeError:
            steps = [s.strip() for s in args.steps.split(",")]
        if not isinstance(steps, list):
            steps = [steps]
    else:
        steps = [args.prompt]

    console.print(
        Panel(
            "[bold cyan]AgentCore Browser Tool — Live View Demo[/bold cyan]\n\n"
            f"Region:       {args.region}\n"
            f"Starting URL: {args.starting_page}\n"
            f"Steps:        {len(steps)}\n"
            f"CAPTCHA mode: {'enabled' if args.captcha else 'disabled'}",
            border_style="blue",
        )
    )

    viewer = None
    try:
        with browser_session(args.region) as client:
            ws_url, headers = client.generate_ws_headers()

            # Optionally start the live viewer
            try:
                from browser_viewer import BrowserViewerServer  # type: ignore

                viewer = BrowserViewerServer(client, port=args.port)
                viewer_url = viewer.start(open_browser=True)
                console.print(f"[green]Live viewer started:[/green] {viewer_url}")
            except ImportError:
                console.print(
                    "[yellow]BrowserViewerServer not available — running headless.[/yellow]\n"
                    "Copy interactive_tools/ from the source repository to enable live view."
                )

            with NovaAct(
                cdp_endpoint_url=ws_url,
                cdp_headers=headers,
                preview={"playwright_actuation": True},
                nova_act_api_key=args.nova_act_key,
                starting_page=args.starting_page,
            ) as nova_act:
                if args.captcha:
                    results = run_steps_with_captcha(nova_act, steps)
                else:
                    results = []
                    for i, step in enumerate(steps):
                        console.print(f"\n[blue]Step {i + 1}/{len(steps)}:[/blue] {step}")
                        result = nova_act.act(step)
                        console.print(f"[bold green]Result:[/bold green] {result}")
                        results.append(result)

    except Exception as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        import traceback

        traceback.print_exc()
    finally:
        console.print("\n[yellow]Shutting down...[/yellow]")

    console.print("\n" + "=" * 60)
    console.print("Demo complete!")
    console.print("=" * 60)


if __name__ == "__main__":
    main()
