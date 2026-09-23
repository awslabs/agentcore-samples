#!/usr/bin/env python3
"""
Tools for the Weekly Update Generator Agent.
Contains all tool functions for data reading, analysis, visualization, and reporting.
"""

import csv
import json
import os
from datetime import datetime, timedelta
from strands import tool
import boto3

# S3 configuration for report uploads
S3_BUCKET = "a-sample-dataset-6"  # Will be updated by update_demo_dates.py
S3_PREFIX = "weekly_reports"
DEMO_DATA_PREFIX = "demo_data"  # S3 prefix for demo data


def download_demo_data_from_s3():
    """
    Download all demo data from S3 to local /tmp/demo_data directory.
    This is called once at agent startup.
    """
    import os
    import boto3

    s3_client = boto3.client("s3")
    local_base = "/tmp/demo_data"  # nosec B108

    print(f"📥 Downloading demo data from s3://{S3_BUCKET}/{DEMO_DATA_PREFIX}/")

    try:
        # List all objects under demo_data prefix
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=DEMO_DATA_PREFIX + "/")

        file_count = 0
        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                s3_key = obj["Key"]

                # Skip directory markers
                if s3_key.endswith("/"):
                    continue

                # Calculate local path
                relative_path = s3_key[len(DEMO_DATA_PREFIX) + 1 :]  # Remove 'demo_data/' prefix
                local_path = os.path.join(local_base, relative_path)

                # Create directory if needed
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                # Download file
                s3_client.download_file(S3_BUCKET, s3_key, local_path)
                file_count += 1

        print(f"✅ Downloaded {file_count} files to {local_base}")

        # Update all file paths to use /tmp/demo_data
        return local_base

    except Exception as e:
        print(f"❌ Error downloading demo data: {e}")
        import traceback

        traceback.print_exc()
        raise


# Download demo data at module import time
try:
    download_demo_data_from_s3()
except Exception as e:
    print(f"⚠️ Warning: Could not download demo data: {e}")


@tool
def read_project_status() -> str:
    """Read and summarize project status from CSV file."""
    try:
        # Find the latest week file dynamically
        project_dir = "/tmp/demo_data/project_status"  # nosec B108
        files = [f for f in os.listdir(project_dir) if f.startswith("projects_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No project status files found"
        latest_file = sorted(files)[-1]  # Get the latest week

        projects = []
        with open(os.path.join(project_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                projects.append(row)

        summary = f"Found {len(projects)} projects:\n"
        for p in projects:
            summary += f"- {p['project_name']}: {p['status']} ({p['progress']}% complete)\n"
            if p["blockers"] != "None":
                summary += f"  ⚠️ Blocker: {p['blockers']}\n"

        return summary
    except Exception as e:
        return f"Error reading project status: {e}"


@tool
def read_team_updates() -> str:
    """Read all team member updates from markdown files."""
    try:
        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        files = [f for f in os.listdir(updates_dir) if f.endswith(".md")]

        summary = f"Found {len(files)} team updates:\n\n"

        for filename in sorted(files):
            with open(os.path.join(updates_dir, filename), "r") as f:
                content = f.read()
                # Extract key sections
                name = filename.replace(".md", "").replace("_week_", " ").rsplit(" ", 1)[0].replace("_", " ").title()
                summary += f"## {name}\n"

                # Extract completed items
                if "Completed" in content or "Accomplishments" in content:
                    lines = content.split("\n")
                    in_completed = False
                    for line in lines:
                        if "Completed" in line or "Accomplishments" in line:
                            in_completed = True
                        elif in_completed and line.startswith("#"):
                            break
                        elif in_completed and line.strip().startswith("-"):
                            summary += f"  ✓ {line.strip()[1:].strip()}\n"

                # Extract blockers
                if "Blocker" in content:
                    lines = content.split("\n")
                    in_blockers = False
                    for line in lines:
                        if "Blocker" in line:
                            in_blockers = True
                        elif in_blockers and line.startswith("#"):
                            break
                        elif in_blockers and line.strip().startswith("-"):
                            summary += f"  ⚠️ {line.strip()[1:].strip()}\n"

                summary += "\n"

        return summary
    except Exception as e:
        return f"Error reading team updates: {e}"


@tool
def read_metrics() -> str:
    """Read KPI metrics from CSV file."""
    try:
        # Find the latest week file dynamically
        metrics_dir = "/tmp/demo_data/metrics"  # nosec B108
        files = [f for f in os.listdir(metrics_dir) if f.startswith("kpis_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No metrics files found"
        latest_file = sorted(files)[-1]  # Get the latest week

        metrics = []
        with open(os.path.join(metrics_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                metrics.append(row)

        summary = f"Key Performance Indicators ({len(metrics)} metrics):\n\n"

        for m in metrics:
            trend = "📈" if "+" in m["change_percent"] else "📉"
            summary += f"{trend} {m['metric_name']}: {m['current_value']} ({m['change_percent']} vs last week)\n"
            summary += f"   Status: {m['status']} | Target: {m['target']}\n\n"

        return summary
    except Exception as e:
        return f"Error reading metrics: {e}"


@tool
def read_bug_tracker() -> str:
    """Read bug/issue tracker data from JSON file."""
    try:
        # Find the latest week file dynamically
        issues_dir = "/tmp/demo_data/issues"  # nosec B108
        files = [f for f in os.listdir(issues_dir) if f.startswith("bug_tracker_week_") and f.endswith(".json")]
        if not files:
            return "Error: No bug tracker files found"
        latest_file = sorted(files)[-1]  # Get the latest week

        with open(os.path.join(issues_dir, latest_file), "r") as f:
            data = json.load(f)

        summary = "Bug Tracker Summary:\n\n"
        summary += f"Total Open Issues: {data['summary']['total_open']}\n"
        summary += f"  - Critical: {data['summary']['critical']}\n"
        summary += f"  - High: {data['summary']['high']}\n"
        summary += f"  - Medium: {data['summary']['medium']}\n"
        summary += f"  - Low: {data['summary']['low']}\n\n"

        summary += (
            f"This Week: {data['summary']['opened_this_week']} opened, {data['summary']['closed_this_week']} closed\n"
        )
        summary += f"Average Age: {data['summary']['average_age_days']} days\n\n"

        if data["critical_issues"]:
            summary += "🚨 Critical Issues:\n"
            for issue in data["critical_issues"]:
                summary += f"  - {issue['id']}: {issue['title']}\n"
                summary += f"    Assigned: {issue['assigned_to']} | Status: {issue['status']}\n"

        return summary
    except Exception as e:
        return f"Error reading bug tracker: {e}"


@tool
def read_meeting_notes() -> str:
    """Read meeting notes from markdown files."""
    try:
        notes_dir = "/tmp/demo_data/meeting_notes"  # nosec B108
        files = [f for f in os.listdir(notes_dir) if f.endswith(".md")]

        summary = f"Meeting Notes ({len(files)} meetings):\n\n"

        for filename in sorted(files):
            with open(os.path.join(notes_dir, filename), "r") as f:
                content = f.read()
                # Extract title
                lines = content.split("\n")
                title = lines[0].replace("#", "").strip() if lines else filename
                summary += f"## {title}\n"

                # Extract key decisions or action items
                if "Decision" in content or "Action Item" in content:
                    for line in lines:
                        if "Decision" in line or "Action" in line or line.strip().startswith("- ["):
                            summary += f"  {line.strip()}\n"

                summary += "\n"

        return summary
    except Exception as e:
        return f"Error reading meeting notes: {e}"


@tool
def generate_bug_severity_chart() -> str:
    """Generate a pie chart showing bug distribution by severity."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Find the latest week file dynamically
        issues_dir = "/tmp/demo_data/issues"  # nosec B108
        files = [f for f in os.listdir(issues_dir) if f.startswith("bug_tracker_week_") and f.endswith(".json")]
        if not files:
            return "Error: No bug tracker files found"
        latest_file = sorted(files)[-1]

        with open(os.path.join(issues_dir, latest_file), "r") as f:
            data = json.load(f)

        summary = data["summary"]
        labels = ["Critical", "High", "Medium", "Low"]
        sizes = [
            summary["critical"],
            summary["high"],
            summary["medium"],
            summary["low"],
        ]
        colors = ["#ff4444", "#ff8800", "#ffbb33", "#00C851"]
        explode = (0.1, 0, 0, 0)  # Explode critical slice

        plt.figure(figsize=(8, 6))
        plt.pie(
            sizes,
            explode=explode,
            labels=labels,
            colors=colors,
            autopct="%1.1f%%",
            shadow=True,
            startangle=90,
        )
        plt.title("Bug Distribution by Severity", fontsize=14, fontweight="bold")
        plt.axis("equal")

        os.makedirs("/tmp/weekly_report_output", exist_ok=True)  # nosec B108
        output_path = "/tmp/weekly_report_output/bug_severity_chart.png"  # nosec B108
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()

        return f"Generated bug severity pie chart: {output_path}"
    except Exception as e:
        return f"Error generating bug chart: {e}"


@tool
def generate_metrics_trend_chart() -> str:
    """Generate a faceted bar chart showing current vs target for key metrics."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Find the latest week file dynamically
        metrics_dir = "/tmp/demo_data/metrics"  # nosec B108
        files = [f for f in os.listdir(metrics_dir) if f.startswith("kpis_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No metrics files found"
        latest_file = sorted(files)[-1]

        metrics = []
        with open(os.path.join(metrics_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                metrics.append(row)

        # Select specific metrics to display
        metric_names_to_show = [
            "Daily Active Users",
            "API Response Time (avg)",
            "Customer Support Tickets",
            "New User Signups",
        ]

        selected_metrics = [m for m in metrics if m["metric_name"] in metric_names_to_show]

        # Extract numeric values
        def extract_number(value_str):
            clean = (
                value_str.replace("$", "")
                .replace(",", "")
                .replace("%", "")
                .replace("ms", "")
                .replace(" days", "")
                .split()[0]
            )
            try:
                return float(clean)
            except:  # noqa: E722
                return 0

        # Create 2x2 subplot grid
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        axes = axes.flatten()

        for idx, metric in enumerate(selected_metrics):
            ax = axes[idx]

            current = extract_number(metric["current_value"])
            target = extract_number(metric["target"])

            # Determine color based on status
            if metric["status"] == "Exceeding":
                color = "#00C851"
            elif metric["status"] == "On Track":
                color = "#33b5e5"
            elif metric["status"] == "Improving":
                color = "#ffbb33"
            else:
                color = "#ff4444"

            # Create bars
            x = ["Current", "Target"]
            values = [current, target]
            bars = ax.bar(x, values, color=[color, "#cccccc"], width=0.6)
            # Set alpha individually for each bar
            bars[0].set_alpha(1.0)
            bars[1].set_alpha(0.6)

            # Add value labels on bars
            for bar, val in zip(bars, values):
                height = bar.get_height()
                # Format the label based on metric type
                if "ms" in metric["current_value"].lower():
                    label = f"{val:.0f}ms"
                elif "," in metric["current_value"]:
                    label = f"{val:,.0f}"
                else:
                    label = f"{val:.0f}"

                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    height,
                    label,
                    ha="center",
                    va="bottom",
                    fontsize=11,
                    fontweight="bold",
                )

            # Add percentage change indicator
            change = metric["change_percent"]
            change_color = "#00C851" if "+" in change else "#ff4444"
            ax.text(
                0.5,
                0.95,
                f"{change} vs last week",
                transform=ax.transAxes,
                ha="center",
                va="top",
                fontsize=10,
                color=change_color,
                fontweight="bold",
                bbox=dict(
                    boxstyle="round,pad=0.5",
                    facecolor="white",
                    edgecolor=change_color,
                    alpha=0.8,
                ),
            )

            # Formatting
            ax.set_title(metric["metric_name"], fontsize=12, fontweight="bold", pad=10)
            ax.set_ylabel("Value", fontsize=10)
            ax.grid(axis="y", alpha=0.3, linestyle="--")
            ax.set_ylim(0, max(values) * 1.2)  # Add 20% headroom

            # Add status badge
            status_colors = {
                "Exceeding": "#00C851",
                "On Track": "#33b5e5",
                "Improving": "#ffbb33",
                "At Risk": "#ff4444",
            }
            ax.text(
                0.02,
                0.98,
                metric["status"],
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=9,
                color="white",
                fontweight="bold",
                bbox=dict(
                    boxstyle="round,pad=0.4",
                    facecolor=status_colors.get(metric["status"], "#999999"),
                    alpha=0.9,
                ),
            )

        plt.suptitle(
            "Key Performance Indicators: Current vs Target",
            fontsize=16,
            fontweight="bold",
            y=0.995,
        )
        plt.tight_layout()

        os.makedirs("/tmp/weekly_report_output", exist_ok=True)  # nosec B108
        output_path = "/tmp/weekly_report_output/metrics_status_chart.png"  # nosec B108
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()

        return f"Generated metrics status chart: {output_path}"
    except Exception as e:
        return f"Error generating metrics chart: {e}"


def extract_structured_data():
    """Extract and structure all data from source files."""
    data = {
        "projects": [],
        "team_updates": [],
        "metrics": [],
        "bugs": {},
        "meetings": [],
    }

    # Extract project data
    try:
        # Find the latest week file dynamically
        project_dir = "/tmp/demo_data/project_status"  # nosec B108
        files = [f for f in os.listdir(project_dir) if f.startswith("projects_week_") and f.endswith(".csv")]
        if files:
            latest_file = sorted(files)[-1]
            with open(os.path.join(project_dir, latest_file), "r") as f:
                reader = csv.DictReader(f)
                data["projects"] = list(reader)
    except Exception as e:
        print(f"Warning: Could not read projects: {e}")

    # Extract metrics data
    try:
        # Find the latest week file dynamically
        metrics_dir = "/tmp/demo_data/metrics"  # nosec B108
        files = [f for f in os.listdir(metrics_dir) if f.startswith("kpis_week_") and f.endswith(".csv")]
        if files:
            latest_file = sorted(files)[-1]
            with open(os.path.join(metrics_dir, latest_file), "r") as f:
                reader = csv.DictReader(f)
                data["metrics"] = list(reader)
    except Exception as e:
        print(f"Warning: Could not read metrics: {e}")

    # Extract bug data
    try:
        # Find the latest week file dynamically
        issues_dir = "/tmp/demo_data/issues"  # nosec B108
        files = [f for f in os.listdir(issues_dir) if f.startswith("bug_tracker_week_") and f.endswith(".json")]
        if files:
            latest_file = sorted(files)[-1]
            with open(os.path.join(issues_dir, latest_file), "r") as f:
                data["bugs"] = json.load(f)
    except Exception as e:
        print(f"Warning: Could not read bugs: {e}")

    # Extract team updates
    try:
        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        for filename in os.listdir(updates_dir):
            if filename.endswith(".md"):
                with open(os.path.join(updates_dir, filename), "r") as f:
                    content = f.read()
                    name = filename.replace("_week_04.md", "").replace("_", " ").title()

                    # Parse sections
                    update = {
                        "name": name,
                        "completed": [],
                        "in_progress": [],
                        "blockers": [],
                        "next_week": [],
                    }

                    lines = content.split("\n")
                    current_section = None

                    for line in lines:
                        line_lower = line.lower()
                        if "completed" in line_lower or "accomplishment" in line_lower:
                            current_section = "completed"
                        elif "in progress" in line_lower or "working on" in line_lower:
                            current_section = "in_progress"
                        elif "blocker" in line_lower or "challenge" in line_lower or "concern" in line_lower:
                            current_section = "blockers"
                        elif "next week" in line_lower or "coming up" in line_lower or "priorities" in line_lower:
                            current_section = "next_week"
                        elif line.startswith("#"):
                            current_section = None
                        elif current_section and line.strip().startswith("-"):
                            item = line.strip()[1:].strip()
                            if item:
                                update[current_section].append(item)

                    data["team_updates"].append(update)
    except Exception as e:
        print(f"Warning: Could not read team updates: {e}")

    # Extract meeting notes
    try:
        notes_dir = "/tmp/demo_data/meeting_notes"  # nosec B108
        for filename in os.listdir(notes_dir):
            if filename.endswith(".md"):
                with open(os.path.join(notes_dir, filename), "r") as f:
                    content = f.read()
                    lines = content.split("\n")
                    title = lines[0].replace("#", "").strip() if lines else filename

                    meeting = {"title": title, "decisions": [], "action_items": []}

                    for line in lines:
                        if "decision" in line.lower() and not line.startswith("#"):
                            meeting["decisions"].append(line.strip())
                        elif line.strip().startswith("- [") or ("action" in line.lower() and ":" in line):
                            meeting["action_items"].append(line.strip())

                    data["meetings"].append(meeting)
    except Exception as e:
        print(f"Warning: Could not read meetings: {e}")

    return data


@tool
def analyze_data_quality() -> str:
    """Validate and analyze data quality across all sources."""
    try:
        issues = []
        warnings = []

        # Find the latest week file dynamically
        project_dir = "/tmp/demo_data/project_status"  # nosec B108
        files = [f for f in os.listdir(project_dir) if f.startswith("projects_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No project status files found"
        latest_file = sorted(files)[-1]

        # Check project data
        with open(os.path.join(project_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            projects = list(reader)
            if len(projects) < 3:
                warnings.append("Low number of projects tracked")

        # Check team updates
        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        update_count = len([f for f in os.listdir(updates_dir) if f.endswith(".md")])
        if update_count < 3:
            warnings.append("Not all team members submitted updates")

        print(f"✓ Data quality check complete: {len(issues)} issues, {len(warnings)} warnings")
        return f"Data quality validated: {len(projects)} projects, {update_count} team updates"
    except Exception as e:
        return f"Error in data quality check: {e}"


@tool
def cross_reference_data() -> str:
    """Cross-reference bugs mentioned in team updates with bug tracker."""
    try:
        # Find the latest week file dynamically
        issues_dir = "/tmp/demo_data/issues"  # nosec B108
        files = [f for f in os.listdir(issues_dir) if f.startswith("bug_tracker_week_") and f.endswith(".json")]
        if not files:
            return "Error: No bug tracker files found"
        latest_file = sorted(files)[-1]

        # Load bug tracker
        with open(os.path.join(issues_dir, latest_file), "r") as f:
            bug_data = json.load(f)

        # Extract bug IDs
        all_bugs = []
        for issue in bug_data.get("critical_issues", []):
            all_bugs.append(issue["id"])
        for issue in bug_data.get("high_priority", []):
            all_bugs.append(issue["id"])

        # Check team updates for bug mentions
        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        mentions = 0
        for filename in os.listdir(updates_dir):
            if filename.endswith(".md"):
                with open(os.path.join(updates_dir, filename), "r") as f:
                    content = f.read()
                    for bug_id in all_bugs:
                        if bug_id in content:
                            mentions += 1

        print(f"✓ Cross-reference complete: {mentions} bug mentions found in updates")
        return f"Cross-referenced {len(all_bugs)} bugs across team updates"
    except Exception as e:
        return f"Error in cross-referencing: {e}"


@tool
def analyze_sentiment() -> str:
    """Analyze sentiment and tone of team updates."""
    try:
        positive_indicators = [
            "completed",
            "success",
            "achieved",
            "improved",
            "excellent",
            "great",
        ]
        concern_indicators = [
            "blocker",
            "delayed",
            "issue",
            "problem",
            "concern",
            "risk",
        ]

        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        sentiment_scores = []

        for filename in os.listdir(updates_dir):
            if filename.endswith(".md"):
                with open(os.path.join(updates_dir, filename), "r") as f:
                    content = f.read().lower()
                    positive_count = sum(content.count(word) for word in positive_indicators)
                    concern_count = sum(content.count(word) for word in concern_indicators)
                    score = positive_count - concern_count
                    sentiment_scores.append(score)

        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
        mood = "positive" if avg_sentiment > 2 else "neutral" if avg_sentiment > -2 else "concerned"

        print(f"✓ Sentiment analysis complete: Team mood is {mood}")
        return f"Team sentiment analyzed: Overall mood is {mood}"
    except Exception as e:
        return f"Error in sentiment analysis: {e}"


@tool
def calculate_risk_scores() -> str:
    """Calculate risk scores for projects based on multiple factors."""
    try:
        # Find the latest week file dynamically
        project_dir = "/tmp/demo_data/project_status"  # nosec B108
        files = [f for f in os.listdir(project_dir) if f.startswith("projects_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No project status files found"
        latest_file = sorted(files)[-1]

        with open(os.path.join(project_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            projects = list(reader)

        risk_assessments = []
        for project in projects:
            risk_score = 0

            # Status risk
            if project["status"] == "Behind":
                risk_score += 3
            elif project["status"] == "At Risk":
                risk_score += 2

            # Progress vs budget risk
            progress = int(project["progress"])
            budget_used = int(project["budget_used"])
            if budget_used > progress + 10:
                risk_score += 2

            # Blocker risk
            if project["blockers"] != "None":
                risk_score += 1

            risk_level = "High" if risk_score >= 4 else "Medium" if risk_score >= 2 else "Low"
            risk_assessments.append(f"{project['project_name']}: {risk_level}")

        print(f"✓ Risk scoring complete for {len(projects)} projects")
        return f"Risk scores calculated for {len(projects)} projects"
    except Exception as e:
        return f"Error calculating risk scores: {e}"


@tool
def generate_project_timeline_chart() -> str:
    """Generate a timeline chart showing project milestones."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Find the latest week file dynamically
        project_dir = "/tmp/demo_data/project_status"  # nosec B108
        files = [f for f in os.listdir(project_dir) if f.startswith("projects_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No project status files found"
        latest_file = sorted(files)[-1]

        with open(os.path.join(project_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            projects = list(reader)

        # Create a horizontal bar chart showing progress
        fig, ax = plt.subplots(figsize=(10, 6))

        project_names = [p["project_name"][:30] for p in projects]
        progress_values = [int(p["progress"]) for p in projects]

        # Color based on status
        colors = []
        for p in projects:
            if p["status"] == "On Track":
                colors.append("#00C851")
            elif p["status"] == "At Risk":
                colors.append("#ffbb33")
            else:
                colors.append("#ff4444")

        bars = ax.barh(project_names, progress_values, color=colors)

        # Add progress labels
        for i, (bar, progress) in enumerate(zip(bars, progress_values)):
            ax.text(progress + 2, i, f"{progress}%", va="center")

        ax.set_xlabel("Progress (%)", fontsize=12)
        ax.set_title("Project Progress Overview", fontsize=14, fontweight="bold")
        ax.set_xlim(0, 110)

        # Add legend
        from matplotlib.patches import Patch

        legend_elements = [
            Patch(facecolor="#00C851", label="On Track"),
            Patch(facecolor="#ffbb33", label="At Risk"),
            Patch(facecolor="#ff4444", label="Behind"),
        ]
        ax.legend(handles=legend_elements, loc="lower right")

        plt.tight_layout()
        output_path = "/tmp/weekly_report_output/project_timeline_chart.png"  # nosec B108
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()

        print("✓ Project timeline chart generated")
        return f"Generated project timeline chart: {output_path}"
    except Exception as e:
        return f"Error generating timeline chart: {e}"


@tool
def build_metrics_forecast_model() -> str:
    """Build regression models to forecast metric trends for next 4 weeks."""
    try:
        import numpy as np
        from sklearn.linear_model import LinearRegression

        # Find the latest week file dynamically
        metrics_dir = "/tmp/demo_data/metrics"  # nosec B108
        files = [f for f in os.listdir(metrics_dir) if f.startswith("kpis_week_") and f.endswith(".csv")]
        if not files:
            return "Error: No metrics files found"
        latest_file = sorted(files)[-1]

        # Load current metrics
        with open(os.path.join(metrics_dir, latest_file), "r") as f:
            reader = csv.DictReader(f)
            metrics = list(reader)

        # Load historical data from CSV
        historical_data = {}
        with open("/tmp/demo_data/metrics/kpis_historical.csv", "r") as f:  # nosec B108
            reader = csv.DictReader(f)
            for row in reader:
                metric_name = row["metric_name"]
                week = int(row["week"])
                value = float(row["value"])

                if metric_name not in historical_data:
                    historical_data[metric_name] = {"weeks": [], "values": []}

                historical_data[metric_name]["weeks"].append(week)
                historical_data[metric_name]["values"].append(value)

        # Determine the current week (last week in historical data)
        max_week = max(max(data["weeks"]) for data in historical_data.values())

        # Add current values and metadata
        for metric in metrics:
            metric_name = metric["metric_name"]
            if metric_name in historical_data:
                current_value_str = metric["current_value"]
                change_percent_str = metric["change_percent"]

                # Extract numeric value
                current_value = float(
                    current_value_str.replace("$", "")
                    .replace(",", "")
                    .replace("%", "")
                    .replace("ms", "")
                    .replace(" days", "")
                    .split()[0]
                )
                change_percent = float(change_percent_str.replace("%", "").replace("+", ""))

                historical_data[metric_name]["current"] = current_value
                historical_data[metric_name]["change_percent"] = change_percent

        # Train regression models for each metric
        forecasts = {}

        for metric_name, data in historical_data.items():
            X = np.array(data["weeks"]).reshape(-1, 1)
            y = np.array(data["values"])

            # Train linear regression model
            model = LinearRegression()
            model.fit(X, y)

            # Predict next 4 weeks after the current week
            future_weeks = np.array([max_week + 1, max_week + 2, max_week + 3, max_week + 4]).reshape(-1, 1)
            predictions = model.predict(future_weeks)

            # Calculate R² score
            r2_score = model.score(X, y)

            forecasts[metric_name] = {
                "predictions": predictions.tolist(),
                "r2_score": r2_score,
                "trend": "increasing" if model.coef_[0] > 0 else "decreasing",
                "future_weeks": future_weeks.flatten().tolist(),
            }

        # Calculate average model accuracy
        avg_r2 = np.mean([f["r2_score"] for f in forecasts.values()])

        print(f"✓ Forecast models trained: Average R² = {avg_r2:.3f}")

        # Save forecast data for chart generation (only keep recent 12 weeks for visualization)
        os.makedirs("/tmp/weekly_report_output", exist_ok=True)  # nosec B108
        forecast_file = "/tmp/weekly_report_output/metrics_forecast.json"  # nosec B108

        # Keep only the most recent 12 weeks for cleaner visualization
        recent_historical = {}
        for metric_name, data in historical_data.items():
            recent_weeks = data["weeks"][-12:]
            recent_values = data["values"][-12:]
            recent_historical[metric_name] = {
                "weeks": recent_weeks,
                "values": recent_values,
            }

        with open(forecast_file, "w") as f:
            json.dump(
                {
                    "historical": recent_historical,
                    "forecasts": forecasts,
                    "current_week": max_week,
                },
                f,
                indent=2,
            )

        return f"Built regression models for {len(forecasts)} metrics (avg R²: {avg_r2:.3f})"
    except Exception as e:
        return f"Error building forecast models: {e}"


@tool
def generate_metrics_forecast_chart() -> str:
    """Generate visualization showing historical data and forecasted trends."""
    try:
        import numpy as np
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Load forecast data
        with open("/tmp/weekly_report_output/metrics_forecast.json", "r") as f:  # nosec B108
            data = json.load(f)

        historical = data["historical"]
        forecasts = data["forecasts"]
        current_week = data.get("current_week", 156)

        # Select top 4 metrics to visualize
        metric_names = list(historical.keys())[:4]

        fig, axes = plt.subplots(2, 2, figsize=(14, 8))
        axes = axes.flatten()

        for idx, metric_name in enumerate(metric_names):
            ax = axes[idx]

            hist_data = historical[metric_name]
            forecast_data = forecasts[metric_name]

            # Historical data (last 12 weeks)
            weeks = hist_data["weeks"]
            values = hist_data["values"]

            # Plot historical data (all but last week)
            ax.plot(
                weeks[:-1],
                values[:-1],
                "o-",
                color="#999999",
                linewidth=2,
                markersize=6,
                label="Historical",
                alpha=0.7,
            )

            # Plot current week (last week in historical) with different color
            ax.plot(
                [weeks[-1]],
                [values[-1]],
                "o",
                color="#33b5e5",
                markersize=10,
                label="Current Week",
                zorder=5,
            )

            # Plot forecast (next 4 weeks)
            future_weeks = forecast_data["future_weeks"]
            predictions = forecast_data["predictions"]

            # Connect current week to forecast
            ax.plot(
                [weeks[-1]] + future_weeks,
                [values[-1]] + predictions,
                "s--",
                color="#ff8800",
                linewidth=2,
                markersize=6,
                label="Forecast",
                alpha=0.8,
            )

            # Add trend line across all data
            all_weeks = weeks + future_weeks
            all_values = values + predictions
            z = np.polyfit(all_weeks, all_values, 1)
            p = np.poly1d(z)
            ax.plot(
                all_weeks,
                p(all_weeks),
                ":",
                color="#666666",
                linewidth=1.5,
                alpha=0.5,
                label="Trend Line",
            )

            # Formatting
            ax.set_xlabel("Week Number", fontsize=10)
            ax.set_ylabel("Value", fontsize=10)
            ax.set_title(
                f"{metric_name}\n(Model R² = {forecast_data['r2_score']:.3f})",
                fontsize=11,
                fontweight="bold",
            )
            ax.legend(loc="best", fontsize=8)
            ax.grid(True, alpha=0.3, linestyle="--")

            # Add shaded region for forecast period
            ax.axvspan(
                current_week + 0.5,
                future_weeks[-1] + 0.5,
                alpha=0.1,
                color="orange",
                label="_nolegend_",
            )

            # Add vertical line between current and forecast
            ax.axvline(x=current_week + 0.5, color="red", linestyle="-", alpha=0.3, linewidth=2)

            # Set x-axis limits
            ax.set_xlim(weeks[0] - 0.5, future_weeks[-1] + 0.5)

            # Add text annotations
            y_pos = ax.get_ylim()[1] * 0.95
            mid_hist = (weeks[0] + weeks[-1]) / 2
            mid_forecast = (future_weeks[0] + future_weeks[-1]) / 2
            ax.text(
                mid_hist,
                y_pos,
                "Historical",
                ha="center",
                fontsize=9,
                color="#666666",
                style="italic",
                alpha=0.7,
            )
            ax.text(
                mid_forecast,
                y_pos,
                "Forecast",
                ha="center",
                fontsize=9,
                color="#ff8800",
                style="italic",
                fontweight="bold",
            )

        plt.suptitle(
            f"Metrics Forecast - Next 4 Weeks (Trained on {current_week} weeks of data)",
            fontsize=14,
            fontweight="bold",
            y=0.98,
        )
        plt.subplots_adjust(left=0.08, right=0.95, top=0.88, bottom=0.08, hspace=0.4, wspace=0.25)

        output_path = "/tmp/weekly_report_output/metrics_forecast_chart.png"  # nosec B108
        plt.savefig(output_path, dpi=150)
        plt.close()

        print("✓ Forecast visualization generated")
        return f"Generated metrics forecast chart: {output_path}"
    except Exception as e:
        return f"Error generating forecast chart: {e}"


@tool
def generate_team_velocity_chart() -> str:
    """Generate a chart showing team productivity metrics."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        print("⚡ Generating team velocity analysis...")

        updates_dir = "/tmp/demo_data/team_updates"  # nosec B108
        team_stats = []

        for filename in os.listdir(updates_dir):
            if filename.endswith(".md"):
                with open(os.path.join(updates_dir, filename), "r") as f:
                    content = f.read()
                    name = filename.replace("_week_04.md", "").replace("_", " ").title()

                    # Count completed items
                    completed_count = 0
                    lines = content.split("\n")
                    in_completed = False
                    for line in lines:
                        if "completed" in line.lower() or "accomplishment" in line.lower():
                            in_completed = True
                        elif line.startswith("#"):
                            in_completed = False
                        elif in_completed and line.strip().startswith("-"):
                            completed_count += 1

                    team_stats.append((name, completed_count))

        # Sort by completion count
        team_stats.sort(key=lambda x: x[1], reverse=True)

        names = [t[0] for t in team_stats]
        counts = [t[1] for t in team_stats]

        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.bar(names, counts, color="#33b5e5")

        ax.set_ylabel("Completed Items", fontsize=12)
        ax.set_title("Team Velocity - Completed Items This Week", fontsize=14, fontweight="bold")
        ax.set_ylim(0, max(counts) + 2)

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                height,
                f"{int(height)}",
                ha="center",
                va="bottom",
            )

        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

        output_path = "/tmp/weekly_report_output/team_velocity_chart.png"  # nosec B108
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()

        print("✓ Team velocity chart generated")
        return f"Generated team velocity chart: {output_path}"
    except Exception as e:
        return f"Error generating velocity chart: {e}"


@tool
def save_report(report_content: str) -> str:
    """
    Save the weekly report markdown content to a file.

    Args:
        report_content: The complete markdown content of the weekly report

    Returns:
        Success message with file path
    """
    try:
        os.makedirs("/tmp/weekly_report_output", exist_ok=True)  # nosec B108
        output_path = "/tmp/weekly_report_output/weekly_report.md"  # nosec B108

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        print(f"✓ Report saved to {output_path}")
        return f"Successfully saved report to {output_path}"
    except Exception as e:
        error_msg = f"Error saving report: {e}"
        print(f"❌ {error_msg}")
        return error_msg


@tool
def upload_report_to_s3() -> str:
    """Upload the weekly report and all charts to S3 bucket."""
    try:
        # Use configured S3 bucket and prefix
        bucket = S3_BUCKET
        s3_prefix = S3_PREFIX

        if not bucket or bucket == "your-bucket-name":
            return "Error: No S3 bucket configured. Set S3_BUCKET in tools.py"

        report_dir = "/tmp/weekly_report_output"  # nosec B108

        # Initialize S3 client
        s3_client = boto3.client("s3")

        # Get current week info for folder naming
        today = datetime.now()
        week_num = today.isocalendar()[1]
        year = today.year
        monday = today - timedelta(days=today.weekday())
        week_folder = f"{year}/week_{week_num:02d}_{monday.strftime('%Y-%m-%d')}"

        uploaded_files = []

        # Files to upload
        files_to_upload = [
            "weekly_report.md",
            "bug_severity_chart.png",
            "metrics_status_chart.png",
            "project_timeline_chart.png",
            "team_velocity_chart.png",
            "metrics_forecast_chart.png",
        ]

        print(f"📤 Uploading to s3://{bucket}/{s3_prefix}/{week_folder}/")

        # Debug: Check current working directory and list files
        import os

        print(f"🔍 Current working directory: {os.getcwd()}")
        print(f"🔍 Checking for report_dir: {report_dir}")
        if os.path.exists(report_dir):
            print(f"✓ Directory exists, contents: {os.listdir(report_dir)}")
        else:
            print("❌ Directory does not exist!")
            # Try to create it
            os.makedirs(report_dir, exist_ok=True)
            print(f"✓ Created directory: {report_dir}")

        for filename in files_to_upload:
            filepath = os.path.join(report_dir, filename)

            if os.path.exists(filepath):
                # Determine content type
                content_type = "text/markdown" if filename.endswith(".md") else "image/png"

                # S3 key
                s3_key = f"{s3_prefix}/{week_folder}/{filename}"

                # Upload file
                s3_client.upload_file(filepath, bucket, s3_key, ExtraArgs={"ContentType": content_type})

                uploaded_files.append(s3_key)
                print(f"   ✓ Uploaded {filename}")
            else:
                print(f"   ⚠️  Skipped {filename} (not found)")

        # Generate S3 URLs
        report_url = f"s3://{bucket}/{s3_prefix}/{week_folder}/weekly_report.md"
        https_url = f"https://{bucket}.s3.amazonaws.com/{s3_prefix}/{week_folder}/weekly_report.md"

        print("\n✅ Upload complete!")
        print(f"   Uploaded {len(uploaded_files)} files")
        print(f"   S3 URI: {report_url}")
        print(f"   HTTPS URL: {https_url}")

        return f"Successfully uploaded {len(uploaded_files)} files to s3://{bucket}/{s3_prefix}/{week_folder}/"

    except Exception as e:
        import traceback

        error_msg = f"Error uploading to S3: {e}"
        print(f"❌ {error_msg}")
        traceback.print_exc()
        print("\nTroubleshooting tips:")
        print("1. Verify S3 bucket exists and you have write permissions")
        print("2. Check AWS credentials are configured")
        print("3. Ensure the agent's execution role has s3:PutObject permission")
        return error_msg
