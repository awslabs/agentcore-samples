"""
Video Games Sales Data Analyst Assistant - Main Application

This application provides an intelligent data analyst assistant specialized in video game sales analysis.
It leverages Amazon Bedrock Claude models for natural language processing, Aurora Serverless PostgreSQL
for data storage, and AgentCore Memory (STM + LTM) for conversation context management.

Key Features:
- Natural language to SQL query conversion
- Video game sales data analysis and insights
- Short-term memory (conversation persistence) and long-term semantic memory (facts across sessions)
- Real-time streaming responses
- Comprehensive error handling and logging
"""

import json
import os
from uuid import uuid4

# Bedrock Agent Core imports
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from strands_tools import current_time
from strands.models import BedrockModel
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

# Custom module imports
from src.tools import get_tables_information, run_sql_query
from src.utils import save_raw_query_result, load_file_content

# Retrieve AgentCore Memory ID
memory_id = os.environ.get("MEMORY_ID")

# Retrieve Bedrock Model ID
bedrock_model_id_env = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

# Initialize the Bedrock Agent Core app
app = BedrockAgentCoreApp()


def load_system_prompt():
    """
    Load the system prompt configuration for the video games sales analyst assistant.

    Returns:
        str: The system prompt configuration for the assistant
    """
    print("\n" + "=" * 50)
    print("📝 LOADING SYSTEM PROMPT")
    print("=" * 50)

    fallback_prompt = """You are a specialized Video Games Sales Data Analyst Assistant with expertise in
                analyzing gaming industry trends, sales performance, and market insights. You can execute SQL queries,
                interpret gaming data, and provide actionable business intelligence for the video game industry."""

    try:
        prompt = load_file_content("instructions.txt", default_content=fallback_prompt)
        if prompt == fallback_prompt:
            print("⚠️  Using fallback prompt (instructions.txt not found)")
        else:
            print("✅ Successfully loaded system prompt from instructions.txt")
            print(f"📊 Prompt length: {len(prompt)} characters")
        print("=" * 50 + "\n")
        return prompt
    except Exception as e:
        print(f"❌ Error loading system prompt: {str(e)}")
        print("=" * 50 + "\n")
        return fallback_prompt


# Load the system prompt
DATA_ANALYST_SYSTEM_PROMPT = load_system_prompt()


def create_execute_sql_query_tool(user_prompt: str, prompt_uuid: str):
    """
    Create a dynamic SQL query execution tool for video game sales data analysis.

    Args:
        user_prompt (str): The original user question about video game sales data
        prompt_uuid (str): Unique identifier for tracking this analysis prompt

    Returns:
        function: Configured SQL execution tool with video game sales context
    """

    @tool
    def execute_sql_query(sql_query: str, description: str) -> str:
        """
        Execute SQL queries against the video game sales database for data analysis.

        Args:
            sql_query (str): The SQL query to execute against the video game sales database
            description (str): Clear description of what the query analyzes or retrieves

        Returns:
            str: JSON string containing query results, metadata, or error information
        """
        print("\n" + "=" * 60)
        print("🎮 VIDEO GAME SALES DATA QUERY EXECUTION")
        print("=" * 60)
        print(f"📝 Analysis: {description}")
        print(f"🔍 SQL Query: {sql_query[:200]}{'...' if len(sql_query) > 200 else ''}")
        print(f"🆔 Prompt UUID: {prompt_uuid}")
        print("-" * 60)

        try:
            print("⏳ Executing video game sales data query via RDS Data API...")
            response_json = json.loads(run_sql_query(sql_query))

            if "error" in response_json:
                print(f"❌ Query execution failed: {response_json['error']}")
                print("=" * 60 + "\n")
                return json.dumps(response_json)

            records_to_return = response_json.get("result", [])
            message = response_json.get("message", "")

            print("✅ Video game sales data query executed successfully")
            print(f"📊 Data records retrieved: {len(records_to_return)}")

            if message != "":
                result = {"result": records_to_return, "message": message}
            else:
                result = {"result": records_to_return}

            print("💾 Saving analysis results to DynamoDB for audit trail...")
            save_result = save_raw_query_result(prompt_uuid, user_prompt, sql_query, description, result, message)

            if not save_result["success"]:
                print(f"⚠️  Failed to save analysis results: {save_result['error']}")
                result["saved"] = False
                result["save_error"] = save_result["error"]
            else:
                print("✅ Analysis results saved to DynamoDB audit trail")

            print("=" * 60 + "\n")
            return json.dumps(result)

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"💥 EXCEPTION: {error_msg}")
            print("=" * 60 + "\n")
            return json.dumps({"error": error_msg})

    return execute_sql_query


@app.entrypoint
async def agent_invocation(payload):
    """Main entry point for video game sales data analysis requests with streaming responses.

    Expected payload structure:
    {
        "prompt": "Your video game sales analysis question",
        "prompt_uuid": "optional-unique-prompt-identifier",
        "user_timezone": "US/Pacific",
        "session_id": "conversation-session-id",
        "user_id": "cognito-user-sub"
    }

    Returns:
        AsyncGenerator: Yields streaming response chunks with analysis results
    """
    try:
        # Extract parameters from payload
        user_message = payload.get(
            "prompt",
            "No prompt found in input, please guide customer to create a json payload with prompt key",
        )
        prompt_uuid = payload.get("prompt_uuid", str(uuid4()))
        user_timezone = payload.get("user_timezone", "US/Pacific")
        session_id = payload.get("session_id", str(uuid4()))
        user_id = payload.get("user_id", "guest")
        user_name = payload.get("user_name", "User")

        print("\n" + "=" * 80)
        print("🎮 VIDEO GAME SALES ANALYSIS REQUEST")
        print("=" * 80)
        print(f"💬 User Query: {user_message[:100]}{'...' if len(user_message) > 100 else ''}")
        print(f"🤖 Claude Model: {bedrock_model_id_env}")
        print(f"🆔 Prompt UUID: {prompt_uuid}")
        print(f"🌍 User Timezone: {user_timezone}")
        print(f"🔗 Session ID: {session_id}")
        print(f"👤 User ID: {user_id}")
        print(f"👤 User Name: {user_name}")
        print("-" * 80)

        # Initialize Claude model
        print(f"🧠 Initializing Claude model: {bedrock_model_id_env}")
        bedrock_model = BedrockModel(model_id=bedrock_model_id_env)
        print("✅ Claude model ready")

        # Configure AgentCore Memory with STM + LTM retrieval
        print("-" * 80)
        print("🧠 Configuring AgentCore Memory (STM + LTM)...")

        agentcore_memory_config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=user_id,
            retrieval_config={
                "/facts/{actorId}": RetrievalConfig(
                    top_k=5,
                    relevance_score=0.3,
                ),
            },
        )

        print(f"📋 Memory ID: {memory_id}")
        print(f"👤 Actor ID: {user_id}")
        print(f"🔗 Session ID: {session_id}")
        print("📊 LTM retrieval: /facts/{actorId} (top_k=5, relevance>=0.3)")

        # Configure system prompt with user context
        system_prompt = DATA_ANALYST_SYSTEM_PROMPT.replace("{timezone}", user_timezone).replace(
            "{user_name}", user_name
        )

        print("-" * 80)
        print("🔧 Initializing agent with AgentCoreMemorySessionManager...")

        # Initialize session manager (explicit close instead of context manager for async generator)
        session_manager = AgentCoreMemorySessionManager(
            agentcore_memory_config=agentcore_memory_config,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

        try:
            # Create agent — session_manager handles STM loading, LTM retrieval, and message saving
            agent = Agent(
                model=bedrock_model,
                system_prompt=system_prompt,
                session_manager=session_manager,
                tools=[
                    get_tables_information,
                    current_time,
                    create_execute_sql_query_tool(user_message, prompt_uuid),
                ],
                callback_handler=None,
            )

            print("✅ Agent ready with AgentCore Memory (STM + LTM)")
            print("   🔧 3 tools (database schema, time utilities, SQL execution)")

            print("-" * 80)
            print("🚀 Starting video game sales data analysis...")
            print("=" * 80)

            # Stream the response
            tool_active = False

            async for item in agent.stream_async(user_message):
                if "event" in item:
                    event = item["event"]

                    if "contentBlockStart" in event and "toolUse" in event["contentBlockStart"].get("start", {}):
                        tool_active = True
                        yield json.dumps({"event": event}) + "\n"

                    elif "contentBlockStop" in event and tool_active:
                        tool_active = False
                        yield json.dumps({"event": event}) + "\n"

                elif "start_event_loop" in item:
                    yield json.dumps(item) + "\n"
                elif "current_tool_use" in item and tool_active:
                    yield json.dumps(item["current_tool_use"]) + "\n"
                elif "data" in item:
                    yield json.dumps({"data": item["data"]}) + "\n"
        finally:
            try:
                session_manager.close()
            except Exception as close_err:
                print(f"⚠️ Memory flush warning (non-fatal): {close_err}")

    except Exception as e:
        import traceback

        tb = traceback.extract_tb(e.__traceback__)
        filename, line_number, function_name, text = tb[-1]
        print("\n" + "=" * 80)
        print("💥 VIDEO GAME SALES ANALYSIS ERROR")
        print("=" * 80)
        print(f"❌ Error: {str(e)}")
        print(f"📍 Location: Line {line_number} in {filename}")
        print(f"🔧 Function: {function_name}")
        if text:
            print(f"💻 Code: {text}")
        print("=" * 80 + "\n")

        # Send error as a proper data chunk so the frontend renders it as a normal
        # assistant message and the user can continue the conversation.
        error_detail = str(e)[:200]
        error_msg = (
            "I'm sorry, I encountered a temporary issue processing your request. "
            "Please try again — I'm ready to help with your video game sales analysis. "
            f"(Details: {error_detail})"
        )
        yield json.dumps({"data": error_msg}) + "\n"


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("🚀 STARTING VIDEO GAMES SALES DATA ANALYST ASSISTANT")
    print("=" * 80)
    print("📡 Server starting on port 8080...")
    print("🌐 Health check endpoint: /ping")
    print("🎯 Analysis endpoint: /invocations")
    print("📋 Ready to analyze video game sales trends and insights!")
    print("=" * 80)
    app.run()
