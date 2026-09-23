#!/usr/bin/env python3

import json
import logging
import os
import re
import sys
import traceback

import psycopg2
from psycopg2 import sql
import requests
from strands import Agent, tool
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory import MemoryClient
from flask import Flask, request, jsonify
from flask_cors import CORS
from opentelemetry import baggage
from opentelemetry.context import attach

# Detect deployment mode
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "ecs")  # 'ecs', 'eks'

app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": "http://localhost:3000"}},
    supports_credentials=True,
)

# Force Flask to show application logs in container
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
app.logger.setLevel(logging.DEBUG)

# Configure Strands observability (optional)
try:
    from strands.observability import configure_tracer

    configure_tracer()
    print("[OTEL] ✅ Strands observability configured")
except ImportError:
    print("[OTEL] ℹ️ Using ADOT auto-instrumentation for observability")
except Exception as e:
    print(f"[OTEL] ⚠️ Observability configuration failed: {e}")
    print("[OTEL] ℹ️ Falling back to ADOT auto-instrumentation")

# Global schema cache
schema_cache = None

print(f"[{DEPLOYMENT_MODE.upper()}] ✅ Flask app created successfully")


def get_system_prompt():
    """Generate system prompt with current database schema"""
    schema = discover_schema()
    return f"""
You are a sales analyst for our company. You analyze our internal sales data and provide market context.

SCOPE: Only answer questions about our company's sales data. For unrelated questions, decline politely.

TOOLS:
1. execute_sql_query - Query our sales database
2. search_web - Get market context for our sales performance

DATABASE SCHEMA:
{schema}

CRITICAL RULES:
1. ALWAYS use execute_sql_query for questions about our internal sales data - never just describe what you would query
2. Use search_web only if market context is needed to enhance database results
3. Use only SELECT queries on tables shown in schema above
4. Sample data shows only limited examples - ALWAYS query to discover all actual values and data patterns in the database
5. Database contains data through 2025 - always query before saying data doesn't exist
6. NEVER include SQL queries in the content field - only provide business insights and analysis
7. MANDATORY: You MUST call tools, not describe what tools you would use

WORKFLOW:
1. Analyze the question to determine what information is needed
2. MUST call execute_sql_query if internal sales data is required
3. Only call search_web if market context is needed to enhance database results
4. Return JSON response with insights from tools used

🚨 CRITICAL: ALWAYS RETURN JSON FORMAT 🚨
EVERY response must be valid JSON - NO EXCEPTIONS
EVEN when declining requests, you MUST return JSON format

JSON OUTPUT REQUIRED:
{{
  "content": "Your response text here",
  "sources": []
}}

EXAMPLES:
- Sales question: {{"content": "Analysis with data", "sources": [{{"type": "database", "name": "Sales Database"}}]}}
- Out of scope: {{"content": "I can only analyze our company's sales data", "sources": []}}
- Error: {{"content": "Unable to process request", "sources": []}}

CRITICAL JSON REQUIREMENTS:
- Output ONLY valid JSON starting with {{ and ending with }}
- NO text before or after the JSON object
- ALWAYS include "content" field with your response as a string
- ALWAYS include "sources" array (empty if no tools used)
- For database sources: {{"type": "database", "name": "Sales Database"}}
- For web sources: {{"type": "web", "title": "Exact title from search result", "url": "Exact URL from search result"}}
- Never fabricate sources - use exact data from tool results

🔥 ABSOLUTE REQUIREMENT 🔥
Your response must be EXACTLY: {{ "content": "...", "sources": [...] }}
NO PLAIN TEXT RESPONSES ALLOWED - SYSTEM WILL FAIL
NEVER include SQL statements in the content - only business analysis
"""


def get_database_connection():
    """Get database connection"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    else:
        return psycopg2.connect(
            host=os.getenv("DB_HOST", "postgres"),
            port=os.getenv("DB_PORT", 5432),
            database=os.getenv("DB_NAME", "sales_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
        )


# amazonq-ignore-next-line
def discover_schema():
    """Dynamically discover database schema for all tables"""
    # amazonq-ignore-next-line
    global schema_cache
    if schema_cache:
        print("📋 Using cached schema")
        return schema_cache

    print("🔍 Discovering database schema dynamically...")
    # amazonq-ignore-next-line
    conn = get_database_connection()
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("""
        SELECT 
            table_name,
            table_type,
            obj_description(c.oid) as table_comment
        FROM information_schema.tables t
        LEFT JOIN pg_class c ON c.relname = t.table_name
        WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    tables = cursor.fetchall()
    print(f"📊 Found {len(tables)} tables: {[t[0] for t in tables]}")

    schema_description = "Database Schema:\n\n"

    for table_name, table_type, table_comment in tables:
        print(f"🔍 Analyzing table: {table_name}")

        # Get table schema
        cursor.execute(
            """
            SELECT 
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                col_description(pgc.oid, c.ordinal_position) as column_comment
            FROM information_schema.columns c
            LEFT JOIN pg_class pgc ON pgc.relname = c.table_name
            WHERE c.table_name = %s
                AND c.table_schema = 'public'
            ORDER BY c.ordinal_position
        """,
            (table_name,),
        )
        columns = cursor.fetchall()

        # amazonq-ignore-next-line
        schema_description += f"Table: {table_name}\n"
        if table_comment:
            schema_description += f"Description: {table_comment}\n"

        schema_description += "Columns:\n"
        for col_name, data_type, is_nullable, col_default, col_comment in columns:
            schema_description += f"- {col_name} ({data_type}"
            if is_nullable == "NO":
                schema_description += ", NOT NULL"
            if col_default:
                schema_description += f", DEFAULT {col_default}"
            if col_comment:
                schema_description += f", -- {col_comment}"
            schema_description += ")\n"

        # Add comprehensive sample data showing variety
        try:
            # Get diverse sample data instead of just first 2 rows
            cursor.execute(sql.SQL("SELECT * FROM {} ORDER BY RANDOM() LIMIT 5").format(sql.Identifier(table_name)))
            sample_data = cursor.fetchall()
            if sample_data:
                col_names = [desc[0] for desc in cursor.description]
                sample_dict = [dict(zip(col_names, row)) for row in sample_data]
                schema_description += f"SAMPLE DATA (5 RANDOM ROWS - NOT COMPLETE DATASET):\n{json.dumps(sample_dict, default=str, indent=2)}\n"

                # Add data variety summary for key categorical columns
                categorical_cols = [
                    "productline",
                    "country",
                    "territory",
                    "dealsize",
                    "status",
                ]
                for col in categorical_cols:
                    if col in [c.lower() for c in col_names]:
                        cursor.execute(
                            sql.SQL(
                                "SELECT {}, COUNT(*) as count FROM {} GROUP BY {} ORDER BY count DESC LIMIT 10"
                            ).format(
                                sql.Identifier(col),
                                sql.Identifier(table_name),
                                sql.Identifier(col),
                            )
                        )
                        variety_data = cursor.fetchall()
                        if variety_data:
                            schema_description += f"\nDATA VARIETY - {col.upper()} (top values):\n"
                            for value, count in variety_data:
                                schema_description += f"- {value}: {count} records\n"

                schema_description += "\nCRITICAL: Sample shows only 5 random rows. The actual table contains thousands more records with extensive variety in all categorical columns. ALWAYS query the database to discover all actual values and patterns.\n"
                print(f"✅ Added comprehensive sample data for {table_name}")
        except Exception as e:
            print(f"⚠️ Could not get sample data for {table_name}: {e}")

        schema_description += "\n"

    cursor.close()
    conn.close()

    print("✅ Schema discovery complete")
    schema_cache = schema_description
    return schema_cache


@tool
def execute_sql_query(sql_query: str) -> str:
    """Execute a SQL query on the PostgreSQL database. The system will automatically provide you with the current database schema including all tables, columns, and sample data when you need to generate SQL queries."""
    print("\n" + "=" * 50)
    print("🔥 EXECUTE_SQL_QUERY TOOL CALLED!")
    print(f"🔥 SQL Query: {sql_query}")
    print("=" * 50 + "\n")
    try:
        # Debug: Print connection details
        database_url = os.getenv("DATABASE_URL")
        print(f"[DB Debug] DATABASE_URL exists: {bool(database_url)}")
        if database_url:
            print("[DB Debug] Using DATABASE_URL connection")
            # amazonq-ignore-next-line
            conn = get_database_connection()
        else:
            print("[DB Debug] Using individual env vars")
            conn = get_database_connection()

        print("[DB Debug] Connection successful")
        print(f"[DB Debug] Executing SQL: {sql_query}")

        # amazonq-ignore-next-line
        cursor = conn.cursor()
        cursor.execute(sql_query)
        results = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        data = [dict(zip(columns, row)) for row in results]
        print(f"[DB Debug] Query returned {len(data)} rows")

        cursor.close()
        conn.close()

        response = {
            "data": data,
            "sql_query": sql_query,
            "source": "PostgreSQL Database",
            "record_count": len(data),
        }

        return json.dumps(response, default=str)

    except Exception as e:
        error_msg = f"Database query failed: {str(e)}"
        print(f"[DB Debug] {error_msg}")
        return json.dumps({"error": error_msg})


@tool
def search_web(query: str) -> str:
    """Search the web for information related to the query using Brave Search API"""
    print("\n" + "=" * 50)
    print("🔥 SEARCH_WEB TOOL CALLED WITH QUERY ONLY!")
    print(f"🔥 Query: {query}")
    print("=" * 50 + "\n")

    all_results = []

    try:
        print("[Web Search Debug] Using Brave Search API...")

        # Get Brave Search API key from environment
        brave_api_key = os.getenv("BRAVE_SEARCH_API_KEY")
        if not brave_api_key:
            print("[Web Search Debug] ❌ BRAVE_SEARCH_API_KEY not found in environment")
            return json.dumps({"error": "Brave Search API key not configured"})

        print(f"[Web Search Debug] Starting Brave search for: '{query}'")

        # Brave Search API endpoint
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": brave_api_key,
        }
        params = {
            "q": query,
            # amazonq-ignore-next-line
            "count": 3,
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        # amazonq-ignore-next-line
        print(f"[Web Search Debug] Brave API response: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            web_results = data.get("web", {}).get("results", [])
            print(f"[Web Search Debug] Brave search returned {len(web_results)} results")

            for i, result in enumerate(web_results):
                search_result = {
                    "title": result.get("title", "")[:100],
                    "url": result.get("url", ""),
                    "snippet": result.get("description", "")[:200],
                    "source": "Web Search",
                }
                all_results.append(search_result)
                print(f"[Web Search Debug] Result {i + 1}: {search_result['title']} - {search_result['url']}")

        elif response.status_code == 429:
            print("[Web Search Debug] ❌ Rate limit exceeded for Brave Search API")
            return json.dumps({"error": "Brave Search API rate limit exceeded"})
        else:
            print(f"[Web Search Debug] ❌ Brave API error: {response.status_code} - {response.text}")
            return json.dumps({"error": f"Brave Search API error: {response.status_code}"})

    # amazonq-ignore-next-line
    except Exception as search_error:
        print(f"[Web Search Debug] ❌ Brave search error: {search_error}")
        print(f"[Web Search Debug] Traceback: {traceback.format_exc()}")
        return json.dumps({"error": f"Brave search failed: {search_error}"})

    response = {
        "query": query,
        "results": all_results,
        "source": "Web Search",
        "total_results": len(all_results),
    }

    print(f"[Web Search Debug] Returning {len(all_results)} results:")
    for i, result in enumerate(all_results):
        print(f"[Web Search Debug] Result {i + 1}: {result['title']} - {result['url']}")

    print("[Web Search Debug] FULL RESPONSE TO AGENT:")
    print(json.dumps(response, indent=2)[:500] + "...")

    result = json.dumps(response)
    print(f"[Web Search Debug] Final JSON length: {len(result)}")
    return result


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "runtime": f"Strands {DEPLOYMENT_MODE.upper()}"})


@app.route("/api/chat/message", methods=["POST"])
def chat_message():
    """Frontend-compatible chat endpoint"""
    try:
        data = request.get_json()
        user_message = data.get("message")  # Frontend sends 'message'
        session_id = data.get("sessionId")
        user_id = data.get("userId")  # Don't default to 'anonymous'

        # Call the main invoke function
        return invoke_agent(user_message, session_id, user_id)
    except Exception as e:
        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Chat API ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/invoke", methods=["POST"])
def invoke():
    """Direct invoke endpoint"""
    try:
        data = request.get_json()
        user_message = data.get("prompt")
        session_id = data.get("sessionId")
        user_id = data.get("userId")  # Don't default to 'anonymous'

        return invoke_agent(user_message, session_id, user_id)
    except Exception as e:
        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Invoke ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500


# amazonq-ignore-next-line
def invoke_agent(user_message, session_id, user_id):
    """Core agent invocation logic"""
    try:
        if not user_message:
            return jsonify({"error": "No message provided"}), 400

        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Processing: {user_message}")
        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Session: {session_id}, User: {user_id}")

        # Set session ID in OTEL baggage for observability
        if session_id:
            ctx = baggage.set_baggage("session.id", session_id)
            attach(ctx)
            print(f"[OTEL] Set session.id in baggage: {session_id}")

        # Initialize AgentCore Memory for containerized deployment
        # amazonq-ignore-next-line
        global memory_id
        if not memory_id:
            try:
                print(f"🔄 Initializing AgentCore memory: {MEMORY_NAME}")
                memories = memory_client.list_memories()
                memory_id = next((m["id"] for m in memories if m["id"].startswith(MEMORY_NAME)), None)

                if memory_id:
                    print(f"✅ Found existing AgentCore memory: {memory_id}")
                else:
                    print(f"🔄 Creating new AgentCore memory: {MEMORY_NAME}")
                    memory = memory_client.create_memory_and_wait(
                        name=MEMORY_NAME,
                        strategies=[],
                        description="Short-term memory for sales assistant",
                        event_expiry_days=30,
                    )
                    memory_id = memory["id"]
                    print(f"✅ Created AgentCore memory: {memory_id}")
            # amazonq-ignore-next-line
            except Exception as e:
                print(f"❌ Memory initialization failed: {e}")
                memory_id = None

        # Create agent with AgentCore Memory hooks
        hooks = []
        if memory_id:
            hooks.append(MemoryHookProvider(memory_client, memory_id))

        # amazonq-ignore-next-line
        agent = Agent(
            model="anthropic.claude-3-sonnet-20240229-v1:0",
            system_prompt=get_system_prompt(),
            tools=[execute_sql_query, search_web],
            hooks=hooks,
            state={"actor_id": user_id, "session_id": session_id},
        )

        # Invoke agent
        response = agent(user_message)
        result = response.message["content"][0]["text"]

        # Clean and validate JSON
        json_match = re.search(r"\{[\s\S]*\}", result)
        if json_match:
            json_str = json_match.group(0)
            print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Original JSON length: {len(json_str)}")

            # Clean control characters and normalize whitespace
            cleaned_json = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", json_str)
            cleaned_json = re.sub(r"\s+", " ", cleaned_json)

            # Fix unescaped quotes in content field
            try:
                # Parse and re-serialize to fix escaping
                parsed = json.loads(cleaned_json)
                cleaned_json = json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                # Manual quote escaping as fallback
                cleaned_json = re.sub(r'"([^"]*?)"([^"]*?)"([^"]*?)"', r'"\1\\"\2\\"\3"', cleaned_json)

            try:
                json.loads(cleaned_json)
                result = cleaned_json
                print(f"[{DEPLOYMENT_MODE.upper()} Runtime] JSON validation successful")
            except json.JSONDecodeError as e:
                print(f"[{DEPLOYMENT_MODE.upper()} Runtime] JSON validation failed: {e}")
                print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Keeping original response")

        # Parse the agent result and format for frontend compatibility
        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Final result for parsing: {result[:200]}...")
        try:
            parsed_result = json.loads(result)
            print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Successfully parsed JSON")
            # Format response to match working frontend expectations
            streaming_response = {
                "type": "complete",
                "response": {
                    "answer": parsed_result.get("content", ""),
                    "sources": parsed_result.get("sources", []),
                    "reasoning": [],
                    "citations": [],
                },
                "timestamp": "2025-10-03T04:26:37.529Z",
            }
            print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Returning streaming response")
            return jsonify(streaming_response)
        except json.JSONDecodeError as e:
            print(f"[{DEPLOYMENT_MODE.upper()} Runtime] JSON parse error: {e}")
            print(
                f"[{DEPLOYMENT_MODE.upper()} Runtime] Error at position {e.pos}: {repr(result[max(0, e.pos - 50) : e.pos + 50]) if hasattr(e, 'pos') else 'N/A'}"
            )
            # If not valid JSON, return error format
            error_response = {
                "type": "error",
                "error": f"Failed to parse agent response: {str(e)}",
            }
            return jsonify(error_response)

    except Exception as e:
        print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Agent ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500


# Agent will be created per request to ensure fresh schema discovery
agent = None

# AgentCore Memory configuration

# Memory configuration
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
MEMORY_NAME = "SalesAnalystMemory"

# Initialize Memory Client
# amazonq-ignore-next-line
memory_client = MemoryClient(region_name=REGION)
memory_id = None

# Memory will be initialized per request to ensure proper logging
memory_id = None


class MemoryHookProvider(HookProvider):
    def __init__(self, memory_client: MemoryClient, memory_id: str):
        self.memory_client = memory_client
        self.memory_id = memory_id

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        try:
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            # For anonymous users, use session_id as actor_id
            if not actor_id:
                actor_id = session_id

            if not actor_id or not session_id or not self.memory_id:
                return

            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                # amazonq-ignore-next-line
                k=6,  # Last 6 turns for context
            )

            if recent_turns:
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"]
                        # amazonq-ignore-next-line
                        content = message["content"]["text"]
                        context_messages.append(f"{role}: {content}")

                context = "\n".join(context_messages)
                event.agent.system_prompt += f"\n\nPREVIOUS CONVERSATION CONTEXT:\n{context}\n\nCURRENT QUESTION:\n"
                # amazonq-ignore-next-line
                print(f"✅ Loaded {len(recent_turns)} conversation turns from AgentCore Memory")

        except Exception as e:
            if "Memory not found" in str(e):
                print(f"❌ Memory not found during load, recreating: {e}")
                self._recreate_memory()
            else:
                print(f"❌ Memory load error: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in AgentCore Memory"""
        try:
            messages = event.agent.messages
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            # For anonymous users, use session_id as actor_id
            if not actor_id:
                actor_id = session_id

            # amazonq-ignore-next-line
            if messages and messages[-1]["content"][0].get("text") and self.memory_id:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[(messages[-1]["content"][0]["text"], messages[-1]["role"])],
                )
        except Exception as e:
            # amazonq-ignore-next-line
            if "Memory not found" in str(e):
                print(f"❌ Memory not found, recreating: {e}")
                self._recreate_memory()
            else:
                print(f"❌ Memory save error: {e}")

    def _recreate_memory(self):
        """Recreate memory if it was deleted"""
        try:
            # amazonq-ignore-next-line
            global memory_id
            print(f"🔄 Recreating AgentCore memory: {MEMORY_NAME}")
            memory = self.memory_client.create_memory_and_wait(
                name=MEMORY_NAME,
                strategies=[],
                description="Short-term memory for sales assistant",
                event_expiry_days=30,
            )
            # amazonq-ignore-next-line
            memory_id = memory["id"]
            self.memory_id = memory_id
            print(f"✅ Recreated AgentCore memory: {memory_id}")
        # amazonq-ignore-next-line
        except Exception as e:
            print(f"❌ Failed to recreate memory: {e}")

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


# AgentCore entry point (only works when app is BedrockAgentCoreApp)
# amazonq-ignore-next-line
def agentcore_invoke(payload):
    """Handler for agent invocation"""
    try:
        print(f"[AgentCore Runtime] Received payload: {payload}")

        # Extract message and session ID
        user_message = payload.get("prompt")
        session_id = payload.get("sessionId")

        if not user_message:
            messages = payload.get("messages", [])
            user_message = messages[0]["content"] if messages else payload.get("inputText", "")

        if not user_message:
            print("[AgentCore Runtime] No prompt found in payload")
            return "No prompt found in input, please provide a message"

        # Extract user ID from payload for AgentCore Memory
        user_id = payload.get("userId") or payload.get("user_id")
        actor_id = user_id  # Use None for anonymous users

        print(f"[AgentCore Runtime] Processing message: {user_message}")
        print(f"[AgentCore Runtime] Session ID: {session_id}")
        print(f"[AgentCore Runtime] User ID: {user_id}")
        print(f"[AgentCore Runtime] Actor ID: {actor_id}")
        contextual_message = user_message

        # Initialize memory if not already done
        # amazonq-ignore-next-line
        global memory_id
        # amazonq-ignore-next-line
        if not memory_id:
            try:
                print(f"🔄 Initializing AgentCore memory: {MEMORY_NAME}")
                memories = memory_client.list_memories()
                memory_id = next((m["id"] for m in memories if m["id"].startswith(MEMORY_NAME)), None)

                if memory_id:
                    print(f"✅ Found existing AgentCore memory: {memory_id}")
                else:
                    print(f"🔄 Creating new AgentCore memory: {MEMORY_NAME}")
                    memory = memory_client.create_memory_and_wait(
                        name=MEMORY_NAME,
                        strategies=[],
                        description="Short-term memory for sales assistant",
                        event_expiry_days=30,
                    )
                    memory_id = memory["id"]
                    print(f"✅ Created AgentCore memory: {memory_id}")
            except Exception as e:
                print(f"❌ Memory initialization failed: {e}")
                memory_id = None
        else:
            print(f"✅ Using existing memory: {memory_id}")

        # Schema will be cached after first discovery

        # Create agent with AgentCore Memory hooks
        print("🔥 INITIALIZING AGENT WITH DYNAMIC SCHEMA DISCOVERY")
        print("=" * 60)

        hooks = []
        if memory_id:
            hooks.append(MemoryHookProvider(memory_client, memory_id))
            print(f"✅ Added memory hook with ID: {memory_id}")

        # amazonq-ignore-next-line
        agent = Agent(
            model="anthropic.claude-3-sonnet-20240229-v1:0",
            system_prompt=get_system_prompt(),
            tools=[execute_sql_query, search_web],
            hooks=hooks,
            state={"actor_id": actor_id, "session_id": session_id},
        )
        print("✅ Agent initialized with dynamic schema and AgentCore Memory")
        print("=" * 60)

        # Invoke the agent with OTEL tracing
        print("🚀 INVOKING AGENT NOW...")
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("agent_invoke") as span:
                span.set_attribute("session_id", session_id or "unknown")
                span.set_attribute("user_id", user_id or "unknown")
                span.add_event("Agent invocation started")
                response = agent(contextual_message)
                span.add_event("Agent invocation completed")
                print("[OTEL] ✅ Agent invocation traced")
        # amazonq-ignore-next-line
        except Exception as otel_error:
            print(f"[OTEL] ⚠️ Tracing failed: {otel_error}")
            response = agent(contextual_message)

        print("✅ AGENT INVOCATION COMPLETE")
        print(f"[AgentCore Runtime] Agent response type: {type(response)}")

        # Parse and clean the JSON response
        result = response.message["content"][0]["text"]
        print(f"[AgentCore Runtime] Raw result length: {len(result)}")

        # Extract and clean JSON object from response

        # Remove debug reflection text that shouldn't be in final response
        result = re.sub(
            r"<search_quality_reflection>.*?</search_quality_reflection>",
            "",
            result,
            flags=re.DOTALL,
        )
        result = re.sub(
            r"<search_quality_score>.*?</search_quality_score>",
            "",
            result,
            flags=re.DOTALL,
        )

        # amazonq-ignore-next-line
        json_match = re.search(r"\{[\s\S]*\}", result)
        if json_match:
            json_str = json_match.group(0)
            print(f"[AgentCore Runtime] Original JSON length: {len(json_str)}")
            print(f"[AgentCore Runtime] First 200 chars: {repr(json_str[:200])}")

            # Clean control characters and normalize whitespace
            cleaned_json = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", json_str)
            cleaned_json = re.sub(r"\s+", " ", cleaned_json)

            # Fix unescaped quotes in content field
            try:
                # Parse and re-serialize to fix escaping
                parsed = json.loads(cleaned_json)
                cleaned_json = json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                # Manual quote escaping as fallback
                cleaned_json = re.sub(r'"([^"]*?)"([^"]*?)"([^"]*?)"', r'"\1\\"\2\\"\3"', cleaned_json)
            print(f"[AgentCore Runtime] Cleaned JSON length: {len(cleaned_json)}")
            print(f"[AgentCore Runtime] Cleaned first 200 chars: {repr(cleaned_json[:200])}")

            try:
                json.loads(cleaned_json)
                result = cleaned_json
                print("[AgentCore Runtime] JSON validation successful")
            except json.JSONDecodeError as e:
                print(f"[AgentCore Runtime] JSON validation failed: {e}")
                print(
                    f"[AgentCore Runtime] Error at position {e.pos}: {repr(cleaned_json[max(0, e.pos - 50) : e.pos + 50])}"
                )
                print("[AgentCore Runtime] Keeping original response")
        else:
            print("[AgentCore Runtime] No JSON object found in response")

        # AgentCore Memory handles conversation storage automatically via hooks
        print(
            f"[AgentCore Runtime] Conversation stored in AgentCore Memory for user: {actor_id}, session: {session_id}"
        )

        # For AgentCore, return the raw response (it handles JSON parsing)
        return result

    except Exception as e:
        print(f"[AgentCore Runtime] ERROR: {str(e)}")
        print(f"[AgentCore Runtime] Traceback: {traceback.format_exc()}")
        return f"Error processing request: {str(e)}"


if __name__ == "__main__":
    # Force unbuffered output for container logging
    sys.stdout.flush()
    sys.stderr.flush()

    print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Starting Strands Agent with ADOT observability")
    print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Available tools: execute_sql_query, search_web")
    print(f"[{DEPLOYMENT_MODE.upper()} Runtime] Deployment mode: {DEPLOYMENT_MODE}")

    # Security: Only enable debug mode in local development
    debug_mode = DEPLOYMENT_MODE == "local"
    # Note: host='0.0.0.0' is required for containerized deployment to accept external connections
    # Container networking and load balancers provide the security boundary
    app.run(host="0.0.0.0", port=8080, debug=debug_mode, use_reloader=False)  # nosec B104
