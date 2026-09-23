"""Main Strands agent for competitive intelligence gathering."""

import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import nest_asyncio
import sys

sys.path.insert(0, str(Path(__file__).parent))

from utils.imports import setup_interactive_tools_import

paths = setup_interactive_tools_import()

from strands import Agent, tool
from strands.models import BedrockModel
from strands.session.s3_session_manager import S3SessionManager
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from interactive_tools.browser_viewer import BrowserViewerServer

# Import tools
from config import AgentConfig
from browser_tools import BrowserTools
from analysis_tools import AnalysisTools

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

console = Console()


class CompetitiveIntelligenceAgent:
    """Strands agent for competitive intelligence gathering."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.browser_tools = BrowserTools(config)
        self.analysis_tools = AnalysisTools(config)
        self.agent = None
        self.browser_viewer = None
        self.parallel_browser_sessions = []
        # Store the event loop
        self.loop = None

    def _safe_state_get(self, key: str, default: Any = None) -> Any:
        """Safely get state value with default."""
        try:
            value = self.agent.state.get(key)
            return value if value is not None else default
        except Exception:
            return default

    async def initialize(self, resume_session_id: Optional[str] = None):
        """Initialize the agent and its tools with optional session resume."""
        console.print(
            Panel(
                "[bold cyan]🎯 Competitive Intelligence Agent[/bold cyan]\n\n"
                "[bold]Powered by Amazon Bedrock and Strands Framework[/bold]\n\n"
                "Features:\n"
                "• 🌐 Automated browser navigation\n"
                "• 📊 Real-time API and network analysis\n"
                "• 🎯 Intelligent content extraction\n"
                "• 📸 Screenshot capture\n"
                "• 📹 Full session recording to S3\n"
                "• 🔄 Multi-tool orchestration\n"
                "• ⚡ Parallel processing support\n",
                title="Initializing",
                border_style="blue",
            )
        )

        # Store the current event loop
        self.loop = asyncio.get_event_loop()

        # Initialize browser with recording
        self.browser_tools.create_browser_with_recording()

        # Set up session manager for persistence
        session_manager = None
        if resume_session_id:
            console.print(f"[cyan]🔄 Resuming session: {resume_session_id}[/cyan]")
            session_manager = S3SessionManager(
                session_id=resume_session_id,
                bucket=self.config.s3_bucket,
                prefix=f"{self.config.s3_prefix}sessions/",
                region_name=self.config.region,
            )

        # Initialize Bedrock model
        bedrock_model = BedrockModel(model_id=self.config.llm_model_id, region_name=self.config.region)

        # Initialize browser session with CDP - IMPORTANT: Do this before creating agent
        await self.browser_tools.initialize_browser_session(bedrock_model)

        # Initialize code interpreter
        self.analysis_tools.initialize()

        # Create the main Strands agent with all tools
        self.agent = Agent(
            model=bedrock_model,
            system_prompt=self._get_system_prompt(),
            tools=self._create_agent_tools(),
            session_manager=session_manager,
            callback_handler=self._create_callback_handler(),
        )

        # Initialize state if starting fresh
        if not resume_session_id:
            self.agent.state.set("competitors", [])
            self.agent.state.set("current_competitor_index", 0)
            self.agent.state.set("competitor_data", {})
            self.agent.state.set("analysis_results", {})
            self.agent.state.set("total_screenshots", 0)
            self.agent.state.set("discovered_apis", [])
            self.agent.state.set("parallel_mode", False)
        else:
            console.print("[green]✅ Previous session data loaded[/green]")

        # Start browser live viewer
        if self.browser_tools.browser_client:
            console.print("\n[cyan]🖥️ Starting live browser viewer...[/cyan]")
            self.browser_viewer = BrowserViewerServer(
                self.browser_tools.browser_client, port=self.config.live_view_port
            )
            viewer_url = self.browser_viewer.start(open_browser=True)
            console.print(f"[green]✅ Live viewer: {viewer_url}[/green]")
            console.print("[dim]You can take/release control in the viewer[/dim]")

        console.print("\n[green]✅ Agent initialized successfully![/green]")
        console.print(f"[cyan]📹 Recording to: {self.browser_tools.recording_path}[/cyan]")

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        return """You are a competitive intelligence analysis agent. When asked to analyze competitors:
        1. Use the analyze_website tool for each competitor
        2. Use the perform_analysis tool to analyze the collected data
        3. Use the generate_report tool to create the final report
        
        Always use these tools in sequence to complete the analysis."""

    def _create_agent_tools(self) -> List:
        """Create all agent tools."""
        tools = []

        # Store reference to self for use in tools
        agent_instance = self

        @tool
        def analyze_website(competitor_name: str, competitor_url: str) -> str:
            """
            Analyze a competitor website to extract pricing, features, and other intelligence.

            Args:
                competitor_name: Name of the competitor company
                competitor_url: URL of the competitor website to analyze
            """
            # Use the existing event loop with run_until_complete
            if agent_instance.loop and agent_instance.loop.is_running():
                # We're already in an async context, create a task
                future = asyncio.ensure_future(
                    agent_instance._analyze_website_impl(competitor_name, competitor_url),
                    loop=agent_instance.loop,
                )

                # Use nest_asyncio to handle the nested loop
                return agent_instance.loop.run_until_complete(future)
            else:
                # No running loop, use asyncio.run
                return asyncio.run(agent_instance._analyze_website_impl(competitor_name, competitor_url))

        @tool
        def perform_analysis() -> str:
            """
            Analyze all collected competitor data to identify patterns and insights.
            """
            console.print("\n[bold yellow]📊 Analyzing all competitor data...[/bold yellow]")

            competitor_data = agent_instance._safe_state_get("competitor_data", {})

            if not competitor_data:
                return "No competitor data to analyze yet"

            # Analyze each competitor
            for competitor_name, data in competitor_data.items():
                console.print(f"[cyan]Analyzing {competitor_name}...[/cyan]")
                analysis_result = agent_instance.analysis_tools.analyze_competitor_data(competitor_name, data)

                # Store analysis results
                analysis_results = agent_instance._safe_state_get("analysis_results", {})
                analysis_results[competitor_name] = analysis_result
                agent_instance.agent.state.set("analysis_results", analysis_results)

            # Create visualizations
            console.print("[cyan]Creating comparison visualizations...[/cyan]")
            viz_result = agent_instance.analysis_tools.create_comparison_visualization(competitor_data)

            analysis_results = agent_instance._safe_state_get("analysis_results", {})
            analysis_results["visualizations"] = viz_result
            agent_instance.agent.state.set("analysis_results", analysis_results)

            return "Analysis completed successfully"

        @tool
        def generate_report() -> str:
            """
            Generate the final competitive intelligence report from analyzed data.
            """
            console.print("\n[bold green]📄 Generating final report...[/bold green]")

            competitor_data = agent_instance._safe_state_get("competitor_data", {})
            analysis_results = agent_instance._safe_state_get("analysis_results", {})

            if not competitor_data:
                return "No data to generate report from"

            # Generate report
            report_result = agent_instance.analysis_tools.generate_final_report(competitor_data, analysis_results)

            agent_instance.agent.state.set("report", report_result.get("report_content", ""))
            agent_instance.agent.state.set("recording_path", agent_instance.browser_tools.recording_path)

            return "Report generated successfully"

        # Add tools to list
        tools.extend([analyze_website, perform_analysis, generate_report])

        return tools

    async def _analyze_website_impl(self, competitor_name: str, competitor_url: str) -> str:
        """Implementation of website analysis."""
        console.print(f"\n[bold blue]🔍 Analyzing: {competitor_name}[/bold blue]")
        console.print(f"[cyan]URL: {competitor_url}[/cyan]")

        competitor_data = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Analyzing {competitor_name}...", total=10)

            try:
                # Navigate to website
                progress.update(task, description="Navigating to website...", advance=1)
                nav_result = await self.browser_tools.navigate_to_url(competitor_url)
                competitor_data["navigation"] = nav_result

                if nav_result.get("status") != "success":
                    console.print(f"[yellow]⚠️ Navigation failed: {nav_result.get('error')}[/yellow]")
                    # Continue anyway to try to get some data

                # Take screenshot
                progress.update(task, description="Taking homepage screenshot...", advance=1)
                await self.browser_tools.take_annotated_screenshot(f"{competitor_name} - Homepage")

                # Discover sections
                progress.update(task, description="Discovering page sections...", advance=1)
                discovered_sections = await self.browser_tools.intelligent_scroll_and_discover()
                competitor_data["discovered_sections"] = discovered_sections
                console.print(f"[green]Found {len(discovered_sections)} key sections[/green]")

                # Try to find pricing page
                progress.update(task, description="Looking for pricing page...", advance=1)
                found_pricing = await self.browser_tools.smart_navigation("pricing")
                if found_pricing:
                    await asyncio.sleep(3)
                    await self.browser_tools.take_annotated_screenshot(f"{competitor_name} - Pricing")

                # Analyze forms
                progress.update(task, description="Checking interactive elements...", advance=1)
                form_data = await self.browser_tools.analyze_forms_and_inputs()
                competitor_data["interactive_elements"] = form_data

                # Extract pricing
                progress.update(task, description="Extracting pricing...", advance=1)
                pricing_result = await self.browser_tools.extract_pricing_info()
                competitor_data["pricing"] = pricing_result

                # Extract features
                progress.update(task, description="Extracting features...", advance=1)
                features_result = await self.browser_tools.extract_product_features()
                competitor_data["features"] = features_result

                # Explore additional pages
                progress.update(task, description="Exploring additional pages...", advance=1)
                additional_pages = await self.browser_tools.explore_multi_page_workflow(
                    ["features", "docs", "api", "about"]
                )
                competitor_data["additional_pages"] = additional_pages

                # Capture metrics
                progress.update(task, description="Capturing metrics...", advance=1)
                metrics = await self.browser_tools.capture_performance_metrics()
                competitor_data["performance_metrics"] = metrics

                # Save to state
                progress.update(task, description="Saving data...", advance=1)
                all_competitor_data = self._safe_state_get("competitor_data", {})
                all_competitor_data[competitor_name] = {
                    "url": competitor_url,
                    "timestamp": datetime.now().isoformat(),
                    **competitor_data,
                    "status": "success",
                }
                self.agent.state.set("competitor_data", all_competitor_data)

                # Update metrics in state
                total_screenshots = self._safe_state_get("total_screenshots", 0)
                self.agent.state.set(
                    "total_screenshots",
                    total_screenshots + len(self.browser_tools._screenshots_taken),
                )

                discovered_apis = self._safe_state_get("discovered_apis", [])
                discovered_apis.extend(self.browser_tools._discovered_apis)
                self.agent.state.set("discovered_apis", discovered_apis)

            except Exception as e:
                console.print(f"[red]❌ Error analyzing {competitor_name}: {e}[/red]")
                import traceback

                traceback.print_exc()

                competitor_data = {"status": "error", "error": str(e)}

                all_competitor_data = self._safe_state_get("competitor_data", {})
                all_competitor_data[competitor_name] = competitor_data
                self.agent.state.set("competitor_data", all_competitor_data)

                return f"Error analyzing {competitor_name}: {str(e)}"

        console.print(f"[green]✅ Completed: {competitor_name}[/green]")
        return f"Successfully analyzed {competitor_name} - found {len(discovered_sections)} sections, extracted pricing and features"

    def _create_callback_handler(self):
        """Create a callback handler for progress tracking."""

        def callback_handler(**kwargs):
            # Track tool usage
            if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
                tool_name = kwargs["current_tool_use"]["name"]
                console.print(f"[cyan]🔧 Using tool: {tool_name}[/cyan]")

            # Show text output
            if "data" in kwargs:
                # Don't print full LLM reasoning, just tool calls
                pass

        return callback_handler

    async def run(self, competitors: List[Dict], parallel: bool = False) -> Dict:
        """Run the competitive intelligence analysis."""
        try:
            # Store competitors in state
            self.agent.state.set("competitors", competitors)

            console.print("\n[cyan]🤖 Starting competitive analysis workflow...[/cyan]")
            console.print(f"[bold]Analyzing {len(competitors)} competitors[/bold]")

            # Analyze each competitor sequentially
            for i, competitor in enumerate(competitors, 1):
                console.print(
                    f"\n[bold yellow]📊 Competitor {i}/{len(competitors)}: {competitor['name']}[/bold yellow]"
                )

                try:
                    # Directly invoke the tool
                    result = self.agent.tool.analyze_website(
                        competitor_name=competitor["name"],
                        competitor_url=competitor["url"],
                    )
                    console.print(f"[green]✓ {competitor['name']} analysis complete[/green]")
                    console.print(
                        f"[dim]Result: {result[:200]}...[/dim]" if len(result) > 200 else f"[dim]Result: {result}[/dim]"
                    )

                    # Add a small delay between competitors to avoid overwhelming
                    if i < len(competitors):
                        console.print("[dim]Waiting 2 seconds before next competitor...[/dim]")
                        await asyncio.sleep(2)

                except Exception as comp_error:
                    console.print(f"[red]❌ Error analyzing {competitor['name']}: {comp_error}[/red]")
                    # Continue with next competitor even if one fails
                    continue

            console.print("\n[bold cyan]All competitors analyzed, generating insights...[/bold cyan]")

            # Perform analysis
            console.print("\n[yellow]Running data analysis...[/yellow]")
            try:
                self.agent.tool.perform_analysis()
                console.print("[green]✓ Analysis complete[/green]")
            except Exception as e:
                console.print(f"[red]Analysis error: {e}[/red]")

            # Generate report
            console.print("\n[yellow]Generating report...[/yellow]")
            try:
                self.agent.tool.generate_report()
                console.print("[green]✓ Report generated[/green]")
            except Exception as e:
                console.print(f"[red]Report generation error: {e}[/red]")

            # Get final state
            report = self._safe_state_get("report")
            recording_path = self._safe_state_get("recording_path") or self.browser_tools.recording_path
            self._safe_state_get("analysis_results", {})
            apis_discovered = self._safe_state_get("discovered_apis", [])
            total_screenshots = self._safe_state_get("total_screenshots", 0)
            competitor_data = self._safe_state_get("competitor_data", {})

            # Display summary
            console.print("\n" + "=" * 60)
            console.print(
                Panel(
                    f"[bold green]✅ Analysis Complete![/bold green]\n\n"
                    f"📊 Competitors requested: {len(competitors)}\n"
                    f"✓ Successfully analyzed: {len([c for c in competitor_data.values() if c.get('status') == 'success'])}\n"
                    f"✗ Failed: {len([c for c in competitor_data.values() if c.get('status') == 'error'])}\n"
                    f"📸 Screenshots taken: {total_screenshots}\n"
                    f"🔍 APIs discovered: {len(apis_discovered)}\n"
                    f"📄 Report generated: {'Yes' if report else 'No'}\n"
                    f"📹 Recording: {recording_path}\n\n"
                    f"[bold]Analyzed:[/bold]\n"
                    + "\n".join(
                        [f"  • {name}: {data.get('status', 'unknown')}" for name, data in competitor_data.items()]
                    ),
                    title="Summary",
                    border_style="green",
                )
            )
            console.print("=" * 60)

            return {
                "success": True,
                "report": self._safe_state_get("report"),
                "recording_path": self.browser_tools.recording_path if self.browser_tools else None,
                "recording_config": self.browser_tools.recording_config if self.browser_tools else None,  # NEW
                "analysis_results": self._safe_state_get("analysis_results", {}),
                "apis_discovered": self._safe_state_get("discovered_apis", []),
                "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
                "parallel_mode": self._safe_state_get("parallel_mode", False),
            }

        except Exception as e:
            console.print(f"[red]❌ Agent error: {e}[/red]")
            import traceback

            traceback.print_exc()
            return {"success": False, "error": str(e)}

    async def cleanup(self):
        """Clean up agent resources."""
        console.print("\n[yellow]🧹 Cleaning up...[/yellow]")

        # Cleanup browser
        await self.browser_tools.cleanup()

        # Cleanup parallel sessions
        for session in self.parallel_browser_sessions:
            try:
                await session.cleanup()
            except Exception:
                pass

        # Cleanup code interpreter
        self.analysis_tools.cleanup()

        console.print("[green]✅ Cleanup complete[/green]")
