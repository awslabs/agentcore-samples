# Evaluation Configuration
# Edit the values below to customize your evaluation setup

# AWS Configuration
AWS_REGION = "us-east-1"

# AgentCore Configuration for your runtime deployed agent
# TODO: Replace <YOUR_ACCOUNT_ID> and <UNIQUE-ID> with your actual values
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:<YOUR_ACCOUNT_ID>:runtime/langgraph_web_search_agent-<UNIQUE-ID>"
QUALIFIER = "DEFAULT"
LOG_GROUP_NAME = "/aws/bedrock-agentcore/runtimes/langgraph_web_search_agent-<UNIQUE-ID>-DEFAULT"
SERVICE_NAME = "langgraph_web_search_agent.DEFAULT"

# Evaluation Configuration for AgentCore Evaluators using Online APIs
EVAL_CONFIG_NAME = "web_search_agent_online_eval"
EVAL_DESCRIPTION = "Online evaluation for web search agent test cases with builtin metrics"
# TODO: Replace <YOUR_ACCOUNT_ID> with your actual AWS account ID
EVALUATION_ROLE_ARN = "arn:aws:iam::<YOUR_ACCOUNT_ID>:role/AgentCoreEvaluationRole"
SAMPLING_PERCENTAGE = 100.0
SESSION_TIMEOUT_MINUTES = 5
EVALUATION_ENDPOINT_URL = "https://bedrock-agentcore-control.us-east-1.amazonaws.com"

# Builtin Evaluators - Add or remove as needed
EVALUATORS = [
    "Builtin.Correctness",
    "Builtin.Faithfulness",
    "Builtin.Helpfulness",
    # "Builtin.Relevance",
    "Builtin.Conciseness",
    # "Builtin.Coherence",
    "Builtin.InstructionFollowing",
    "Builtin.Refusal",
    "Builtin.Harmfulness",
    # "Builtin.Stereotyping",
    "Builtin.GoalSuccessRate",
    "Builtin.ToolSelectionAccuracy",
    "Builtin.ToolParameterAccuracy",
]

# Agent Context for strands eval dataset generator
AGENT_CAPABILITIES = "Real-time web search using DuckDuckGo to find current information about destinations, attractions, events, shows, restaurants, museums, activities, travel tips, and general knowledge queries. Can retrieve up-to-date information from the internet including titles, summaries, and source URLs."

AGENT_LIMITATIONS = "Cannot book flights, hotels, or activities. Cannot store or maintain conversation history across sessions. Cannot access private/gated content or perform authentication. Cannot make reservations or transactions. Limited to publicly available web information. Searches may take 20+ seconds due to rate limiting."

AGENT_TOOLS = ["web_search"]

AGENT_TOPICS = [
    "NYC attractions and museums",
    "Broadway shows and entertainment",
    "restaurants and dining",
    "travel destinations",
    "current events and festivals",
    "rodeos and cowboy experiences",
    "hotels and accommodations",
    "weather and forecasts",
    "cultural activities",
    "tourist attractions",
    "local events",
    "general knowledge questions",
]

AGENT_COMPLEXITY = "Multi-turn web search queries. Can handle informational requests, comparison questions, and recommendation queries. Best suited for 'What', 'Where', 'When', and 'How' questions that require current web information."

# Test Generation Settings for strands eval actor simulator
NUM_TEST_CASES = 10
MAX_TURNS = 3

# Custom Evaluator Configuration - LLM as a Judge
CUSTOM_EVALUATOR_NAME = "web_search_quality_evaluator"
CUSTOM_EVALUATOR_CONFIG = {
    "llmAsAJudge": {
        "modelConfig": {
            "bedrockEvaluatorModelConfig": {
                "modelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "inferenceConfig": {"maxTokens": 500, "temperature": 1.0},
            }
        },
        "ratingScale": {
            "numerical": [
                {
                    "value": 0.0,
                    "definition": "No search performed when needed, or search results completely irrelevant. No sources cited. Information appears fabricated or hallucinatory.",
                    "label": "Failed Search",
                },
                {
                    "value": 0.17,
                    "definition": "Search performed but results have minimal relevance to query. Poor source attribution. Information synthesis is poor or misleading.",
                    "label": "Very Poor Quality",
                },
                {
                    "value": 0.33,
                    "definition": "Search results somewhat relevant but incomplete. Missing important context. Sources mentioned but not well integrated. Partial information synthesis.",
                    "label": "Below Average Quality",
                },
                {
                    "value": 0.5,
                    "definition": "Search results adequately relevant. Basic information provided with some source attribution. Acceptable but not comprehensive synthesis of information.",
                    "label": "Average Quality",
                },
                {
                    "value": 0.67,
                    "definition": "Search results highly relevant. Good information synthesis from multiple sources. Clear source attribution with URLs. Addresses most aspects of the query.",
                    "label": "Good Quality",
                },
                {
                    "value": 0.83,
                    "definition": "Search results very relevant and comprehensive. Excellent synthesis of information from multiple high-quality sources. Clear attribution. Addresses all aspects of query with current information.",
                    "label": "Very High Quality",
                },
                {
                    "value": 1.0,
                    "definition": "Search results exceptionally relevant and comprehensive. Outstanding synthesis with insights from multiple authoritative sources. Perfect attribution. Provides current, accurate information that thoroughly addresses the query. Anticipates related user needs.",
                    "label": "Exceptional Quality",
                },
            ]
        },
        "instructions": "You are an objective judge evaluating the quality of a web search agent's response. Your task is to assess: (1) Whether the agent performed appropriate web searches for the user's query, (2) The relevance and quality of search results used, (3) How well the agent synthesized information from search results, (4) Whether sources were properly attributed with URLs, (5) Whether the information appears current and accurate. Consider the conversation context and evaluate the target turn. IMPORTANT: Focus on search quality, information synthesis, and source attribution. Do not penalize for search delays or API limitations. # Conversation Context: ## Previous turns: {context} ## Target turn to evaluate: {assistant_turn}",
    }
}
