"""Main LangGraph agent for competitive intelligence gathering."""

import asyncio
import sys
from pathlib import Path
from typing import Dict, List, TypedDict, Annotated, Optional
from datetime import datetime

import langgraph.graph as lg_graph

StateGraph = lg_graph.StateGraph
END = lg_graph.END
from langchain_core.messages import HumanMessage
from langchain_aws import ChatBedrockConverse
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

sys.path.insert(0, str(Path(__file__).parent))
from utils.imports import setup_interactive_tools_import

paths = setup_interactive_tools_import()

from interactive_tools.browser_viewer import BrowserViewerServer

# Import tools
from config import AgentConfig
from browser_tools import BrowserTools
from analysis_tools import AnalysisTools
import boto3
from bedrock_agentcore._utils.endpoints import get_control_plane_endpoint


console = Console()


class CompetitiveIntelState(TypedDict):
    """State for the competitive intelligence agent."""

    messages: Annotated[List, "append"]
    competitors: List[Dict]
    current_competitor_index: int
    competitor_data: Dict
    analysis_results: Dict
    report: Optional[str]
    recording_path: Optional[str]
    error: Optional[str]
    total_screenshots: int
    discovered_apis: List[Dict]
    performance_metrics: Dict
    session_data: Optional[Dict]  # For session persistence
    parallel_mode: bool  # For parallel processing


class CompetitiveIntelligenceAgent:
    """LangGraph agent for competitive intelligence gathering."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.browser_tools = BrowserTools(config)
        self.analysis_tools = AnalysisTools(config)
        self.llm = None
        self.graph = None
        self.browser_viewer = None
        self.parallel_browser_sessions = []  # For parallel processing

    async def initialize(self, resume_session_id: Optional[str] = None):
        """Initialize the agent and its tools with optional session resume."""
        console.print(
            Panel(
                "[bold cyan]🎯 Competitive Intelligence Agent[/bold cyan]\n\n"
                "[bold]Powered by Amazon Bedrock AgentCore[/bold]\n\n"
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

        # Check if we're resuming a session
        if resume_session_id:
            console.print(f"[cyan]🔄 Resuming session: {resume_session_id}[/cyan]")
            session_data = await self.resume_session(resume_session_id)
            if session_data:
                console.print("[green]✅ Previous session data loaded[/green]")

        # Initialize browser with recording
        self.browser_tools.create_browser_with_recording()

        # Initialize LLM
        self.llm = ChatBedrockConverse(model_id=self.config.llm_model_id, region_name=self.config.region)
        console.print(f"✅ LLM initialized: {self.config.llm_model_id}")

        # Initialize browser session with CDP
        await self.browser_tools.initialize_browser_session(self.llm)

        # Initialize code interpreter
        self.analysis_tools.initialize()

        # Start browser live viewer
        if self.browser_tools.browser_client:
            console.print("\n[cyan]🖥️ Starting live browser viewer...[/cyan]")
            self.browser_viewer = BrowserViewerServer(
                self.browser_tools.browser_client, port=self.config.live_view_port
            )
            viewer_url = self.browser_viewer.start(open_browser=True)
            console.print(f"[green]✅ Live viewer: {viewer_url}[/green]")
            console.print("[dim]You can take/release control in the viewer[/dim]")

        # Build the graph
        self._build_graph()

        console.print("\n[green]✅ Agent initialized successfully![/green]")
        console.print(f"[cyan]📹 Recording to: {self.browser_tools.recording_path}[/cyan]")

    async def resume_session(self, session_id: str) -> Optional[Dict]:
        """Resume a previous analysis session using Code Interpreter persistence."""
        try:
            console.print("[cyan]📂 Loading previous session data...[/cyan]")

            # Use Code Interpreter to load session data
            session_data = self.analysis_tools.load_session_state(session_id)

            if session_data and session_data.get("status") == "success":
                return session_data.get("data")
            else:
                console.print("[yellow]⚠️ No previous session data found[/yellow]")
                return None

        except Exception as e:
            console.print(f"[yellow]⚠️ Could not resume session: {e}[/yellow]")
            return None

    def _build_graph(self):
        """Build the LangGraph workflow."""
        workflow = StateGraph(CompetitiveIntelState)

        # Add nodes
        workflow.add_node("analyze_competitor", self.analyze_competitor)
        workflow.add_node("intelligent_analysis", self.intelligent_multi_tool_analysis)
        workflow.add_node("process_data", self.process_data)
        workflow.add_node("generate_report", self.generate_report)

        # Set entry point
        workflow.set_entry_point("analyze_competitor")

        # Conditional edge to loop through competitors
        workflow.add_conditional_edges(
            "analyze_competitor",
            self.should_continue_analyzing,
            {
                "continue": "analyze_competitor",
                "analyze": "intelligent_analysis",
                "process": "process_data",
            },
        )

        workflow.add_edge("intelligent_analysis", "process_data")
        workflow.add_edge("process_data", "generate_report")
        workflow.add_edge("generate_report", END)

        self.graph = workflow.compile()

    async def analyze_competitor(self, state: CompetitiveIntelState) -> CompetitiveIntelState:
        """Analyze a single competitor with enhanced features."""
        competitors = state["competitors"]
        current_index = state.get("current_competitor_index", 0)

        if current_index >= len(competitors):
            return state

        competitor = competitors[current_index]
        console.print(
            f"\n[bold blue]🔍 Analyzing Competitor {current_index + 1}/{len(competitors)}: {competitor['name']}[/bold blue]"
        )

        # Progress tracking
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Analyzing {competitor['name']}...", total=10)

            competitor_data = {}

            try:
                # Step 1: Navigate
                progress.update(task, description="Navigating to website...", advance=1)
                nav_result = await self.browser_tools.navigate_to_url(competitor["url"])
                competitor_data["navigation"] = nav_result

                # Step 2: Take initial screenshot
                progress.update(task, description="Taking homepage screenshot...", advance=1)
                try:
                    await self.browser_tools.take_annotated_screenshot(f"{competitor['name']} - Homepage")
                except Exception as e:
                    console.print(f"[yellow]⚠️ Screenshot error: {e}[/yellow]")

                # Step 3: Intelligent discovery
                progress.update(task, description="Discovering page sections...", advance=1)
                try:
                    discovered_sections = await self.browser_tools.intelligent_scroll_and_discover()
                    competitor_data["discovered_sections"] = discovered_sections
                    console.print(f"[green]Found {len(discovered_sections)} key sections[/green]")
                except Exception as e:
                    console.print(f"[yellow]⚠️ Discovery error: {e}[/yellow]")
                    competitor_data["discovered_sections"] = []

                # Step 4: Try to navigate to pricing page
                progress.update(task, description="Looking for pricing page...", advance=1)
                try:
                    found_pricing = await self.browser_tools.smart_navigation("pricing")
                    if found_pricing:
                        await asyncio.sleep(3)  # Let page load
                        await self.browser_tools.take_annotated_screenshot(f"{competitor['name']} - Pricing Page")
                except Exception as e:
                    console.print(f"[yellow]⚠️ Navigation error: {e}[/yellow]")

                # Step 5: Advanced form interaction (NEW)
                progress.update(task, description="Checking for interactive elements...", advance=1)
                try:
                    form_data = await self.browser_tools.analyze_forms_and_inputs()
                    competitor_data["interactive_elements"] = form_data
                except Exception as e:
                    console.print(f"[yellow]⚠️ Form analysis error: {e}[/yellow]")

                # Step 6: Extract pricing
                progress.update(task, description="Extracting pricing information...", advance=1)
                try:
                    pricing_result = await self.browser_tools.extract_pricing_info()
                    competitor_data["pricing"] = pricing_result
                except Exception as e:
                    console.print(f"[yellow]⚠️ Pricing extraction error: {e}[/yellow]")
                    competitor_data["pricing"] = {"status": "error", "error": str(e)}

                # Step 7: Extract features
                progress.update(task, description="Extracting product features...", advance=1)
                try:
                    features_result = await self.browser_tools.extract_product_features()
                    competitor_data["features"] = features_result
                except Exception as e:
                    console.print(f"[yellow]⚠️ Feature extraction error: {e}[/yellow]")
                    competitor_data["features"] = {"status": "error", "error": str(e)}

                # Step 8: Multi-page workflow (NEW)
                progress.update(task, description="Exploring additional pages...", advance=1)
                try:
                    additional_pages = await self.browser_tools.explore_multi_page_workflow(
                        ["features", "docs", "api", "about"]
                    )
                    competitor_data["additional_pages"] = additional_pages
                except Exception as e:
                    console.print(f"[yellow]⚠️ Multi-page exploration error: {e}[/yellow]")

                # Step 9: Capture performance metrics
                progress.update(task, description="Capturing performance metrics...", advance=1)
                try:
                    metrics = await self.browser_tools.capture_performance_metrics()
                    competitor_data["performance_metrics"] = metrics
                except Exception as e:
                    console.print(f"[yellow]⚠️ Metrics error: {e}[/yellow]")

                # Step 10: Save session state (NEW)
                progress.update(task, description="Saving session state...", advance=1)
                try:
                    self.analysis_tools.save_session_state(f"competitor_{current_index}", competitor_data)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Session save error: {e}[/yellow]")

            except Exception as e:
                console.print(f"[red]❌ Critical error analyzing {competitor['name']}: {e}[/red]")
                competitor_data = {
                    "status": "error",
                    "error": str(e),
                    "url": competitor["url"],
                }

        # Store results
        all_competitor_data = state.get("competitor_data", {})
        all_competitor_data[competitor["name"]] = {
            "url": competitor["url"],
            "timestamp": datetime.now().isoformat(),
            **competitor_data,
            "apis_discovered": len(self.browser_tools._discovered_apis),
            "screenshots_taken": len(self.browser_tools._screenshots_taken),
            "status": "success" if competitor_data.get("navigation", {}).get("status") == "success" else "error",
        }

        # Analyze this competitor's data
        console.print(f"[cyan]📊 Running analysis for {competitor['name']}...[/cyan]")
        try:
            analysis_result = self.analysis_tools.analyze_competitor_data(
                competitor["name"], all_competitor_data[competitor["name"]]
            )
        except Exception as e:
            console.print(f"[yellow]⚠️ Analysis error: {e}[/yellow]")
            analysis_result = {"status": "error", "error": str(e)}

        console.print(f"[green]✅ Completed: {competitor['name']}[/green]")
        console.print(f"  • Discovered {len(competitor_data.get('discovered_sections', []))} sections")
        console.print(f"  • Found {len(self.browser_tools._discovered_apis)} API endpoints")
        console.print(f"  • Took {len(self.browser_tools._screenshots_taken)} screenshots")
        console.print(f"  • Explored {len(competitor_data.get('additional_pages', []))} additional pages")

        # Update state
        return {
            **state,
            "current_competitor_index": current_index + 1,
            "competitor_data": all_competitor_data,
            "total_screenshots": state.get("total_screenshots", 0) + len(self.browser_tools._screenshots_taken),
            "discovered_apis": state.get("discovered_apis", []) + self.browser_tools._discovered_apis,
            "messages": state["messages"] + [HumanMessage(content=f"Analyzed {competitor['name']}: {analysis_result}")],
        }

    async def intelligent_multi_tool_analysis(self, state: CompetitiveIntelState) -> CompetitiveIntelState:
        """NEW: Intelligent analysis that orchestrates browser and code interpreter together."""
        console.print("\n[bold cyan]🤖 Running Intelligent Multi-Tool Analysis...[/bold cyan]")

        competitor_data = state.get("competitor_data", {})

        # Step 1: Use Code Interpreter to analyze patterns
        console.print("[cyan]Step 1: Analyzing data patterns with Code Interpreter...[/cyan]")
        pattern_analysis = self.analysis_tools.analyze_pricing_patterns(competitor_data)

        # Step 2: Based on analysis, browser performs targeted actions
        if pattern_analysis.get("missing_data"):
            console.print("[cyan]Step 2: Browser collecting missing data points...[/cyan]")
            for competitor_name, missing_items in pattern_analysis["missing_data"].items():
                if "pricing_tiers" in missing_items:
                    # Browser goes back to find more detailed pricing
                    console.print(f"[yellow]Revisiting {competitor_name} for detailed pricing...[/yellow]")
                    # This would navigate back if needed

        # Step 3: Code Interpreter processes combined data
        console.print("[cyan]Step 3: Processing combined insights...[/cyan]")
        combined_insights = self.analysis_tools.generate_competitive_insights(competitor_data, pattern_analysis)

        # Step 4: Use AWS CLI in Code Interpreter to save results
        console.print("[cyan]Step 4: Using AWS CLI to archive results...[/cyan]")
        aws_result = self.analysis_tools.save_to_s3_with_aws_cli(
            combined_insights,
            self.config.s3_bucket,
            f"{self.config.s3_prefix}analysis/",
        )

        return {
            **state,
            "analysis_results": {
                **state.get("analysis_results", {}),
                "pattern_analysis": pattern_analysis,
                "combined_insights": combined_insights,
                "aws_storage": aws_result,
            },
        }

    async def analyze_competitors_parallel(self, competitors: List[Dict]) -> Dict:
        """Analyze multiple competitors in parallel with statistics."""
        console.print("\n[bold cyan]⚡ Starting Parallel Analysis Mode[/bold cyan]")
        console.print(f"Analyzing {len(competitors)} competitors simultaneously...")

        # Add timing for performance comparison
        start_time = datetime.now()

        # Create tasks for parallel execution
        tasks = []
        for i, competitor in enumerate(competitors):
            task = self._analyze_single_competitor_async(competitor, i)
            tasks.append(task)

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        all_competitor_data = {}
        total_apis = []
        total_screenshots = 0
        parallel_sessions = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                console.print(f"[red]Error analyzing {competitors[i]['name']}: {result}[/red]")
                all_competitor_data[competitors[i]["name"]] = {
                    "status": "error",
                    "error": str(result),
                }
            else:
                competitor_name = competitors[i]["name"]
                all_competitor_data[competitor_name] = result["data"]
                total_apis.extend(result.get("apis", []))
                total_screenshots += result.get("screenshots", 0)

                # Track session information for replaying
                if "browser_session_id" in result["data"] and result["data"]["browser_session_id"]:
                    parallel_sessions.append(
                        {
                            "name": competitor_name,
                            "session_id": result["data"]["browser_session_id"],
                            "recording_path": result["data"].get("recording_path"),
                        }
                    )

        # Calculate execution time
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        console.print("\n[green]✅ Parallel analysis complete![/green]")
        console.print(
            f"  • Successfully analyzed: {sum(1 for d in all_competitor_data.values() if d.get('status') != 'error')}/{len(competitors)}"
        )
        console.print(f"  • Total APIs discovered: {len(total_apis)}")
        console.print(f"  • Total screenshots: {total_screenshots}")
        console.print(f"  • Execution time: {duration:.2f} seconds")
        console.print(f"  • Average time per competitor: {duration / len(competitors):.2f} seconds")

        return {
            "competitor_data": all_competitor_data,
            "discovered_apis": total_apis,
            "total_screenshots": total_screenshots,
            "parallel_sessions": parallel_sessions,
            "execution_stats": {
                "total_duration": duration,
                "avg_duration_per_competitor": duration / len(competitors),
                "concurrent_sessions": len(competitors),
            },
        }

    async def _analyze_single_competitor_async(self, competitor: Dict, index: int) -> Dict:
        """Helper method for parallel competitor analysis."""
        console.print(f"[cyan]🔄 Starting parallel analysis for {competitor['name']}...[/cyan]")

        # Create a new browser session for this competitor
        browser_session = BrowserTools(self.config)
        browser_session.create_browser_with_recording()
        await browser_session.initialize_browser_session(self.llm)

        # Track the browser session for potential cleanup
        self.parallel_browser_sessions.append(browser_session)

        try:
            # Navigate and analyze
            await browser_session.navigate_to_url(competitor["url"])

            # Collect data
            pricing = await browser_session.extract_pricing_info()
            features = await browser_session.extract_product_features()
            sections = await browser_session.intelligent_scroll_and_discover()

            # Take screenshots
            await browser_session.take_annotated_screenshot(f"{competitor['name']} - Parallel Analysis")

            result = {
                "data": {
                    "url": competitor["url"],
                    "timestamp": datetime.now().isoformat(),
                    "pricing": pricing,
                    "features": features,
                    "sections": sections,
                    "status": "success",
                    "browser_session_id": browser_session.browser_client.session_id
                    if browser_session.browser_client
                    else None,
                    "recording_path": browser_session.recording_path,
                },
                "apis": browser_session._discovered_apis,
                "screenshots": len(browser_session._screenshots_taken),
            }

            console.print(f"[green]✅ Completed parallel analysis for {competitor['name']}[/green]")
            return result

        except Exception as e:
            console.print(f"[red]Error in parallel analysis for {competitor['name']}: {e}[/red]")
            raise e
        finally:
            # Cleanup the browser session
            await browser_session.cleanup()

    def should_continue_analyzing(self, state: CompetitiveIntelState) -> str:
        """Determine if we should continue to the next competitor."""
        current_index = state.get("current_competitor_index", 0)
        total_competitors = len(state["competitors"])

        if current_index < total_competitors:
            return "continue"
        elif state.get("competitor_data"):
            return "analyze"  # Go to intelligent analysis
        else:
            return "process"

    async def process_data(self, state: CompetitiveIntelState) -> CompetitiveIntelState:
        """Process all collected data and create visualizations."""
        console.print("\n[bold yellow]📊 Processing all competitor data...[/bold yellow]")

        competitor_data = state.get("competitor_data", {})

        # Create comparison visualization
        console.print("[cyan]Creating visualizations...[/cyan]")
        viz_result = self.analysis_tools.create_comparison_visualization(competitor_data)

        # Save final session state
        console.print("[cyan]Saving final session state...[/cyan]")
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create a safe copy of state for serialization
        serializable_state = {
            "competitor_data": competitor_data,
            "total_screenshots": state.get("total_screenshots", 0),
            "discovered_apis": state.get("discovered_apis", []),
            "timestamp": datetime.now().isoformat(),
            "parallel_mode": state.get("parallel_mode", False),
            # Don't include full messages to avoid serialization issues
            "message_count": len(state.get("messages", [])) if "messages" in state else 0,
        }

        # Save session state with serializable content
        self.analysis_tools.save_session_state(f"final_{session_id}", serializable_state)

        return {
            **state,
            "analysis_results": {
                "visualization": viz_result,
                "total_competitors": len(competitor_data),
                "successful_analyses": sum(1 for d in competitor_data.values() if d.get("status") == "success"),
                "total_apis_discovered": len(state.get("discovered_apis", [])),
                "session_id": session_id,
            },
        }

    async def generate_report(self, state: CompetitiveIntelState) -> CompetitiveIntelState:
        """Generate the final report."""
        console.print("\n[bold green]📄 Generating final report...[/bold green]")

        # Generate comprehensive report
        report_result = self.analysis_tools.generate_final_report(
            state.get("competitor_data", {}), state.get("analysis_results", {})
        )

        # Get recording path
        recording_path = self.browser_tools.recording_path

        # Summary panel
        console.print("\n")
        console.print(
            Panel(
                f"[bold green]✅ Analysis Complete![/bold green]\n\n"
                f"📊 Competitors analyzed: {len(state['competitors'])}\n"
                f"📸 Screenshots taken: {state.get('total_screenshots', 0)}\n"
                f"🔍 APIs discovered: {len(state.get('discovered_apis', []))}\n"
                f"📄 Report: {report_result.get('report_path', 'N/A')}\n"
                f"📹 Recording: {recording_path}\n"
                f"💾 Session ID: {state['analysis_results'].get('session_id', 'N/A')}\n"
                f"⚡ Mode: {'Parallel' if state.get('parallel_mode', False) else 'Sequential'}\n"
                + (
                    f"⏱️ Total execution: {state.get('execution_stats', {}).get('total_duration', 0):.2f}s"
                    if state.get("execution_stats")
                    else ""
                ),
                title="Summary",
                border_style="green",
            )
        )

        return {
            **state,
            "report": report_result.get("report_content", ""),
            "recording_path": recording_path,
            "messages": state["messages"]
            + [
                {
                    "type": "human",
                    "content": f"Report generated: {report_result.get('output', '')}",
                }
            ],
        }

    async def run(
        self,
        competitors: List[Dict],
        parallel: bool = False,
        force_parallel: bool = False,
    ) -> Dict:
        """Run the competitive intelligence analysis."""
        try:
            # For live view, we need to warn but allow forcing parallel mode
            if parallel and self.browser_viewer and len(competitors) > 1 and not force_parallel:
                console.print("[yellow]⚠️ Live viewing is active - parallel mode will disable live view[/yellow]")
                if not force_parallel:
                    console.print("[yellow]Switching to sequential mode to maintain visibility...[/yellow]")
                    parallel = False

            if parallel and len(competitors) > 1:
                # Use parallel mode for multiple competitors
                console.print("[bold cyan]Using parallel processing mode[/bold cyan]")
                if self.browser_viewer:
                    console.print("[yellow]⚠️ Live view will be limited during parallel execution[/yellow]")

                parallel_results = await self.analyze_competitors_parallel(competitors)

                # Create state with parallel results
                initial_state: CompetitiveIntelState = {
                    "messages": [
                        {
                            "type": "system",
                            "content": "Parallel competitive intelligence analysis",
                        }
                    ],
                    "competitors": competitors,
                    "current_competitor_index": len(competitors),
                    "competitor_data": parallel_results["competitor_data"],
                    "analysis_results": {},
                    "report": None,
                    "recording_path": self.browser_tools.recording_path,
                    "error": None,
                    "total_screenshots": parallel_results["total_screenshots"],
                    "discovered_apis": parallel_results["discovered_apis"],
                    "performance_metrics": {},
                    "session_data": None,
                    "parallel_mode": True,
                    "execution_stats": parallel_results.get("execution_stats", {}),
                }

                # Run only the processing and report generation
                final_state = await self.graph.ainvoke(initial_state)

            else:
                # Use sequential mode
                initial_state: CompetitiveIntelState = {
                    "messages": [
                        {
                            "type": "system",
                            "content": "Starting competitive intelligence analysis",
                        }
                    ],
                    "competitors": competitors,
                    "current_competitor_index": 0,
                    "competitor_data": {},
                    "analysis_results": {},
                    "report": None,
                    "recording_path": None,
                    "error": None,
                    "total_screenshots": 0,
                    "discovered_apis": [],
                    "performance_metrics": {},
                    "session_data": None,
                    "parallel_mode": False,
                }

                # Run the full graph
                console.print("\n[cyan]🚀 Starting analysis workflow...[/cyan]")
                final_state = await self.graph.ainvoke(initial_state)

            return {
                "success": True,
                "report": final_state["report"],
                "recording_path": final_state["recording_path"],
                "analysis_results": final_state["analysis_results"],
                "apis_discovered": final_state.get("discovered_apis", []),
                "session_id": final_state["analysis_results"].get("session_id"),
                "parallel_mode": final_state.get("parallel_mode", False),
                "parallel_sessions": parallel_results.get("parallel_sessions", []) if parallel else [],
                "execution_stats": final_state.get("execution_stats", {}),
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

        # Cleanup any parallel browser sessions
        for session in self.parallel_browser_sessions:
            try:
                await session.cleanup()
            except Exception:
                pass

        # Cleanup code interpreter
        self.analysis_tools.cleanup()

        console.print("[green]✅ Cleanup complete[/green]")

    async def cleanup_resources(state: CompetitiveIntelState):
        """Clean up all resources to prevent ongoing costs."""

        cleanup_report = {"browsers_closed": 0, "s3_objects_deleted": 0, "errors": []}

        try:
            # 1. Stop BedrockAgentCore browsers
            if "browser_tools" in state:
                browser_tools = state["browser_tools"]
                if browser_tools.browser_id:
                    control_client = boto3.client(
                        "bedrock-agentcore-control",
                        region_name=state["config"].region,
                        endpoint_url=get_control_plane_endpoint(state["config"].region),
                    )

                    try:
                        control_client.delete_browser(browserId=browser_tools.browser_id)
                        cleanup_report["browsers_closed"] += 1
                    except Exception as e:
                        cleanup_report["errors"].append(str(e))

            # 2. Clean up parallel browser sessions
            for session in state.get("parallel_browser_sessions", []):
                if session.browser_id:
                    try:
                        control_client.delete_browser(browserId=session.browser_id)
                        cleanup_report["browsers_closed"] += 1
                    except Exception:
                        pass

            # 3. Stop Code Interpreter
            if "analysis_tools" in state:
                try:
                    state["analysis_tools"].cleanup()
                except Exception:
                    pass

            # 4. Delete recordings if requested
            if state.get("delete_recordings"):
                s3_client = boto3.client("s3")
                recording_path = state.get("recording_path", "")

                if recording_path.startswith("s3://"):
                    parts = recording_path.replace("s3://", "").split("/", 1)
                    bucket = parts[0]
                    prefix = parts[1] if len(parts) > 1 else ""

                    try:
                        paginator = s3_client.get_paginator("list_objects_v2")
                        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

                        for page in pages:
                            if "Contents" in page:
                                for obj in page["Contents"]:
                                    s3_client.delete_object(Bucket=bucket, Key=obj["Key"])
                                    cleanup_report["s3_objects_deleted"] += 1
                    except Exception as e:
                        cleanup_report["errors"].append(f"S3: {str(e)}")

        except Exception as e:
            cleanup_report["errors"].append(str(e))

        return cleanup_report
