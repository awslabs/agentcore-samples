"""
memory_manager.py — AgentCore Memory integration for the Text-to-Python IDE.

Two responsibilities:
  1. Save every generate/execute turn to short-term memory (per session)
  2. Retrieve relevant long-term memories to inject as context into prompts

Memory structure:
  - Short-term: raw conversation events per actor+session (auto-extracted to long-term)
  - Long-term semantic: code patterns, solutions, reusable functions  → ide/{actorId}/knowledge/
"""

import json
import logging
import os

from dotenv import load_dotenv
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

logger = logging.getLogger(__name__)

# Load .env from project root
_dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_dotenv_path)

MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", "")
# Fallback: read from memory_info.json if env var not set
if not MEMORY_ID:
    _mem_info_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory_info.json")
    if os.path.exists(_mem_info_path):
        with open(_mem_info_path) as _f:
            MEMORY_ID = json.load(_f).get("memory_id", "")

REGION = os.getenv("AWS_REGION", "us-east-1")

# Namespace for long-term semantic knowledge (per user)
KNOWLEDGE_NS = "ide/{actorId}/knowledge/"

_client: MemorySessionManager | None = None


def _get_client() -> MemorySessionManager | None:
    """Lazily initialise the MemorySessionManager. Returns None if memory is not configured."""
    global _client
    if not MEMORY_ID:
        return None
    if _client is None:
        try:
            _client = MemorySessionManager(memory_id=MEMORY_ID, region_name=REGION)
            logger.info("✅ AgentCore MemorySessionManager initialised (memory_id=%s)", MEMORY_ID)
        except Exception as e:
            logger.warning("⚠️  Could not initialise MemorySessionManager: %s", e)
            return None
    return _client


def save_turn(actor_id: str, session_id: str, user_prompt: str, agent_response: str):
    """
    Save a user→agent turn to short-term memory.
    AgentCore automatically extracts long-term memories from these turns
    using the strategies defined at memory creation time.
    """
    client = _get_client()
    if not client:
        return

    try:
        client.add_turns(
            actor_id=actor_id,
            session_id=session_id,
            messages=[
                ConversationalMessage(user_prompt, MessageRole.USER),
                ConversationalMessage(agent_response, MessageRole.ASSISTANT),
            ],
        )
        logger.info("💾 Saved turn to memory (actor=%s session=%s)", actor_id, session_id)
    except Exception as e:
        # Memory failures should never break the main flow
        logger.warning("⚠️  Failed to save turn to memory: %s", e)


def retrieve_context(actor_id: str, query: str, top_k: int = 3) -> str:
    """
    Search long-term memory for relevant past code/solutions.
    Returns a formatted string ready to inject into a prompt, or empty string if nothing found.
    """
    client = _get_client()
    if not client:
        return ""

    try:
        namespace = KNOWLEDGE_NS.format(actorId=actor_id)
        records = client.search_long_term_memories(query=query, namespace_prefix=namespace, top_k=top_k)

        if not records:
            return ""

        lines = ["Relevant context from your previous sessions:"]
        for r in records:
            content = r.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if text:
                lines.append(f"- {text.strip()}")

        context = "\n".join(lines)
        logger.info("🔍 Retrieved %d memory records for actor=%s", len(records), actor_id)
        return context

    except Exception as e:
        logger.warning("⚠️  Memory retrieval failed: %s", e)
        return ""


def _extract_text(content) -> str:
    """Extract readable text from various AgentCore/Strands content formats."""
    import json as _json

    if isinstance(content, str):
        # Check if it's a JSON-encoded message structure
        if content.startswith("{") and '"message"' in content:
            try:
                parsed = _json.loads(content)
                return _extract_text(parsed)
            except _json.JSONDecodeError:
                pass
        return content

    # Handle dict-like objects (dict or EventMessage with .get)
    if hasattr(content, "get"):
        # {"text": "..."} — simple text wrapper
        text_field = content.get("text")
        if text_field is not None:
            return _extract_text(text_field)
        # {"message": {"content": [...]}} — Strands agent message
        message_field = content.get("message")
        if message_field and hasattr(message_field, "get"):
            return _extract_text(message_field.get("content", ""))

    if isinstance(content, list):
        # [{"text": "..."}, ...] — content block array
        parts = []
        for item in content:
            if hasattr(item, "get"):
                text_val = item.get("text")
                if text_val is not None:
                    parts.append(str(text_val))
                elif item.get("toolResult"):
                    tool_content = item["toolResult"].get("content", [])
                    for tc in tool_content:
                        if hasattr(tc, "get") and tc.get("text"):
                            parts.append(tc["text"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)

    return str(content) if content else ""


def _parse_msg(msg) -> dict | None:
    """Parse a single message from AgentCore Memory into {role, content}.

    Handles EventMessage objects, dicts, and string-serialized dicts.
    """
    import ast
    import json as _json

    # If msg is a string, try to parse it as a dict
    if isinstance(msg, str):
        try:
            msg = ast.literal_eval(msg)
        except (ValueError, SyntaxError):
            try:
                msg = _json.loads(msg)
            except _json.JSONDecodeError:
                return None

    # Accept anything with a .get method (dict, EventMessage, etc.)
    if not hasattr(msg, "get"):
        return None

    # Format A: {"message": {"role": "...", "content": [...]}, "message_id": ...}
    message_field = msg.get("message")
    if message_field and hasattr(message_field, "get"):
        role = message_field.get("role", "")
        text = _extract_text(message_field.get("content", ""))
        return {"role": role, "content": text}

    # Format B: {"role": "...", "content": ...} or EventMessage with .role/.content
    role = msg.get("role", "")
    if role:
        text = _extract_text(msg.get("content", ""))
        return {"role": role, "content": text}

    # Format C: {"conversational": {"role": "...", "content": {...}}}
    conv = msg.get("conversational")
    if conv and hasattr(conv, "get"):
        role = conv.get("role", "")
        text = _extract_text(conv.get("content", ""))
        return {"role": role, "content": text}

    return None


def get_session_history(actor_id: str, session_id: str, k: int = 10) -> list:
    """
    Return the last k conversation turns for a session as a list of dicts.
    Used by the /api/memory/history endpoint.
    """
    client = _get_client()
    if not client:
        return []

    try:
        turns = client.get_last_k_turns(actor_id=actor_id, session_id=session_id, k=k)
        logger.info("📋 get_last_k_turns returned %d items, type=%s", len(turns) if turns else 0, type(turns).__name__)
        if turns:
            logger.info("📋 First item type: %s, preview: %s", type(turns[0]).__name__, str(turns[0])[:200])

        result = []
        for turn in turns:
            # turns may be a list of lists (each turn = [user_msg, assistant_msg])
            # or a flat list of messages/EventMessage objects
            if isinstance(turn, (list, tuple)):
                for msg in turn:
                    parsed = _parse_msg(msg)
                    if parsed:
                        result.append(parsed)
            else:
                parsed = _parse_msg(turn)
                if parsed:
                    result.append(parsed)

        logger.info("📋 Parsed %d messages from session history", len(result))
        return result
    except Exception as e:
        logger.warning("⚠️  Failed to get session history from memory: %s", e)
        import traceback

        logger.warning("📋 Traceback: %s", traceback.format_exc())
        return []


def list_actor_sessions(actor_id: str) -> list:
    """List all past sessions for an actor from AgentCore Memory.

    Only returns sessions that still have events (filters out deleted ones).
    Includes the first user message as a preview for context.
    """
    client = _get_client()
    if not client:
        return []
    try:
        sessions = client.list_actor_sessions(actor_id=actor_id)
        result = []
        seen_base_ids = set()
        for s in sessions:
            session_id = s.get("sessionId", "")
            if not session_id:
                continue

            # Deduplicate: strip -generator/-executor suffix to get base session ID
            import re as _re_dedup

            base_id = _re_dedup.sub(r"-(generator|executor)$", "", session_id)
            if base_id in seen_base_ids:
                continue
            seen_base_ids.add(base_id)

            # Check if session still has events (filters out deleted sessions)
            events = client.list_events(actor_id=actor_id, session_id=session_id, max_results=5)
            if not events:
                continue

            # Extract the first natural-language user prompt as preview
            import re as _re

            first_message = ""
            fallback_func = ""
            for event in events:
                for payload_item in event.get("payload", []):
                    conv = payload_item.get("conversational", {})
                    role = conv.get("role", "")
                    raw_content = conv.get("content", "")
                    text = _extract_text(raw_content)
                    if not text:
                        continue

                    if role.upper() == "USER":
                        stripped = text.strip()
                        # Skip non-prompt content
                        if _re.match(
                            r"^(Execute(?:\s+code)?:|```|def |class |import |from |Relevant context)", stripped
                        ):
                            # Extract function name as fallback
                            if not fallback_func:
                                func_match = _re.search(r"def (\w+)\(", text)
                                if func_match:
                                    fallback_func = func_match.group(1)
                            continue
                        if "blocked" in text.lower() or "guardrail" in text.lower():
                            continue
                        # Skip tool results / execution output (starts with numbers, errors, etc.)
                        if _re.match(r"^(\d|Enter |Error:|Initial state|{)", stripped):
                            continue
                        first_message = text[:100]
                        break
                if first_message:
                    break

            if not first_message and fallback_func:
                first_message = f"Code: {fallback_func}()"

            result.append(
                {
                    "session_id": base_id,
                    "created_at": str(s.get("createdAt", "")),
                    "updated_at": str(s.get("updatedAt", "")),
                    "first_message": first_message,
                }
            )
        # Sort by created_at descending (latest first)
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return result
    except Exception as e:
        error_str = str(e)
        if "ResourceNotFoundException" in error_str or "not found" in error_str:
            return []
        logger.warning("⚠️  Failed to list actor sessions: %s", e)
        return []


def delete_session(actor_id: str, session_id: str) -> bool:
    """Delete all events in a session, effectively removing it."""
    client = _get_client()
    if not client:
        return False
    try:
        events = client.list_events(actor_id=actor_id, session_id=session_id)
        if not events:
            return True
        for event in events:
            event_id = event.get("eventId", "")
            if event_id:
                client.delete_event(actor_id=actor_id, session_id=session_id, event_id=event_id)
        logger.info("🗑️  Deleted %d events from session %s", len(events), session_id)
        return True
    except Exception as e:
        logger.warning("⚠️  Failed to delete session: %s", e)
        return False


def is_enabled() -> bool:
    return bool(MEMORY_ID)
