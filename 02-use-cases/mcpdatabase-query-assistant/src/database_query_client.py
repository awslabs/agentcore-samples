#!/usr/bin/env python3
"""
MCP App Client with Bedrock Converse API and Rate Limiting
This is the chat client that executes all necessary tools automatically without user confirmation.
"""

import json
import requests
import sys
import boto3
import time
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError


class RateLimiter:
    """Rate limiter to control MCP server API calls"""

    def __init__(self, calls_per_minute=20):
        self.calls_per_minute = calls_per_minute
        self.calls = deque()

    def sync_acquire(self):
        """Synchronous version of acquire for non-async contexts"""
        now = time.time()
        # Remove calls older than 1 minute
        while self.calls and self.calls[0] < now - 60:
            self.calls.popleft()

        if len(self.calls) >= self.calls_per_minute:
            sleep_time = max(
                0.5, 60 - (now - self.calls[0])
            )  # Minimum 0.5s, max based on oldest call
            print(f"Rate limit reached. Waiting {sleep_time:.1f} seconds...")
            time.sleep(sleep_time)

        self.calls.append(now)


class FinalMCPChat:
    def __init__(self):
        """Initialize the MCP chat client with no confirmation mode"""
        print("Initializing Database Query Assistance MCP Chat Client...")

        # Initialize rate limiter - increased for better performance
        self.rate_limiter = RateLimiter(calls_per_minute=120)  # 2 calls per second
        print("Rate limiter initialized (120 calls/minute)")

        # Initialize response cache for performance
        self.response_cache = {}
        self.cache_ttl = 300  # 5 minutes cache TTL
        print("Response cache initialized (5min TTL)")

        # Load Cognito configuration
        try:
            with open("../config/cognito.json", "r", encoding="utf-8") as f:
                self.cognito_config = json.load(f)
            print("Cognito configuration loaded")
        except Exception as e:
            print(f"Error loading Cognito config: {e}")
            sys.exit(1)

        # Initialize Bedrock client for Converse API
        try:
            self.bedrock_client = boto3.client(
                "bedrock-runtime", region_name="us-east-1"
            )
            print("Bedrock client initialized")
        except Exception as e:
            print(f"Failed to initialize Bedrock client: {e}")
            sys.exit(1)

        # Load agent ARN from config file
        try:
            import yaml

            with open("../config/.bedrock_agentcore.yaml", "r", encoding="utf-8") as f:
                yaml.safe_load(f)

            # Use the current agent ARN from deployment
            agent_arn = "arn:aws:bedrock-agentcore:us-east-1:xxxxx123456:runtime/mcp_db_server_1xxxx-HNxxxxxauuT72Ex"
            print(f"Using agent ARN: {agent_arn}")
        except Exception as e:
            print(f"Could not load agent ARN from config: {e}")
            agent_arn = "arn:aws:bedrock-agentcore:us-east-1:xxxxx123456:runtime/mcp_db_server_1xxxx-HNxxxxxauuT72Ex"
            print(f"Using hardcoded agent ARN: {agent_arn}")

        # Setup MCP endpoint
        encoded_arn = agent_arn.replace(":", "%3A").replace("/", "%2F")
        self.mcp_url = f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

        self.headers = {
            "Authorization": f"Bearer {self.cognito_config['bearer_token']}",  # Back to bearer token
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Connection": "keep-alive",
        }

        # Create a session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=10, max_retries=1
        )
        self.session.mount("https://", adapter)
        print("HTTP connection pooling configured")

        # Dynamic tool discovery with caching
        self.available_tools = []
        self.tools_cache_expiry = 0  # Cache tools for 5 minutes
        self.tools_discovered = False  # Flag to prevent repeated discovery attempts
        # Note: Tool discovery will happen on first use to avoid startup delays

        print("MCP endpoint configured")
        print()

    def _discover_tools(self):
        """Dynamically discover available tools from MCP server with caching"""
        current_time = time.time()

        # Use cached tools if still valid (5 minutes cache) or already discovered
        if (
            self.available_tools and current_time < self.tools_cache_expiry
        ) or self.tools_discovered:
            return

        print("Discovering available tools from MCP server...")

        try:
            # Try using our custom list_available_tools first
            response = self.call_mcp_tool("list_available_tools", {})

            if response and response.get("success") and "tools" in response:
                self.available_tools = response["tools"]
                self.tools_cache_expiry = current_time + 300  # Cache for 5 minutes
                self.tools_discovered = True
                print(f"Discovered {len(self.available_tools)} tools from server")
                return

        except Exception as e:
            print(f"Custom tool discovery failed: {e}")

        # Fallback to standard MCP protocol
        try:
            request_data = {
                "jsonrpc": "2.0",
                "id": f"tools_list_{int(time.time())}",
                "method": "tools/list",
                "params": {},
            }

            response = self.session.post(self.mcp_url, json=request_data, timeout=10)

            if response.status_code == 200:
                response_text = response.text.strip()

                if not response_text:
                    print(
                        "Warning: Empty response from server, using fallback static list"
                    )
                    self._use_fallback_tools()
                    return

                if response_text.startswith("data: "):
                    response_text = response_text[6:]

                if not response_text:
                    print(
                        "Warning: Empty response after SSE parsing, using fallback static list"
                    )
                    self._use_fallback_tools()
                    return

                result = json.loads(response_text)

                if "result" in result and "tools" in result["result"]:
                    self.available_tools = result["result"]["tools"]
                    self.tools_cache_expiry = current_time + 300  # Cache for 5 minutes
                    self.tools_discovered = True
                    print(f"Discovered {len(self.available_tools)} tools from server")
                else:
                    print(
                        "No tools found in server response, using fallback static list"
                    )
                    self._use_fallback_tools()
            else:
                print(
                    f"Warning: Server returned {response.status_code}, using fallback static list"
                )
                self._use_fallback_tools()

        except json.JSONDecodeError:
            print(
                "Warning: JSON decode error (likely server not supporting tools/list), using fallback static list"
            )
            self._use_fallback_tools()
        except Exception as e:
            print(f"Warning: Tool discovery failed: {e}, using fallback static list")
            self._use_fallback_tools()

    def _use_fallback_tools(self):
        """Fallback to actual static tools when dynamic discovery fails"""
        self.available_tools = [
            {
                "name": "execute_query",
                "description": "Execute a SQL query and return the results",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The SQL query to execute",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "list_tables",
                "description": "List all tables in the current database",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "describe_table",
                "description": "Get detailed information about a specific table",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Name of the table to describe",
                        },
                        "schema_name": {
                            "type": "string",
                            "description": "Schema name (defaults to 'public')",
                        },
                    },
                    "required": ["table_name"],
                },
            },
            {
                "name": "health_check",
                "description": "Health check endpoint to verify the MCP server is running",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "get_database_info",
                "description": "Get general information about the database",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
        ]
        self.tools_discovered = True  # Mark as discovered to prevent retries
        print(
            f"Using {len(self.available_tools)} fallback tools (server doesn't support tools/list)"
        )

    def refresh_tools(self):
        """Force refresh of available tools from server"""
        self.tools_cache_expiry = 0  # Expire cache
        self.tools_discovered = False  # Reset discovery flag
        self._discover_tools()

    def call_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Optimized MCP tool call with smart caching and efficient parsing"""
        if arguments is None:
            arguments = {}

        # Smart caching - only cache read-only operations
        cacheable_tools = {
            "list_tables",
            "describe_table",
            "get_database_info",
            "health_check",
            "get_business_module_tables",
            "search_tables_by_pattern",
            "list_available_tools",
        }

        cache_key = None
        if tool_name in cacheable_tools:
            cache_key = f"{tool_name}:{hash(str(sorted(arguments.items())))}"
            cached_result = self.get_cached_response(cache_key)
            if cached_result:
                return cached_result

        # Rate limiting
        self.rate_limiter.sync_acquire()

        # Optimized request payload
        request_data = {
            "jsonrpc": "2.0",
            "id": f"{tool_name}_{int(time.time())}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        try:
            response = self.session.post(self.mcp_url, json=request_data, timeout=8)

            if response.status_code == 200:
                result = self._parse_sse_response(response.text)

                # Cache successful results for cacheable tools
                if result.get("success") and cache_key:
                    self.cache_response(cache_key, result)

                return result
            elif response.status_code == 429:
                return {"success": False, "error": "Rate limited - retry in a moment"}
            else:
                return {"success": False, "error": f"HTTP {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": f"Request failed: {str(e)[:100]}"}

    def _parse_sse_response(self, response_text: str) -> Dict[str, Any]:
        """Optimized SSE response parsing"""
        try:
            # Fast SSE parsing
            if "data: " in response_text:
                start_idx = response_text.find("data: ") + 6
                end_idx = response_text.find("\\r\\n", start_idx)
                json_str = (
                    response_text[start_idx:end_idx]
                    if end_idx != -1
                    else response_text[start_idx:].strip()
                )
            else:
                json_str = response_text.strip()

            parsed = json.loads(json_str)

            # Extract result efficiently
            if "result" in parsed:
                result = parsed["result"]
                if isinstance(result, dict) and "content" in result:
                    content = result["content"]
                    if isinstance(content, list) and content:
                        text_content = content[0].get("text", "")
                        try:
                            return json.loads(text_content)
                        except json.JSONDecodeError:
                            return {"success": True, "data": text_content}
                return result

            return {"success": False, "error": "No result in response"}

        except json.JSONDecodeError:
            return {"success": False, "error": "Invalid JSON response"}

    def get_cached_response(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Optimized cache retrieval with automatic cleanup"""
        if cache_key in self.response_cache:
            cached_data, timestamp = self.response_cache[cache_key]
            if time.time() - timestamp < self.cache_ttl:
                return cached_data
            else:
                # Auto-cleanup expired entries
                del self.response_cache[cache_key]
        return None

    def cache_response(self, cache_key: str, response: Dict[str, Any]):
        """Optimized cache storage with size management"""
        # Limit cache size to prevent memory issues
        if len(self.response_cache) > 100:
            # Remove oldest 20% of entries
            sorted_items = sorted(self.response_cache.items(), key=lambda x: x[1][1])
            for key, _ in sorted_items[:20]:
                del self.response_cache[key]

        self.response_cache[cache_key] = (response, time.time())

    def _convert_tools_for_bedrock(self) -> List[Dict[str, Any]]:
        """Convert MCP tools to Bedrock Converse API format"""
        # Ensure tools are discovered before conversion
        self._discover_tools()

        bedrock_tools = []

        for tool in self.available_tools:
            bedrock_tool = {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": {"json": tool["inputSchema"]},
                }
            }
            bedrock_tools.append(bedrock_tool)

        return bedrock_tools

    def process_user_query_completely(
        self,
        user_input: str,
        conversation_history: List[Dict] = None,
        silent: bool = False,
        active_only: bool = True,
    ) -> str:
        """Process user query with continuous tool execution until completion - NO CONFIRMATIONS"""
        start_time = time.time()  # Start timing

        if conversation_history is None:
            conversation_history = []

        max_iterations = 10  # Increased for complex queries
        iteration = 0

        # Ultra-aggressive system prompt for zero confirmations
        active_filter_instruction = ""
        if active_only:
            active_filter_instruction = """
CRITICAL DATA FILTERING RULE:
ALWAYS filter for active records only by default
- ALL queries MUST include: WHERE is_active = true
- This applies to ALL tables unless user explicitly asks for "inactive" or "all records"
- If joining tables, use: WHERE table1.is_active = true AND table2.is_active = true
- User queries like "show users" means "show ACTIVE users"
- Only show inactive records if user specifically asks for "inactive" or "all records"
"""

        system_prompt = """You are a database assistant that EXECUTES ALL NECESSARY TOOLS AUTOMATICALLY.

ZERO CONFIRMATION MODE - EXECUTE EVERYTHING IMMEDIATELY

MANDATORY EXECUTION RULES:
1. When user asks for data → IMMEDIATELY execute all needed tools
2. NEVER ask "Would you like me to execute this query?"
3. NEVER ask "Shall I proceed?"  
4. NEVER show queries without executing them
5. AUTOMATICALLY chain: discover → understand → execute → present results
{active_filter_instruction}
SPECIFIC WORKFLOW FOR QUERIES:
- Any data query → Auto-discover tables → Auto-execute query({'with is_active=true filter' if active_only else ''}) → Auto-present results

TOOL CHAINING RULES:
- If you need table structure → IMMEDIATELY call describe_table()
- After getting structure → IMMEDIATELY call execute_query() {'WITH is_active=true filter' if active_only else ''}
- After getting results → IMMEDIATELY format and present data
- NO STOPS between tools
- NO CONFIRMATIONS between tools
- COMPLETE THE ENTIRE WORKFLOW AUTOMATICALLY

CURRENT DATE: {datetime.now().strftime('%Y-%m-%d')}

The user asking for data IS the permission to execute everything needed. START EXECUTING NOW."""

        current_messages = conversation_history + [
            {"role": "user", "content": [{"text": user_input}]}
        ]

        while iteration < max_iterations:
            iteration += 1
            if not silent:
                print(f"Processing iteration {iteration}...")

            try:
                # Call Bedrock with system prompt
                response = self.bedrock_client.converse(
                    modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
                    messages=current_messages,
                    system=[{"text": system_prompt}],
                    toolConfig={"tools": self._convert_tools_for_bedrock()},
                    inferenceConfig={
                        "maxTokens": 4000,
                        "temperature": 0.0,
                        "topP": 0.9,
                    },
                )

                if "error" in response:
                    return f"Error: {response['error']}"

                output = response.get("output", {})
                message = output.get("message", {})
                content = message.get("content", [])

                # Check for tool calls
                tool_calls = [item for item in content if "toolUse" in item]
                text_content = [item for item in content if "text" in item]

                # If no tool calls, check if we have a complete answer
                if not tool_calls:
                    if text_content:
                        response_text = "\n".join(
                            [item["text"] for item in text_content]
                        )
                        # Check if this looks like a complete answer with data
                        if any(
                            keyword in response_text.lower()
                            for keyword in [
                                "session",
                                "found",
                                "data",
                                "result",
                                "record",
                                "total",
                            ]
                        ):
                            total_time = time.time() - start_time
                            return f"{response_text}\n\nTotal response time: {total_time:.2f} seconds"
                        elif iteration == 1:
                            # First iteration without tools - might need to be more explicit
                            current_messages.append(
                                {
                                    "role": "assistant",
                                    "content": [{"text": response_text}],
                                }
                            )
                            current_messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "text": "Execute the necessary database tools to get the actual data. Do not ask for permission."
                                        }
                                    ],
                                }
                            )
                            continue
                        else:
                            total_time = time.time() - start_time
                            return f"{response_text}\n\nTotal response time: {total_time:.2f} seconds"
                    else:
                        total_time = time.time() - start_time
                        return f"Query completed but no response generated.\n\nTotal response time: {total_time:.2f} seconds"

                # Process tool calls immediately
                if not silent:
                    print(f"Executing {len(tool_calls)} tools automatically...")
                tool_results = []

                for i, tool_call in enumerate(tool_calls):
                    tool_use = tool_call.get("toolUse", {})
                    tool_name = tool_use.get("name")
                    tool_input = tool_use.get("input", {})
                    tool_use_id = tool_use.get("toolUseId")

                    if not silent:
                        print(f"   Tool {i + 1}: {tool_name}")

                    # Call the MCP tool
                    result = self.call_mcp_tool(tool_name, tool_input)

                    # Format result for Bedrock
                    tool_result = {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": json.dumps(result, indent=2)}],
                        }
                    }

                    if not result.get("success", True):
                        tool_result["toolResult"]["status"] = "error"
                        if not silent:
                            print(f"   Error: {result.get('error', 'Unknown error')}")
                    else:
                        if not silent:
                            print("   Success")

                    tool_results.append(tool_result)

                # Add assistant response and tool results to conversation
                current_messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": tool_results},
                    ]
                )

                # Check if we have actual data results
                has_data = False
                for result in tool_results:
                    result_text = (
                        result.get("toolResult", {})
                        .get("content", [{}])[0]
                        .get("text", "")
                    )
                    if "data" in result_text and '"success": true' in result_text:
                        try:
                            parsed = json.loads(result_text)
                            if parsed.get("data") and len(parsed.get("data", [])) > 0:
                                has_data = True
                                break
                        except json.JSONDecodeError:
                            pass

                # If we have data, continue to get final formatted response
                if (
                    has_data and iteration >= 2
                ):  # Allow at least 2 iterations for discover + execute
                    continue

            except ClientError as e:
                total_time = time.time() - start_time
                return f"AWS error in iteration {iteration}: {e.response['Error']['Message']}\n\nTotal response time: {total_time:.2f} seconds"
            except json.JSONDecodeError as e:
                total_time = time.time() - start_time
                return f"JSON parsing error in iteration {iteration}: {str(e)}\n\nTotal response time: {total_time:.2f} seconds"
            except requests.RequestException as e:
                total_time = time.time() - start_time
                return f"Network error in iteration {iteration}: {str(e)}\n\nTotal response time: {total_time:.2f} seconds"

        total_time = time.time() - start_time
        return f"Maximum iterations reached. Please try a more specific query.\n\nTotal response time: {total_time:.2f} seconds"

    def show_rate_limiter_status(self):
        """Show current rate limiter status"""
        now = time.time()
        while self.rate_limiter.calls and self.rate_limiter.calls[0] < now - 60:
            self.rate_limiter.calls.popleft()

        calls_made = len(self.rate_limiter.calls)
        calls_remaining = self.rate_limiter.calls_per_minute - calls_made

        print("Rate Limiter Status")
        print("=" * 30)
        print(f"Calls made in last minute: {calls_made}")
        print(f"Calls remaining: {calls_remaining}")
        print(f" Rate limit: {self.rate_limiter.calls_per_minute} calls/minute")

        if calls_made > 0:
            oldest_call = self.rate_limiter.calls[0]
            time_until_reset = 60 - (now - oldest_call)
            if time_until_reset > 0:
                print(f"Next call available in: {time_until_reset:.1f} seconds")
        print()

    def clear_cache(self):
        """Clear the response cache"""
        cache_size = len(self.response_cache)
        self.response_cache.clear()
        print(f"Cleared {cache_size} cache entries!")
        print()

    def show_help(self):
        """Show available commands and examples"""
        print("Zero-Confirmation Database Chat Help")
        print("=" * 60)
        print("Natural Language Examples (AUTO-EXECUTED):")
        print("   'What tables do I have?'")
        print("   'Get user info for user ID abc'")
        print("   'How many records are in the orders table?'")
        print()
        print("ZERO CONFIRMATION MODE:")
        print("   • ALL necessary tools execute automatically")
        print("   • NO permission requests")
        print("   • NO manual confirmations needed")
        print("   • Complete workflow in one request")
        print()
        print("Commands:")
        print("   help       - Show this help")
        print("   silent     - Toggle silent mode (hide processing steps)")
        print("   active     - Toggle active-only filter (default: ON)")
        print("   status     - Show rate limiter status")
        print("   clear      - Clear conversation history")
        print("   clearcache - Clear response cache")
        print("   quit       - Exit chat")
        print()

    def run_chat(self):
        """Run the zero-confirmation interactive chat"""
        print("Zero-Confirmation Database Chat")
        print("=" * 50)
        print("MCP Server: DEPLOYED and READY")
        print(" Database: PostgreSQL")
        print("Auto-Execution: ALL tools run automatically")
        print("NO confirmations required - just ask and get results!")
        print()
        print(
            "Ask for any data - the system will automatically execute all needed tools!"
        )
        print(
            "Type 'help' for examples, 'silent' to toggle silent mode, 'active' to toggle active-only filter, or 'quit' to exit"
        )
        print("*** Now ask questions on the data in the database***")
        print("*** For eample: How many products are there in product table?")
        print("=" * 50)

        conversation_history = []
        silent_mode = False  # Default to showing processing steps
        active_only_mode = True  # Default to showing only active records

        while True:
            try:
                mode_indicator = "SILENT" if silent_mode else "NORMAL"
                active_indicator = "ACTIVE" if active_only_mode else "ALL"
                user_input = input(f"You {mode_indicator}{active_indicator}: ").strip()

                if not user_input:
                    continue

                print()

                # Handle special commands
                if user_input.lower() in ["quit", "exit", "q"]:
                    print("Thanks for using Database Query Assistance Chat! Goodbye!")
                    break

                elif user_input.lower() == "help":
                    self.show_help()
                    continue

                elif user_input.lower() == "silent":
                    silent_mode = not silent_mode
                    status = "ON" if silent_mode else "OFF"
                    print(f"Silent mode: {status}")
                    if silent_mode:
                        print("   Silent ON: Only shows final results")
                    else:
                        print("   Silent OFF: Shows processing steps")
                    print()
                    continue

                elif user_input.lower() == "active":
                    active_only_mode = not active_only_mode
                    status = "ON" if active_only_mode else "OFF"
                    print(f"Active-only filter: {status}")
                    if active_only_mode:
                        print(
                            "   Active ON: Only shows active records (is_active = true)"
                        )
                    else:
                        print("   Active OFF: Shows all records including inactive")
                    print()
                    continue

                elif user_input.lower() == "status":
                    self.show_rate_limiter_status()
                    continue

                elif user_input.lower() == "clearcache":
                    self.clear_cache()
                    continue

                elif user_input.lower() == "clear":
                    conversation_history = []
                    print("Conversation history cleared!")
                    continue

                if not silent_mode:
                    print("Auto-executing all necessary tools...")

                # Process query with continuous execution - NO CONFIRMATIONS
                result = self.process_user_query_completely(
                    user_input,
                    conversation_history,
                    silent=silent_mode,
                    active_only=active_only_mode,
                )

                print(f"Assistant: {result}")
                print()

                # Update conversation history with final result
                conversation_history.extend(
                    [
                        {"role": "user", "content": [{"text": user_input}]},
                        {"role": "assistant", "content": [{"text": result}]},
                    ]
                )

                # Keep conversation history manageable
                if len(conversation_history) > 10:
                    conversation_history = conversation_history[-6:]

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}")
                print("Please try again with a different query.")
                print()


def main():
    """Main function"""
    chat = FinalMCPChat()
    chat.run_chat()


if __name__ == "__main__":
    main()
