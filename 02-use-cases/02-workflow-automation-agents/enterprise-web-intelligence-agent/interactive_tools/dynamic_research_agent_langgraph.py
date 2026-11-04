"""
Dynamic Research Agent with Bedrock-AgentCore Code Interpreter
With simplified architecture and robust error handling
"""

import asyncio
import json
from typing import Dict, List, TypedDict, Optional, Any, Annotated

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage
from langchain_aws import ChatBedrockConverse
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax

console = Console()


# Define the agent state
class AgentState(TypedDict):
    """State for the research agent with proper annotations"""

    messages: Annotated[List, "append"]  # Annotated to handle append operations
    research_query: str
    code_session_id: Optional[str]
    research_data: Dict[str, any]
    completed_tasks: List[str]
    errors: List[str]


class ResearchAgent:
    """Streamlined research agent"""

    def __init__(
        self,
        region: str = "us-west-2",
        model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    ):
        self.region = region
        self.model = model
        self.llm = ChatBedrockConverse(model=model, region_name=region)

        console.print("[cyan]Initializing Bedrock-AgentCore Tools...[/cyan]")

        # Initialize Code Interpreter session
        self.code_client = CodeInterpreter(region)
        self.code_session_id = self.code_client.start()
        console.print(f"✅ Code Interpreter session: {self.code_session_id}")

        # Set up working environment
        self._setup_working_environment()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        console.print("\n[yellow]Cleaning up...[/yellow]")
        if self.code_client:
            self.code_client.stop()

    def _setup_working_environment(self):
        """Set up the working environment in the code interpreter with detailed feedback"""
        setup_code = """
import os
import sys
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend

# Print current working directory
print(f"Current working directory: {os.getcwd()}")
print(f"Python version: {sys.version}")

# Create directories with detailed feedback
try:
    os.makedirs('data', exist_ok=True)
    print("✓ Created 'data' directory")
    os.makedirs('visualizations', exist_ok=True)
    print("✓ Created 'visualizations' directory")
    os.makedirs('reports', exist_ok=True)
    print("✓ Created 'reports' directory")
    print("Environment setup complete.")
except Exception as e:
    print(f"Error creating directories: {e}")
    
# Test file writing
try:
    with open('data/test_file.txt', 'w') as f:
        f.write('Test file writing capability')
    print("✓ Successfully tested file writing")
except Exception as e:
    print(f"Error writing test file: {e}")

# List directories to confirm
print("\\nDirectory structure:")
for root, dirs, files in os.walk('.'):
    level = root.count(os.sep)
    indent = ' ' * 4 * level
    print(f"{indent}{os.path.basename(root) or '.'}/")
    for file in files:
        print(f"{indent}    {file}")
"""
        result = self.code_client.invoke(
            "executeCode",
            {"code": setup_code, "language": "python", "clearContext": False},
        )
        console.print(self._extract_output(result))

    def _refresh_file_list(self):
        """Get updated list of files in the sandbox"""
        result = self.code_client.invoke("listFiles", {"path": ""})
        return self._extract_output(result).strip().split("\n") if self._extract_output(result).strip() else []

    def _extract_output(self, result: Dict) -> str:
        """Extract output from code execution result"""
        if "structuredContent" in result:
            stdout = result["structuredContent"].get("stdout", "")
            stderr = result["structuredContent"].get("stderr", "")
            return stdout + (f"\nSTDERR: {stderr}" if stderr else "")

        output_parts = []
        if "content" in result:
            for item in result["content"]:
                if item.get("type") == "text":
                    output_parts.append(item.get("text", ""))
        return "\n".join(output_parts)

    def _extract_code_block(self, text: str) -> str:
        """Extract code from text that might contain markdown code blocks"""
        if "```python" in text:
            # Extract the code block
            start_idx = text.find("```python") + 9
            end_idx = text.find("```", start_idx)
            if end_idx != -1:
                return text[start_idx:end_idx].strip()
        elif "```" in text:
            # Extract the code block without language specification
            start_idx = text.find("```") + 3
            end_idx = text.find("```", start_idx)
            if end_idx != -1:
                return text[start_idx:end_idx].strip()

        # If no code block is found, return the whole text
        return text.strip()

    def execute_llm_generated_code(self, task_description: str, context: Dict = None) -> Dict[str, Any]:
        """Have LLM generate and execute code for the task"""
        console.print(f"\n[bold blue]🤖 LLM generating code for:[/bold blue] {task_description}")

        # Build prompt with context
        prompt = f"""You are working in a Python code interpreter sandbox. 
Task: {task_description}

Available context:
{json.dumps(context, indent=2) if context else "No previous context"}

Generate Python code to accomplish this task. Be specific and include:
- All necessary imports (pandas, numpy, matplotlib, seaborn, scikit-learn, etc. are available)
- Error handling with try/except blocks
- Clear output with print statements to show progress
- Ensure visualizations have proper titles, labels, and legends
- Save outputs to appropriate directories:
  * data/ - for CSV and JSON files
  * visualizations/ - for plots and charts
  * reports/ - for text reports

Return ONLY the Python code, no explanations."""

        # Get code from LLM
        response = self.llm.invoke([HumanMessage(content=prompt)])
        generated_code = self._extract_code_block(response.content)

        # Display the code preview
        code_preview = generated_code[:300] + "..." if len(generated_code) > 300 else generated_code
        console.print(Syntax(code_preview, "python"))

        # Execute the code
        result = self.code_client.invoke(
            "executeCode",
            {"code": generated_code, "language": "python", "clearContext": False},
        )

        # Extract output
        output = self._extract_output(result)

        # Check for error
        has_error = result.get("isError", False)
        if has_error:
            console.print(f"[red]Execution error:[/red]\n{output}")
        else:
            console.print("[green]✅ Code executed successfully[/green]")

        # Get updated file list
        files = self._refresh_file_list()

        return {"output": output, "error": has_error, "files": files}

    def create_workflow(self) -> StateGraph:
        """Create a simple linear workflow that attempts all steps"""
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("understand_query", self.understand_query)
        workflow.add_node("collect_data", self.collect_data)
        workflow.add_node("process_data", self.process_data)
        workflow.add_node("analyze_data", self.analyze_data)
        workflow.add_node("generate_insights", self.generate_insights)

        # Set linear flow - attempt all steps
        workflow.set_entry_point("understand_query")
        workflow.add_edge("understand_query", "collect_data")
        workflow.add_edge("collect_data", "process_data")
        workflow.add_edge("process_data", "analyze_data")
        workflow.add_edge("analyze_data", "generate_insights")
        workflow.add_edge("generate_insights", END)

        return workflow.compile()

    def understand_query(self, state: AgentState) -> AgentState:
        """Understand what the user wants to research"""
        console.print(f"\n[bold magenta]🎯 Understanding research query:[/bold magenta] {state['research_query']}")

        # Have LLM break down the query
        prompt = f"""Analyze this research query: '{state["research_query"]}'
        
Break it down into:
1. What specific data points need to be collected
2. What analysis techniques would be most appropriate
3. What insights are expected
4. What visualizations would be most informative

Respond in JSON format with the following structure:
{{
  "data_points": [],
  "analysis_techniques": [],
  "expected_insights": [],
  "recommended_visualizations": []
}}"""

        response = self.llm.invoke([HumanMessage(content=prompt)])
        understanding = response.content

        try:
            # Try to parse as JSON
            json_understanding = json.loads(understanding)
            console.print("[green]Query analysis completed as structured JSON[/green]")
        except json.JSONDecodeError:
            console.print("[yellow]Could not parse response as JSON. Using raw text.[/yellow]")
            json_understanding = {"raw_analysis": understanding}

        # Display a summary of the understanding
        console.print("[cyan]Query Understanding:[/cyan]")
        for key, value in json_understanding.items():
            if isinstance(value, list) and value:
                console.print(f"[cyan]• {key}:[/cyan] {', '.join(value[:3])}{'...' if len(value) > 3 else ''}")
            else:
                preview = str(value)[:100] + "..." if len(str(value)) > 100 else str(value)
                console.print(f"[cyan]• {key}:[/cyan] {preview}")

        return {
            **state,
            "research_data": {"query_understanding": json_understanding},
            "completed_tasks": ["understand_query"],
            "errors": [],
        }

    def collect_data(self, state: AgentState) -> AgentState:
        """Collect data based on the research query"""
        console.print("\n[bold magenta]📊 Collecting data...[/bold magenta]")

        # Always create synthetic data directly
        synthetic_data_code = """
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import os
import matplotlib.pyplot as plt
import seaborn as sns

# Ensure directory exists
os.makedirs('data', exist_ok=True)
os.makedirs('visualizations', exist_ok=True)

# Set random seed
np.random.seed(42)

# Customer IDs
n_customers = 1000
customer_ids = [f'CUST{i:05d}' for i in range(n_customers)]

# Date range - last 2 years
end_date = datetime.now()
start_date = end_date - timedelta(days=730)
dates = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]

# Create purchase data - multiple purchases per customer
purchases = []
for cust_id in customer_ids:
    # Random number of purchases (0 to 15)
    n_purchases = np.random.poisson(3)  
    for _ in range(n_purchases):
        purchase_date = np.random.choice(dates)
        # Higher probability of purchases in recent months
        days_ago = (end_date - purchase_date).days
        if days_ago > 365 and random.random() < 0.5:
            continue  # Skip some older purchases
            
        purchases.append({
            'customer_id': cust_id,
            'purchase_date': purchase_date,
            'product_category': np.random.choice(['Electronics', 'Clothing', 'Home', 'Books', 'Beauty', 'Food', 'Sports']),
            'amount': round(np.random.gamma(shape=2, scale=25), 2),
            'satisfaction_score': np.random.choice(range(1, 11), p=[0.01, 0.02, 0.03, 0.05, 0.09, 0.15, 0.25, 0.2, 0.1, 0.1]),
            'delivery_days': np.random.choice(range(1, 10)),
            'is_return': np.random.choice([0, 1], p=[0.95, 0.05])
        })

# Convert to DataFrame
df = pd.DataFrame(purchases)

# Add more features
df['is_repeat_purchase'] = df.groupby('customer_id')['purchase_date'].rank(method='first') > 1
df['is_repeat_purchase'] = df['is_repeat_purchase'].astype(int)

# Calculate customer lifetime value
customer_stats = df.groupby('customer_id').agg(
    total_spent=('amount', 'sum'),
    avg_satisfaction=('satisfaction_score', 'mean'),
    purchase_count=('purchase_date', 'count')
).reset_index()

# Save data files
df.to_csv('data/research_data.csv', index=False)
customer_stats.to_csv('data/customer_stats.csv', index=False)

# Create a simple visualization
plt.figure(figsize=(10, 6))
sns.histplot(df['satisfaction_score'], kde=True, bins=10)
plt.title('Distribution of Customer Satisfaction Scores')
plt.xlabel('Satisfaction Score')
plt.ylabel('Count')
plt.savefig('visualizations/satisfaction_distribution.png', dpi=300)

print(f"Created dataset with {len(df)} purchases from {n_customers} customers")
print(f"Data saved to data/research_data.csv")
print(f"Customer stats saved to data/customer_stats.csv")
print(f"Basic visualization saved to visualizations/satisfaction_distribution.png")
print("\\nFirst 5 rows of data:")
print(df.head())
print("\\nSummary statistics:")
print(df.describe())
"""

        # Execute the data creation code directly
        result = self.code_client.invoke(
            "executeCode",
            {"code": synthetic_data_code, "language": "python", "clearContext": False},
        )

        output = self._extract_output(result)
        console.print(output)

        # Check if we have errors
        errors = state["errors"]
        if result.get("isError", False):
            errors.append("Error generating synthetic data")

        return {
            **state,
            "research_data": {
                **state["research_data"],
                "data_collection_output": output,
            },
            "completed_tasks": state["completed_tasks"] + ["collect_data"],
            "errors": errors,
        }

    def process_data(self, state: AgentState) -> AgentState:
        """Process and clean the collected data"""
        console.print("\n[bold magenta]🔧 Processing data...[/bold magenta]")

        # LLM generates data processing code
        result = self.execute_llm_generated_code(
            "Load data/research_data.csv and perform thorough data processing: "
            "1. Handle missing values "
            "2. Remove outliers or cap extreme values "
            "3. Create summary statistics and distributions "
            "4. Add derived features useful for the analysis "
            "5. Create visualizations showing data quality "
            "6. Save processed data as data/processed_data.csv "
            "7. Save summary statistics as data/summary_stats.json",
            context=state["research_data"],
        )

        # Check if we have errors
        errors = state["errors"]
        if result["error"]:
            errors.append("Error processing data")

        return {
            **state,
            "research_data": {
                **state["research_data"],
                "processing_output": result["output"],
                "available_files": result["files"],
            },
            "completed_tasks": state["completed_tasks"] + ["process_data"],
            "errors": errors,
        }

    def analyze_data(self, state: AgentState) -> AgentState:
        """Perform analysis on the processed data"""
        console.print("\n[bold magenta]📈 Analyzing data...[/bold magenta]")

        # Find the best data file to use
        available_files = state["research_data"].get("available_files", [])
        data_file = (
            "data/processed_data.csv" if "data/processed_data.csv" in available_files else "data/research_data.csv"
        )

        # Get understanding to guide analysis
        understanding = state["research_data"].get("query_understanding", {})

        # LLM generates analysis code based on the research query
        result = self.execute_llm_generated_code(
            f"Load {data_file} and perform comprehensive analysis for: {state['research_query']}. "
            "Your analysis should include: "
            "1. Trend analysis over time for satisfaction metrics "
            "2. Correlation analysis between satisfaction and repeat purchases "
            "3. Customer segmentation based on behavior patterns "
            "4. Feature importance for factors driving repeat purchases "
            "5. Create visualizations saved to the visualizations/ directory "
            "6. Save analysis results as data/analysis_results.json",
            context={
                "query": state["research_query"],
                "understanding": understanding,
                "available_files": state["research_data"].get("available_files", []),
            },
        )

        # Check if we have errors
        errors = state["errors"]
        if result["error"]:
            errors.append("Error analyzing data")

        return {
            **state,
            "research_data": {
                **state["research_data"],
                "analysis_output": result["output"],
                "available_files": result["files"],
            },
            "completed_tasks": state["completed_tasks"] + ["analyze_data"],
            "errors": errors,
        }

    def generate_insights(self, state: AgentState) -> AgentState:
        """Generate final report with insights regardless of previous step success"""
        console.print("\n[bold magenta]💡 Generating insights and report...[/bold magenta]")

        # Get list of available files
        available_files = state["research_data"].get("available_files", [])
        if not available_files:
            available_files = self._refresh_file_list()

        # Filter for specific file types
        data_files = [f for f in available_files if f.endswith(".csv") or f.endswith(".json")]
        viz_files = [f for f in available_files if f.endswith((".png", ".jpg", ".jpeg", ".svg"))]

        # Load analysis results if available
        analysis_data = {}
        if "data/analysis_results.json" in available_files:
            try:
                result = self.code_client.invoke("readFiles", {"paths": ["data/analysis_results.json"]})
                analysis_content = self._extract_output(result)
                analysis_data = json.loads(analysis_content) if analysis_content else {}
            except Exception:
                console.print("[yellow]Could not load analysis results[/yellow]")

        # Generate report directly with LLM
        prompt = f"""Create a comprehensive markdown research report for: {state["research_query"]}

Available data files: {data_files}
Available visualizations: {viz_files}
Completed research steps: {state["completed_tasks"]}
Analysis results: {json.dumps(analysis_data, indent=2)[:1000] if analysis_data else "Not available"}

The report should include:
1. Executive summary
2. Key findings with supporting data
3. Methodology section
4. Analysis of factors driving customer satisfaction
5. Analysis of factors driving repeat purchases
6. Actionable recommendations for businesses
7. References to any visualizations using markdown image syntax: ![description](filename)

Format as a complete professional markdown document with proper headings, bullet points, and formatting.
"""

        response = self.llm.invoke([HumanMessage(content=prompt)])
        report_content = response.content

        # Save the report directly
        try:
            save_result = self.code_client.invoke(
                "executeCode",
                {
                    "code": f"import os\nos.makedirs('reports', exist_ok=True)\nwith open('reports/final_report.md', 'w') as f:\n    f.write('''{report_content}''')\nprint('Report saved successfully to reports/final_report.md')",
                    "language": "python",
                },
            )
            console.print(self._extract_output(save_result))
        except Exception as e:
            console.print(f"[yellow]Could not save report file: {e}[/yellow]")

        # Display the report
        console.print("\n[bold green]📄 Final Report:[/bold green]")
        console.print("=" * 60)

        try:
            md = Markdown(report_content[:5000] + ("..." if len(report_content) > 5000 else ""))
            console.print(md)
        except Exception:
            # Fallback to plain text if Markdown rendering fails
            console.print(report_content[:2000] + "..." if len(report_content) > 2000 else report_content)

        console.print("=" * 60)

        # Return updated state with the report
        return {
            **state,
            "messages": state["messages"] + [AIMessage(content=report_content)],
            "research_data": {**state["research_data"], "final_report": report_content},
            "completed_tasks": state["completed_tasks"] + ["generate_insights"],
        }


async def run_research(query: str):
    """Run research with dynamic LLM-generated code"""
    console.print(
        Panel(
            f"[bold cyan]🚀 Dynamic Research Agent[/bold cyan]\n\n"
            f"Research Query: {query}\n\n"
            "[dim]Using Bedrock-AgentCore Code Interpreter with LLM-generated code[/dim]",
            border_style="blue",
        )
    )

    with ResearchAgent() as agent:
        workflow = agent.create_workflow()

        initial_state = {
            "messages": [HumanMessage(content=query)],
            "research_query": query,
            "code_session_id": agent.code_session_id,
            "research_data": {},
            "completed_tasks": [],
            "errors": [],
        }

        final_state = await workflow.ainvoke(initial_state)

        # List all files created
        console.print("\n[bold]Files created during research:[/bold]")
        files = agent._refresh_file_list()
        for file in files:
            if file.endswith(("/")):
                console.print(f"[blue]📁 {file}[/blue]")
            elif file.endswith((".png", ".jpg", ".jpeg", ".svg")):
                console.print(f"[magenta]🖼️ {file}[/magenta]")
            elif file.endswith((".csv", ".json")):
                console.print(f"[yellow]📊 {file}[/yellow]")
            elif file.endswith((".md", ".txt")):
                console.print(f"[green]📝 {file}[/green]")
            else:
                console.print(f"📄 {file}")

        console.print(
            f"\n[bold green]✅ Research completed with {len(final_state['completed_tasks'])} tasks![/bold green]"
        )
        console.print(f"Completed: {', '.join(final_state['completed_tasks'])}")

        if final_state.get("errors"):
            console.print(f"[red]⚠️ {len(final_state['errors'])} errors encountered[/red]")
            for error in final_state["errors"]:
                console.print(f"[red]- {error}[/red]")


if __name__ == "__main__":
    import sys

    # Get query from command line or use default
    query = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else "Analyze customer satisfaction trends in e-commerce and identify factors that drive repeat purchases"
    )

    asyncio.run(run_research(query))
