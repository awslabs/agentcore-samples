"""Constants for CloudWatch trace data export and evaluation."""

import os

# API Configuration
DEFAULT_MAX_EVALUATION_ITEMS = int(os.getenv("AGENTCORE_MAX_EVAL_ITEMS", "1000"))
MAX_SPAN_IDS_IN_CONTEXT = int(os.getenv("AGENTCORE_MAX_SPAN_IDS", "20"))

DEFAULT_RUNTIME_SUFFIX = "DEFAULT"

# Dashboard Configuration
EVALUATION_OUTPUT_DIR = "evaluation_output"
EVALUATION_INPUT_DIR = "evaluation_input"
DASHBOARD_DATA_FILE = "dashboard_data.js"
DASHBOARD_HTML_FILE = "evaluation_dashboard.html"
EVALUATION_OUTPUT_PATTERN = "*.json"
DEFAULT_FILE_ENCODING = "utf-8"

# Session-Scoped Evaluators (sessionId-only)
# These evaluators require data across all traces in a session
SESSION_SCOPED_EVALUATORS = {
    "Builtin.GoalSuccessRate",
}

# Span-Scoped Evaluators (spanIds-only)
# These evaluators require specific span-level data (tool invocations)
SPAN_SCOPED_EVALUATORS = {
    "Builtin.ToolSelectionAccuracy",
    "Builtin.ToolParameterAccuracy",
}

# Flexible-Scoped Evaluators (notSpanIds)
# These evaluators can work at session OR trace level (don't require span IDs)
FLEXIBLE_SCOPED_EVALUATORS = {
    "Builtin.Correctness",
    "Builtin.Faithfulness",
    "Builtin.Helpfulness",
    "Builtin.ResponseRelevance",
    "Builtin.Conciseness",
    "Builtin.Coherence",
    "Builtin.InstructionFollowing",
    "Builtin.Refusal",
    "Builtin.Harmfulness",
    "Builtin.Stereotyping",
}


class AttributePrefixes:
    """OpenTelemetry attribute prefixes."""

    GEN_AI = "gen_ai"
