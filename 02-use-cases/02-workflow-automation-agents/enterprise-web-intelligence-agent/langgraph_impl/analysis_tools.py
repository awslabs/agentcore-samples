"""Analysis tools using BedrockAgentCore SDK's CodeInterpreter."""

import json
from typing import Dict
from rich.console import Console
from datetime import datetime

# Import from BedrockAgentCore SDK
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

console = Console()


class AnalysisTools:
    """Data analysis tools using BedrockAgentCore SDK's CodeInterpreter."""

    def __init__(self, config):
        self.config = config
        self.code_interpreter = CodeInterpreter(config.region)
        self.session_active = False

    def _extract_output(self, result: Dict) -> str:
        """Extract output from CodeInterpreter result."""
        # Handle the streaming response format from SDK
        if "stream" in result:
            for event in result.get("stream", []):
                if "result" in event:
                    result_data = event["result"]
                    if "structuredContent" in result_data:
                        return result_data["structuredContent"].get("stdout", "")
                    elif "content" in result_data:
                        for item in result_data["content"]:
                            if item.get("type") == "text":
                                return item.get("text", "")
        # Fallback to direct result format
        elif "structuredContent" in result:
            return result["structuredContent"].get("stdout", "")
        elif "content" in result:
            for item in result["content"]:
                if item.get("type") == "text":
                    return item.get("text", "")
        return ""

    def initialize(self):
        """Initialize the CodeInterpreter session."""
        console.print("[cyan]🔧 Initializing CodeInterpreter...[/cyan]")

        # Start session using SDK method
        session_id = self.code_interpreter.start(
            name="competitive_intel_analysis",
            session_timeout_seconds=self.config.code_session_timeout,
        )

        self.session_active = True
        console.print(f"✅ CodeInterpreter session: {session_id}")

        # Set up the analysis environment
        setup_code = """
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import json
from datetime import datetime
import os
import subprocess

# Create directories for outputs
os.makedirs('analysis', exist_ok=True)
os.makedirs('reports', exist_ok=True)
os.makedirs('visualizations', exist_ok=True)
os.makedirs('data', exist_ok=True)
os.makedirs('sessions', exist_ok=True)  # For session persistence

# Configure matplotlib
plt.style.use('seaborn-v0_8-darkgrid')
sns.set_palette("husl")

print("Analysis environment ready!")
print(f"Working directory: {os.getcwd()}")
print(f"Available directories: {', '.join([d for d in os.listdir('.') if os.path.isdir(d)])}")

# Test AWS CLI availability
try:
    result = subprocess.run(['aws', '--version'], capture_output=True, text=True)
    print(f"AWS CLI available: {result.stdout.strip()}")
except Exception as e:
    print(f"AWS CLI not available: {e}")
"""

        # Execute setup code
        result = self.code_interpreter.invoke(
            "executeCode",
            {"code": setup_code, "language": "python", "clearContext": False},
        )

        output = self._extract_output(result)
        console.print(f"[dim]{output}[/dim]")

        return session_id

    def save_session_state(self, session_name: str, data: Dict) -> Dict:
        """NEW: Save session state for later resumption."""
        try:
            console.print(f"[cyan]💾 Saving session state: {session_name}[/cyan]")

            # Create a JSON-serializable copy of the data
            serializable_data = self._make_serializable(data)

            save_code = f"""
import json
import os
from datetime import datetime

session_data = {json.dumps(serializable_data)}
session_name = "{session_name}"

# Create session metadata
session_metadata = {{
    "session_name": session_name,
    "saved_at": datetime.now().isoformat(),
    "data_size": len(json.dumps(session_data))
}}

# Save session data
os.makedirs('sessions', exist_ok=True)
session_file = f'sessions/{{session_name}}_data.json'
with open(session_file, 'w') as f:
    json.dump(session_data, f, indent=2)

# Save metadata
metadata_file = f'sessions/{{session_name}}_metadata.json'
with open(metadata_file, 'w') as f:
    json.dump(session_metadata, f, indent=2)

print(f"Session saved: {{session_name}}")
print(f"Data file: {{session_file}} ({{os.path.getsize(session_file)}} bytes)")
print(f"Metadata file: {{metadata_file}}")

# List all saved sessions
all_sessions = [f.replace('_metadata.json', '') for f in os.listdir('sessions') if f.endswith('_metadata.json')]
print(f"Total saved sessions: {{len(all_sessions)}}")
"""

            result = self.code_interpreter.invoke("executeCode", {"code": save_code, "language": "python"})

            output = self._extract_output(result)

            return {"status": "success", "session_name": session_name, "output": output}

        except Exception as e:
            console.print(f"[red]❌ Session save error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def _make_serializable(self, data):
        """Convert data structure to be JSON serializable, handling LangChain messages."""
        if data is None:
            return None

        if isinstance(data, dict):
            return {k: self._make_serializable(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._make_serializable(item) for item in data]
        # Handle LangChain message types
        elif hasattr(data, "content") and hasattr(data, "type"):
            # This is likely a LangChain message object
            return {"content": data.content, "type": getattr(data, "type", "message")}
        # Add handling for other custom objects as needed
        else:
            try:
                # Try to serialize with json to test if it's serializable
                json.dumps(data)
                return data
            except (TypeError, OverflowError):
                # If we can't serialize it, convert to string representation
                return str(data)

    def load_session_state(self, session_name: str) -> Dict:
        """NEW: Load a previously saved session state."""
        try:
            console.print(f"[cyan]📂 Loading session state: {session_name}[/cyan]")

            load_code = f"""
import json
import os

session_name = "{session_name}"

# Check if session exists
data_file = f'sessions/{{session_name}}_data.json'
metadata_file = f'sessions/{{session_name}}_metadata.json'

if not os.path.exists(data_file):
    print(f"Session not found: {{session_name}}")
    print(json.dumps({{"status": "not_found"}}))
else:
    # Load session data
    with open(data_file, 'r') as f:
        session_data = json.load(f)
    
    # Load metadata
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
    else:
        metadata = {{}}
    
    result = {{
        "status": "success",
        "data": session_data,
        "metadata": metadata
    }}
    
    print(json.dumps(result))
"""

            result = self.code_interpreter.invoke("executeCode", {"code": load_code, "language": "python"})

            output = self._extract_output(result)

            try:
                return json.loads(output)
            except Exception:
                return {"status": "error", "output": output}

        except Exception as e:
            console.print(f"[red]❌ Session load error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def save_to_s3_with_aws_cli(self, data: Dict, bucket: str, prefix: str) -> Dict:
        """NEW: Use AWS CLI within Code Interpreter to save data to S3."""
        try:
            console.print("[cyan]☁️ Saving to S3 using AWS CLI...[/cyan]")

            aws_cli_code = f"""
import json
import subprocess
import os
from datetime import datetime

# Prepare data
data = {json.dumps(data)}
bucket = "{bucket}"
prefix = "{prefix}"

# Save data locally first
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
local_file = f'analysis/competitive_analysis_{{timestamp}}.json'

with open(local_file, 'w') as f:
    json.dump(data, f, indent=2)

print(f"Saved locally: {{local_file}}")

# Use AWS CLI to upload to S3
s3_key = f"{{prefix}}competitive_analysis_{{timestamp}}.json"
cmd = [
    'aws', 's3', 'cp',
    local_file,
    f's3://{{bucket}}/{{s3_key}}',
    '--region', '{self.config.region}'
]

try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
    if result.returncode == 0:
        print(f"Successfully uploaded to S3: s3://{{bucket}}/{{s3_key}}")
        
        # List files in the S3 prefix to verify
        list_cmd = ['aws', 's3', 'ls', f's3://{{bucket}}/{{prefix}}', '--region', '{self.config.region}']
        list_result = subprocess.run(list_cmd, capture_output=True, text=True, timeout=30)
        
        print("Files in S3 prefix:")
        print(list_result.stdout)
        
        output = {{
            "status": "success",
            "s3_path": f"s3://{{bucket}}/{{s3_key}}",
            "local_file": local_file,
            "file_size": os.path.getsize(local_file)
        }}
    else:
        print(f"AWS CLI error: {{result.stderr}}")
        output = {{
            "status": "error",
            "error": result.stderr
        }}
        
except subprocess.TimeoutExpired:
    print("AWS CLI command timed out")
    output = {{
        "status": "error",
        "error": "Command timed out"
    }}
except Exception as e:
    print(f"Error: {{e}}")
    output = {{
        "status": "error",
        "error": str(e)
    }}

print(json.dumps(output))
"""

            result = self.code_interpreter.invoke("executeCode", {"code": aws_cli_code, "language": "python"})

            output = self._extract_output(result)

            try:
                # Try to parse JSON output
                lines = output.strip().split("\n")
                for line in reversed(lines):
                    if line.strip().startswith("{"):
                        return json.loads(line)
                return {"status": "success", "output": output}
            except Exception:
                return {"status": "success", "output": output}

        except Exception as e:
            console.print(f"[red]❌ AWS CLI error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def analyze_pricing_patterns(self, competitor_data: Dict) -> Dict:
        """NEW: Analyze pricing patterns across competitors."""
        try:
            console.print("[cyan]🔍 Analyzing pricing patterns...[/cyan]")

            analysis_code = f"""
import json
import pandas as pd

competitor_data = {json.dumps(competitor_data)}

# Analyze what data we have and what's missing
analysis = {{
    "competitors_with_pricing": [],
    "competitors_without_pricing": [],
    "missing_data": {{}},
    "patterns": []
}}

for name, data in competitor_data.items():
    if data.get('pricing', {{}}).get('status') == 'success':
        analysis["competitors_with_pricing"].append(name)
    else:
        analysis["competitors_without_pricing"].append(name)
        analysis["missing_data"][name] = ["pricing_tiers", "subscription_details"]

# Identify patterns
if len(analysis["competitors_with_pricing"]) > 0:
    analysis["patterns"].append("Pricing data available for analysis")
    
print(json.dumps(analysis, indent=2))
"""

            result = self.code_interpreter.invoke("executeCode", {"code": analysis_code, "language": "python"})

            output = self._extract_output(result)

            try:
                return json.loads(output)
            except Exception:
                return {"raw_output": output}

        except Exception as e:
            console.print(f"[red]❌ Pattern analysis error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def generate_competitive_insights(self, competitor_data: Dict, pattern_analysis: Dict) -> Dict:
        """NEW: Generate insights by combining browser and analysis data."""
        try:
            console.print("[cyan]💡 Generating competitive insights...[/cyan]")

            insights_code = f"""
import json
from datetime import datetime

competitor_data = {json.dumps(competitor_data)}
pattern_analysis = {json.dumps(pattern_analysis)}

insights = {{
    "generated_at": datetime.now().isoformat(),
    "total_competitors": len(competitor_data),
    "data_completeness": {{
        "with_pricing": len(pattern_analysis.get("competitors_with_pricing", [])),
        "without_pricing": len(pattern_analysis.get("competitors_without_pricing", []))
    }},
    "key_findings": [],
    "recommendations": []
}}

# Generate findings
if pattern_analysis.get("competitors_with_pricing"):
    insights["key_findings"].append(
        f"Successfully extracted pricing from {{len(pattern_analysis['competitors_with_pricing'])}} competitors"
    )

if pattern_analysis.get("missing_data"):
    insights["recommendations"].append(
        "Consider manual review for competitors with missing data"
    )

print(json.dumps(insights, indent=2))
"""

            result = self.code_interpreter.invoke("executeCode", {"code": insights_code, "language": "python"})

            output = self._extract_output(result)

            try:
                return json.loads(output)
            except Exception:
                return {"raw_output": output}

        except Exception as e:
            console.print(f"[red]❌ Insights generation error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def analyze_competitor_data(self, competitor_name: str, data: Dict) -> Dict:
        """Analyze data for a specific competitor."""
        try:
            console.print(f"[cyan]📊 Analyzing {competitor_name}...[/cyan]")

            analysis_code = f"""
import json
import pandas as pd
import os  # Make sure os is imported
from datetime import datetime

# Load competitor data
competitor_data = {json.dumps(data)}
competitor_name = "{competitor_name}"

# Create analysis summary
analysis = {{
    "competitor": competitor_name,
    "analyzed_at": datetime.now().isoformat(),
    "data_points_collected": {{
        "has_pricing": bool(competitor_data.get('pricing', {{}}).get('data')),
        "has_features": bool(competitor_data.get('features', {{}}).get('data')),
        "navigation_success": competitor_data.get('navigation', {{}}).get('status') == 'success',
        "screenshots_taken": competitor_data.get('screenshots_taken', 0),
        "pages_explored": len(competitor_data.get('additional_pages', [])),
        "forms_found": len(competitor_data.get('interactive_elements', {{}}).get('forms', []))
    }}
}}

# Save raw data
with open(f'analysis/{{competitor_name.replace(" ", "_")}}_raw_data.json', 'w') as f:
    json.dump(competitor_data, f, indent=2)

# Extract and analyze pricing if available
if competitor_data.get('pricing', {{}}).get('data'):
    pricing_data = competitor_data['pricing']['data']
    analysis['pricing_analysis'] = {{
        'data_length': len(str(pricing_data)),
        'extracted_successfully': True
    }}
    
    # Save pricing data
    with open(f'analysis/{{competitor_name.replace(" ", "_")}}_pricing.txt', 'w') as f:
        f.write(str(pricing_data))

# Extract and analyze features if available
if competitor_data.get('features', {{}}).get('data'):
    features_data = competitor_data['features']['data']
    analysis['features_analysis'] = {{
        'data_length': len(str(features_data)),
        'extracted_successfully': True
    }}
    
    # Save features data
    with open(f'analysis/{{competitor_name.replace(" ", "_")}}_features.txt', 'w') as f:
        f.write(str(features_data))

print(json.dumps(analysis, indent=2))

# Define created_files variable before using it
created_files = []
for file in ['raw_data.json', 'pricing.txt', 'features.txt']:
    file_path = f'analysis/{{competitor_name.replace(" ", "_")}}_{{file}}'
    if os.path.exists(file_path):
        created_files.append(file_path)

print(f"\\nCreated {{len(created_files)}} analysis files for {{competitor_name}}:")
for file in created_files:
    print(f"  - {{file}}")
"""

            # Execute using SDK
            result = self.code_interpreter.invoke("executeCode", {"code": analysis_code, "language": "python"})

            output = self._extract_output(result)

            try:
                # Try to extract JSON from output
                lines = output.split("\n")
                for line in lines:
                    if line.strip().startswith("{"):
                        analysis_result = json.loads(line)
                        break
                else:
                    analysis_result = {"raw_output": output}
            except Exception:
                analysis_result = {"raw_output": output}

            return {
                "status": "success",
                "analysis": analysis_result,
                "competitor": competitor_name,
            }

        except Exception as e:
            console.print(f"[red]❌ Analysis error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def create_comparison_visualization(self, all_competitors_data: Dict) -> Dict:
        """Create comparison visualizations."""
        try:
            console.print("[cyan]📈 Creating visualizations...[/cyan]")

            viz_code = f"""
import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os
import glob
from datetime import datetime

# Load all competitor data
all_data = {json.dumps(all_competitors_data)}

# Create figure
fig, axes = plt.subplots(2, 2, figsize=(15, 12))
fig.suptitle('Competitive Intelligence Analysis - ' + datetime.now().strftime('%Y-%m-%d'), fontsize=16)

# Prepare summary data
competitors = list(all_data.keys())
success_rates = []
data_collected = []
screenshots = []
pages_explored = []

for comp in competitors:
    comp_data = all_data[comp]
    # Success rate
    success = 1 if comp_data.get('status') == 'success' else 0
    success_rates.append(success)
    
    # Data collection
    has_pricing = 1 if comp_data.get('pricing', {{}}).get('status') == 'success' else 0
    has_features = 1 if comp_data.get('features', {{}}).get('status') == 'success' else 0
    data_collected.append(has_pricing + has_features)
    
    # Screenshots
    screenshots.append(comp_data.get('screenshots_taken', 0))
    
    # Pages explored
    pages_explored.append(len(comp_data.get('additional_pages', [])))

# Plot 1: Success Rate
ax1 = axes[0, 0]
ax1.bar(competitors, success_rates, color='green', alpha=0.7)
ax1.set_title('Navigation Success Rate')
ax1.set_ylabel('Success (1) / Failure (0)')
ax1.set_ylim(0, 1.2)

# Plot 2: Data Collection
ax2 = axes[0, 1]
ax2.bar(competitors, data_collected, color='blue', alpha=0.7)
ax2.set_title('Data Points Collected')
ax2.set_ylabel('Count (Pricing + Features)')
ax2.set_ylim(0, 2.5)

# Plot 3: Pages Explored
ax3 = axes[1, 0]
ax3.bar(competitors, pages_explored, color='orange', alpha=0.7)
ax3.set_title('Additional Pages Explored')
ax3.set_ylabel('Page Count')

# Plot 4: Summary Table
ax4 = axes[1, 1]
ax4.axis('off')

# Create summary table
summary_data = []
for comp in competitors:
    comp_data = all_data[comp]
    summary_data.append([
        comp,
        '✓' if comp_data.get('status') == 'success' else '✗',
        '✓' if comp_data.get('pricing', {{}}).get('status') == 'success' else '✗',
        str(len(comp_data.get('additional_pages', [])))
    ])

table = ax4.table(cellText=summary_data,
                  colLabels=['Competitor', 'Nav', 'Pricing', 'Pages'],
                  cellLoc='center',
                  loc='center')
table.auto_set_font_size(False)
table.set_fontsize(10)
table.scale(1, 2)

plt.tight_layout()
plt.savefig('visualizations/competitive_analysis_dashboard.png', dpi=300, bbox_inches='tight')
plt.close()

# Create detailed comparison matrix
comparison_df = pd.DataFrame({{
    'Competitor': competitors,
    'Navigation': ['Success' if all_data[c].get('status') == 'success' else 'Failed' for c in competitors],
    'Pricing Data': ['Collected' if all_data[c].get('pricing', {{}}).get('status') == 'success' else 'Missing' for c in competitors],
    'Features Data': ['Collected' if all_data[c].get('features', {{}}).get('status') == 'success' else 'Missing' for c in competitors],
    'Screenshots': [all_data[c].get('screenshots_taken', 0) for c in competitors],
    'Pages Explored': [len(all_data[c].get('additional_pages', [])) for c in competitors]
}})

comparison_df.to_csv('analysis/comparison_matrix.csv', index=False)

print(f"Visualizations created successfully!")
print(f"Dashboard: visualizations/competitive_analysis_dashboard.png")
print(f"Matrix: analysis/comparison_matrix.csv")
print(f"Analyzed {{len(competitors)}} competitors")

# Verify files were created
created_files = []
for file_pattern in ['visualizations/*.png', 'analysis/*.csv']:
    for file_path in glob.glob(file_pattern):
        if os.path.isfile(file_path):
            created_files.append(file_path)
            
print(f"\\nVerified {{len(created_files)}} files were created:")
for file in created_files:
    print(f"  - {{file}}")
"""

            # Execute using SDK
            result = self.code_interpreter.invoke("executeCode", {"code": viz_code, "language": "python"})

            output = self._extract_output(result)

            return {
                "status": "success",
                "output": output,
                "files_created": [
                    "visualizations/competitive_analysis_dashboard.png",
                    "analysis/comparison_matrix.csv",
                ],
            }

        except Exception as e:
            console.print(f"[red]❌ Visualization error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    def _extract_file_content(self, result: Dict) -> str:
        """Extract file content from readFiles result."""
        for event in result.get("stream", []):
            if "result" in event:
                result_data = event["result"]
                if "content" in result_data:
                    for item in result_data["content"]:
                        if item.get("type") == "text":
                            return item.get("text", "")
        return ""

    def generate_final_report(self, all_data: Dict, analysis_results: Dict) -> Dict:
        """Generate the final competitive intelligence report."""
        try:
            console.print("[cyan]📄 Generating final report...[/cyan]")

            # Create the report string directly here in case all else fails
            direct_report = "# Competitive Intelligence Report\n\n"
            direct_report += f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            direct_report += f"**Session ID:** {analysis_results.get('session_id', 'N/A')}\n\n"
            direct_report += "## Executive Summary\n\n"
            direct_report += f"This report analyzes {len(all_data)} competitor websites.\n\n"

            # Add sections for each competitor to direct report
            for competitor, data in all_data.items():
                direct_report += f"### {competitor}\n\n"
                direct_report += f"**Website:** {data.get('url', 'N/A')}\n"
                direct_report += f"**Pages Explored:** {len(data.get('additional_pages', []))}\n\n"

                # Add pricing section if available
                if data.get("pricing", {}).get("data"):
                    direct_report += "#### Pricing Information\n\n"
                    pricing_data = str(data["pricing"].get("data", ""))[:500]
                    direct_report += f"{pricing_data}...\n\n"

                # Add features section if available
                if data.get("features", {}).get("data"):
                    direct_report += "#### Product Features\n\n"
                    features_data = str(data["features"].get("data", ""))[:500]
                    direct_report += f"{features_data}...\n\n"

                direct_report += "---\n\n"

            # Ensure directory exists before creating report file
            exec_code = """
import os
os.makedirs('reports', exist_ok=True)

# Create a simple report to verify file creation works
with open('reports/competitive_intelligence_report.md', 'w') as f:
    f.write("# Test Report\\n\\nThis is a test report to verify file creation.")

# Check if the file was created
if os.path.exists('reports/competitive_intelligence_report.md'):
    print("Test report file created successfully")
    # Read it back to verify
    with open('reports/competitive_intelligence_report.md', 'r') as f:
        content = f.read()
        print(f"File content length: {len(content)}")
else:
    print("Failed to create test report file")
"""

            # Execute the test first
            console.print("[yellow]Testing file creation capability...[/yellow]")
            test_result = self.code_interpreter.invoke("executeCode", {"code": exec_code, "language": "python"})
            test_output = self._extract_output(test_result)
            console.print(f"[dim]Test output: {test_output}[/dim]")

            # Now create the full report
            report_code = f'''
import json
import os
from datetime import datetime

# Create directories
os.makedirs('reports', exist_ok=True)

# Generate markdown report
report_content = """# Competitive Intelligence Report

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Session ID:** {analysis_results.get("session_id", "N/A")}

## Executive Summary

This report analyzes {len(all_data)} competitor websites, examining their pricing strategies and product features.

## Analysis Results

### Overall Statistics
- Total Competitors Analyzed: {len(all_data)}
- Successful Site Visits: {sum(1 for d in all_data.values() if d.get("status") == "success")}
- Pricing Data Collected: {sum(1 for d in all_data.values() if d.get("pricing", {}).get("status") == "success")}
- Feature Data Collected: {sum(1 for d in all_data.values() if d.get("features", {}).get("status") == "success")}
- Total Screenshots: {sum(d.get("screenshots_taken", 0) for d in all_data.values())}
- Total Pages Explored: {sum(len(d.get("additional_pages", [])) for d in all_data.values())}

## Detailed Competitor Analysis
"""

# Add sections for each competitor
for competitor, data in {json.dumps(all_data)}.items():
    report_content += f"### {{competitor}}\\n\\n"
    report_content += f"**Website:** {{data.get('url', 'N/A')}}  \\n"
    report_content += f"**Status:** {{data.get('status', 'Unknown')}}  \\n"
    report_content += f"**Analysis Time:** {{data.get('timestamp', 'N/A')}}  \\n"
    report_content += f"**Screenshots Taken:** {{data.get('screenshots_taken', 0)}}\\n"
    report_content += f"**Pages Explored:** {{len(data.get('additional_pages', []))}}\\n\\n"
    
    # Add pricing section
    if data.get('pricing', {{}}).get('status') == 'success':
        pricing_data_text = str(data['pricing'].get('data', 'No data'))
        if len(pricing_data_text) > 500:
            pricing_data_text = pricing_data_text[:500] + '...'
        report_content += "#### Pricing Information\\n\\n"
        report_content += pricing_data_text + "\\n\\n"
    
    # Add features section
    if data.get('features', {{}}).get('status') == 'success':
        features_data_text = str(data['features'].get('data', 'No data'))
        if len(features_data_text) > 500:
            features_data_text = features_data_text[:500] + '...'
        report_content += "#### Product Features\\n\\n"
        report_content += features_data_text + "\\n\\n"
    
    report_content += "---\\n\\n"

# Add final sections
report_content += """## Session Recording

All browser interactions have been recorded for compliance and review purposes.
The recording includes all navigation, data extraction, and screenshot activities.

---
*End of Report*
"""

# Save the report file
with open('reports/competitive_intelligence_report.md', 'w') as f:
    f.write(report_content)

print(f"Report saved to: reports/competitive_intelligence_report.md")
print(f"Report length: {{len(report_content)}} characters")

# Print the entire report content for direct capture
print("\\nREPORT_CONTENT_BEGIN")
print(report_content)
print("REPORT_CONTENT_END")
'''

            # Execute the code to generate the report
            console.print("[cyan]Creating report file...[/cyan]")
            result = self.code_interpreter.invoke("executeCode", {"code": report_code, "language": "python"})

            output = self._extract_output(result)
            console.print(f"[dim]Report generation output: {output[:200]}...[/dim]")

            # Extract the report content from the output
            report_content = None

            if "REPORT_CONTENT_BEGIN" in output and "REPORT_CONTENT_END" in output:
                try:
                    start_marker = "REPORT_CONTENT_BEGIN"
                    end_marker = "REPORT_CONTENT_END"
                    start_pos = output.find(start_marker) + len(start_marker)
                    end_pos = output.find(end_marker)
                    if start_pos > 0 and end_pos > start_pos:
                        report_content = output[start_pos:end_pos].strip()
                        console.print(f"[green]✅ Extracted report content ({len(report_content)} chars)[/green]")
                except Exception as e:
                    console.print(f"[yellow]Error extracting report from markers: {e}[/yellow]")

            # If we couldn't extract the report from output, use our direct report
            if not report_content or len(report_content) < 100:  # Sanity check
                console.print("[yellow]Using direct report as fallback...[/yellow]")
                report_content = direct_report

                # Also try to save it directly
                try:
                    save_code = f'''
import os
os.makedirs('reports', exist_ok=True)
with open('reports/competitive_intelligence_report.md', 'w') as f:
    f.write("""{direct_report}""")
print("Direct report saved successfully")
'''
                    self.code_interpreter.invoke("executeCode", {"code": save_code, "language": "python"})
                except Exception as e:
                    console.print(f"[yellow]Failed to save direct report: {e}[/yellow]")

            # Track files created during report generation
            file_tracking_code = """
import os

created_files = []
# Check common directories
for dir_name in ['reports', 'analysis', 'visualizations', 'data', 'sessions']:
    if os.path.exists(dir_name):
        for file in os.listdir(dir_name):
            file_path = os.path.join(dir_name, file)
            if os.path.isfile(file_path):
                created_files.append(file_path)

# List all created files
print("\\nFiles created:")
for file in sorted(created_files):
    print(f"  - {file}")
"""

            track_result = self.code_interpreter.invoke(
                "executeCode", {"code": file_tracking_code, "language": "python"}
            )
            track_output = self._extract_output(track_result)
            console.print(f"[dim]{track_output}[/dim]")

            return {
                "status": "success",
                "report_content": report_content,
                "output": output,
                "report_path": "reports/competitive_intelligence_report.md",
            }

        except Exception as e:
            console.print(f"[red]❌ Report generation error: {e}[/red]")
            import traceback

            traceback.print_exc()

            # Create minimal report as fallback
            fallback_report = "# Competitive Intelligence Report\n\n"
            fallback_report += f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            fallback_report += f"**Error encountered during report generation:** {str(e)}\n\n"
            fallback_report += f"Analyzed {len(all_data)} competitors.\n\n"

            return {
                "status": "error",
                "error": str(e),
                "report_content": fallback_report,
                "report_path": "reports/competitive_intelligence_report.md",
            }

    def cleanup(self):
        """Clean up CodeInterpreter session."""
        if self.session_active:
            console.print("[yellow]🧹 Cleaning up CodeInterpreter...[/yellow]")
            try:
                self.code_interpreter.stop()
                self.session_active = False
                console.print("✅ CodeInterpreter cleaned up")
            except Exception as e:
                console.print(f"[yellow]Warning: Error cleaning up CodeInterpreter: {e}[/yellow]")
