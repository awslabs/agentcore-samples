#!/usr/bin/env python3
"""Run the Competitive Intelligence Agent with Strands Framework."""

import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Any

# Add parent directory to path
parent_dir = str(Path(__file__).parent.parent)
sys.path.append(parent_dir)


from utils.imports import setup_interactive_tools_import

paths = setup_interactive_tools_import()

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel

from shared.utils.s3_datasource import UnifiedS3DataSource

from config import AgentConfig
from agent import CompetitiveIntelligenceAgent
from interactive_tools.live_view_sessionreplay.session_replay_viewer import (
    SessionReplayViewer,
)

# from ..shared.utils.s3_datasource import UnifiedS3DataSource
# from ..shared.utils.imports import setup_interactive_tools_import

console = Console()


def get_bedrock_agentcore_single() -> List[Dict]:
    """Analyze AWS Bedrock AgentCore pricing."""
    return [
        {
            "name": "AWS Bedrock AgentCore",
            "url": "https://aws.amazon.com/bedrock/agentcore/pricing/",
        }
    ]


def get_bedrock_vs_vertex() -> List[Dict]:
    """Compare AWS Bedrock AgentCore with Google Vertex AI."""
    return [
        {
            "name": "AWS Bedrock AgentCore",
            "url": "https://aws.amazon.com/bedrock/agentcore/pricing/",
        },
        {
            "name": "Google Vertex AI",
            "url": "https://cloud.google.com/vertex-ai/pricing",
        },
    ]


def get_custom_competitors() -> List[Dict]:
    """Get custom competitors from user input with explicit analysis options."""
    competitors = []

    console.print("\n[bold]Enter competitors to analyze:[/bold]")
    console.print("[dim]Press Enter with empty name to finish[/dim]\n")

    while True:
        name = Prompt.ask("Competitor name", default="")
        if not name:
            break

        url = Prompt.ask(f"URL for {name}")

        # Let user specify what to analyze
        console.print("\n[cyan]What would you like to analyze?[/cyan]")
        console.print("1. Pricing information")
        console.print("2. Product features")
        console.print("3. API documentation")
        console.print("4. Company/About information")
        console.print("5. All of the above")

        analysis_choice = Prompt.ask("Select options (comma-separated, e.g., 1,2,3)", default="1,2")

        analyze = []
        if "1" in analysis_choice:
            analyze.extend(["pricing", "tiers"])
        if "2" in analysis_choice:
            analyze.extend(["features", "capabilities"])
        if "3" in analysis_choice:
            analyze.extend(["api", "docs", "developer"])
        if "4" in analysis_choice:
            analyze.extend(["about", "company", "team"])
        if "5" in analysis_choice:
            analyze = [
                "pricing",
                "tiers",
                "features",
                "capabilities",
                "api",
                "docs",
                "about",
                "company",
            ]

        # Ask for specific URLs (optional)
        additional_urls = {}
        if Confirm.ask("Do you have specific URLs for pricing/docs pages?", default=False):
            if "pricing" in analyze:
                pricing_url = Prompt.ask("Pricing page URL (optional)", default="")
                if pricing_url:
                    additional_urls["pricing_url"] = pricing_url
            if "api" in analyze or "docs" in analyze:
                docs_url = Prompt.ask("API/Docs URL (optional)", default="")
                if docs_url:
                    additional_urls["docs_url"] = docs_url

        competitors.append(
            {
                "name": name,
                "url": url,
                "analyze": analyze,
                "additional_urls": additional_urls,
                "auto_discover": True,
            }
        )

        console.print(f"[green]✓ Added {name} - will analyze: {', '.join(analyze)}[/green]\n")

    return competitors


def show_competitors_table(competitors: List[Dict]):
    """Display competitors in a table."""
    table = Table(title="Competitors to Analyze", title_style="bold cyan")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Name", style="magenta")
    table.add_column("URL", style="blue")

    for i, comp in enumerate(competitors, 1):
        table.add_row(
            str(i),
            comp["name"],
            comp["url"][:50] + "..." if len(comp["url"]) > 50 else comp["url"],
        )

    console.print(table)


async def view_replay(recording_config: Any, config: AgentConfig):
    """
    Start the session replay viewer using the recording configuration.

    Args:
        recording_config: Either a dict with S3Location or a string path
        config: Agent configuration
    """
    try:
        console.print("\n[cyan]🎭 Starting session replay viewer...[/cyan]")

        # Handle both structured config and legacy string format
        if isinstance(recording_config, dict):
            # New structured format from API
            if "s3Location" in recording_config:
                s3_location = recording_config["s3Location"]
                bucket = s3_location.get("bucket")
                prefix = s3_location.get("prefix", "").rstrip("/")
            else:
                # Direct dict with bucket and prefix
                bucket = recording_config.get("bucket")
                prefix = recording_config.get("prefix", "").rstrip("/")

            # Extract session ID from prefix
            prefix_parts = prefix.split("/")
            session_id = prefix_parts[-1] if prefix_parts else "unknown"

        elif isinstance(recording_config, str):
            # Legacy string format (s3://bucket/prefix/session_id/)
            console.print("[yellow]⚠️ Using legacy S3 path format[/yellow]")
            parts = recording_config.replace("s3://", "").rstrip("/").split("/")
            bucket = parts[0]
            prefix = "/".join(parts[1:-1]) if len(parts) > 2 else ""
            session_id = parts[-1] if len(parts) > 1 else "unknown"
        else:
            raise ValueError(f"Invalid recording configuration format: {type(recording_config)}")

        console.print(f"[dim]Bucket: {bucket}[/dim]")
        console.print(f"[dim]Prefix: {prefix}[/dim]")
        console.print(f"[dim]Session: {session_id}[/dim]")

        # Wait for recordings to be uploaded
        console.print("⏳ Waiting for recordings to be uploaded to S3 (30 seconds)...")
        await asyncio.sleep(30)

        # Use the unified S3 data source
        data_source = UnifiedS3DataSource(bucket=bucket, prefix=prefix, session_id=session_id)

        # Start replay viewer
        console.print(f"🎬 Starting session replay viewer for: {session_id}")
        viewer = SessionReplayViewer(data_source=data_source, port=config.replay_viewer_port)
        viewer.start()

    except Exception as e:
        console.print(f"[red]❌ Error starting replay viewer: {e}[/red]")
        import traceback

        traceback.print_exc()


async def main():
    """Main function to run the agent."""
    console.print(
        Panel(
            "[bold cyan]🎯 Competitive Intelligence Agent[/bold cyan]\n\n"
            "[bold]Powered by Strands Framework & Amazon Bedrock[/bold]\n\n"
            "Migration from LangGraph → Strands ✅\n\n"
            "All Features Preserved:\n"
            "• 🔍 Automated browser navigation with CDP\n"
            "• 📊 Intelligent content extraction with LLM\n"
            "• 📸 Screenshot capture with annotations\n"
            "• 📹 Full session recording to S3\n"
            "• 🎭 Session replay capability\n"
            "• 🤖 Claude 3.5 Sonnet for analysis\n"
            "• ⚡ Parallel processing support\n"
            "• 💾 Session persistence & resume\n"
            "• ☁️ AWS CLI integration\n"
            "• 📝 Advanced form analysis\n"
            "• 🌐 Multi-page workflows",
            title="Welcome - Strands Edition",
            border_style="blue",
        )
    )

    # Load configuration
    config = AgentConfig()

    # Validate configuration
    if not config.validate():
        console.print("[red]❌ Configuration validation failed[/red]")
        console.print("Please set the required environment variables")
        return

    # Show configuration
    console.print("\n[bold]Configuration:[/bold]")
    console.print(f"  Region: {config.region}")
    console.print(f"  Model: {config.llm_model_id}")
    console.print(f"  S3 Bucket: {config.s3_bucket}")
    console.print(f"  Role ARN: {config.recording_role_arn}")
    console.print()

    # Check for resume option
    resume_session = None
    if Confirm.ask("Do you want to resume a previous session?", default=False):
        resume_session = Prompt.ask("Enter session ID to resume")

    # Get competitors
    console.print("\n[bold]Select analysis option:[/bold]")
    console.print("1. 🎯 AWS Bedrock AgentCore Pricing Only")
    console.print("2. 🆚 Compare Bedrock AgentCore vs Vertex AI")
    console.print("3. ✏️  Custom competitors")

    choice = Prompt.ask("Select option", choices=["1", "2", "3"], default="1")

    if choice == "1":
        competitors = get_bedrock_agentcore_single()
    elif choice == "2":
        competitors = get_bedrock_vs_vertex()
    else:
        competitors = get_custom_competitors()
        if not competitors:
            console.print("[yellow]No competitors entered. Exiting.[/yellow]")
            return

    # Show competitors
    show_competitors_table(competitors)

    # Ask for processing mode
    parallel_mode = False
    if len(competitors) > 1:
        parallel_mode = Confirm.ask(
            f"\n⚡ Use parallel processing for {len(competitors)} competitors?",
            default=False,
        )

    if not Confirm.ask("\nProceed with analysis?", default=True):
        console.print("[yellow]Analysis cancelled.[/yellow]")
        return

    # Create and run agent
    agent = CompetitiveIntelligenceAgent(config)

    try:
        # Initialize with optional session resume
        await agent.initialize(resume_session_id=resume_session)

        # Show what to watch for
        watch_panel = Panel(
            "[bold yellow]👁️  Watch the Live Browser Viewer![/bold yellow]\n\n"
            "[bold]The browser will automatically:[/bold]\n"
            "• Navigate to each competitor's pricing page\n"
            "• Scroll through pages to discover content\n"
            "• Extract pricing information and features\n"
            "• Take annotated screenshots\n"
            "• Generate a comprehensive report\n\n"
            f"[bold]Mode:[/bold] {'⚡ Parallel' if parallel_mode else '🔄 Sequential'}\n\n"
            "[dim]Framework: Strands (migrated from LangGraph)[/dim]",
            border_style="yellow",
        )
        console.print(watch_panel)

        console.print("\n[cyan]Starting automated analysis in 5 seconds...[/cyan]")
        await asyncio.sleep(5)

        # Run analysis
        results = await agent.run(competitors, parallel=parallel_mode)

        if results["success"]:
            # Show results summary
            results_panel = Panel(
                f"[bold green]✅ Analysis Complete![/bold green]\n\n"
                f"[bold]Key Findings:[/bold]\n"
                f"📊 Competitors analyzed: {len(competitors)}\n"
                f"🌐 API endpoints discovered: {len(results.get('apis_discovered', []))}\n"
                f"📄 Report generated: Yes\n"
                f"📹 Session recorded: Yes\n"
                f"💾 Session ID: {results.get('session_id', 'N/A')}\n"
                f"⚡ Processing mode: {'Parallel' if parallel_mode else 'Sequential'}\n\n"
                f"[dim]Framework: Strands[/dim]",
                border_style="green",
            )
            console.print(results_panel)

            # Show report preview
            if results.get("report"):
                console.print("\n[bold]Report Preview:[/bold]")
                console.print("-" * 60)
                preview = results["report"][:1500]
                console.print(preview + "..." if len(results["report"]) > 1500 else preview)
                console.print("-" * 60)

            # Ask about replay
            # if results.get("recording_path"):
            #    if Confirm.ask("\nView session replay?", default=True):
            #        await view_replay(results["recording_path"], config)
            #
            if results.get("recording_config") or results.get("recording_path"):
                replay_prompt = Panel(
                    "[bold cyan]🎬 Session Recording Available![/bold cyan]\n\n"
                    "Your entire analysis session has been recorded.\n"
                    "You can replay it to:\n"
                    "• Review the extraction process\n"
                    "• Share findings with stakeholders\n"
                    "• Debug any issues\n"
                    "• Create training materials",
                    border_style="cyan",
                )
                console.print(replay_prompt)

                if Confirm.ask("\nView session replay?", default=True):
                    # Use recording_config if available, fallback to recording_path
                    recording_data = results.get("recording_config") or results.get("recording_path")
                    await view_replay(recording_data, config)
        else:
            console.print(f"\n[red]Analysis failed: {results.get('error', 'Unknown error')}[/red]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Analysis interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        import traceback

        traceback.print_exc()
    finally:
        await agent.cleanup()
        console.print("\n[green]✅ Agent shutdown complete[/green]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        import traceback

        traceback.print_exc()
