# Evaluation Configuration
# Edit the values below to customize your evaluation setup

# AWS Configuration
AWS_REGION = "us-east-1"

# AgentCore Configuration for your runtime deployed agent
# TODO: Replace <YOUR_ACCOUNT_ID> and <YOUR_AGENT_NAME> with your actual values
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:<YOUR_ACCOUNT_ID>:runtime/<YOUR_AGENT_NAME>"
QUALIFIER = "DEFAULT"
LOG_GROUP_NAME = "/aws/bedrock-agentcore/runtimes/<YOUR_AGENT_NAME>-DEFAULT"
SERVICE_NAME = "<YOUR_AGENT_NAME>.DEFAULT"

# Evaluation Configuration for AgentCore Evaluators using Online APIs
EVAL_CONFIG_NAME = "actor_simulator_online_eval"
EVAL_DESCRIPTION = "Online evaluation for actor simulator test cases with builtin metrics"
# TODO: Replace <YOUR_ACCOUNT_ID> with your actual AWS account ID
EVALUATION_ROLE_ARN = "arn:aws:iam::<YOUR_ACCOUNT_ID>:role/AgentCoreEvaluationRole"
SAMPLING_PERCENTAGE = 100.0
SESSION_TIMEOUT_MINUTES = 5
EVALUATION_ENDPOINT_URL = "https://bedrock-agentcore-control.us-east-1.amazonaws.com"

# Builtin Evaluators - Add or remove as needed
EVALUATORS = [
    "Builtin.Helpfulness",
    "Builtin.ToolSelectionAccuracy",
    "Builtin.Faithfulness",
    "Builtin.GoalSuccessRate",
    "Builtin.ToolParameterAccuracy",
    "Builtin.Correctness",
]

# Agent Context for strands eval dataset generator
AGENT_CAPABILITIES = "Simple arithmetic: addition, subtraction, multiplication, division"
AGENT_LIMITATIONS = "Cannot solve trigonometry, calculus, linear algebra, or multi-step word problems"
AGENT_TOOLS = ["calculator"]
AGENT_TOPICS = ["basic mathematics", "simple arithmetic", "number comparison"]
AGENT_COMPLEXITY = "single-step or two-step calculations only"

# Test Generation Settings for strands eval actor simulator
NUM_TEST_CASES = 10
MAX_TURNS = 7
