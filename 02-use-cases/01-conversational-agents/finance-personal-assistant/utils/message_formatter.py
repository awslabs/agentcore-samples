"""
Utility functions for formatting and displaying agent messages in a readable way.
"""


def pretty_print_messages(messages, max_content_length=500, show_indices=True):
    """
    Pretty print agent messages with formatted output.

    Args:
        messages: List of message objects from agent.messages
        max_content_length: Maximum length of content to display (default: 500)
        show_indices: Whether to show message indices (default: True)
    """
    if not messages:
        print("📭 No messages in conversation history")
        return

    print(f"💬 CONVERSATION HISTORY ({len(messages)} messages)")
    print("=" * 80)

    for i, message in enumerate(messages):
        role = message.get("role", "unknown").upper()
        content = message.get("content", [])

        # Format role with emoji
        role_emoji = "👤" if role == "USER" else "🤖" if role == "ASSISTANT" else "⚙️"

        if show_indices:
            print(f"\n{role_emoji} MESSAGE {i + 1} ({role}):")
        else:
            print(f"\n{role_emoji} {role}:")

        print("-" * 40)

        # Handle content (which is typically a list of content blocks)
        if isinstance(content, list):
            for j, content_block in enumerate(content):
                if isinstance(content_block, dict):
                    # Handle text content blocks
                    if "text" in content_block:
                        text = content_block["text"]

                        # Truncate long content
                        if len(text) > max_content_length:
                            text = text[:max_content_length] + "\n... [content truncated]"

                        if len(content) > 1:
                            print(f"  Content Block {j + 1}:")

                        # Format text with proper indentation
                        formatted_text = "\n".join(["  " + line for line in text.split("\n")])
                        print(formatted_text)

                    # Handle other content types (images, etc.)
                    elif "type" in content_block:
                        print(f"  📎 Content Type: {content_block['type']}")
                        if "source" in content_block:
                            print(f"     Source: {content_block.get('source', {}).get('type', 'unknown')}")
                else:
                    # Handle simple string content
                    print(f"  {content_block}")
        else:
            # Handle direct string content
            text = str(content)
            if len(text) > max_content_length:
                text = text[:max_content_length] + "\n... [content truncated]"
            formatted_text = "\n".join(["  " + line for line in text.split("\n")])
            print(formatted_text)

    print("\n" + "=" * 80)
    print(f"📊 SUMMARY: {len(messages)} total messages")

    # Count messages by role
    role_counts = {}
    for message in messages:
        role = message.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1

    for role, count in role_counts.items():
        print(f"   • {role.capitalize()}: {count} messages")


def print_conversation_stats(messages):
    """
    Print detailed statistics about the conversation.

    Args:
        messages: List of message objects from agent.messages
    """
    if not messages:
        print("📭 No conversation data to analyze")
        return

    print("📈 CONVERSATION STATISTICS")
    print("=" * 50)

    total_messages = len(messages)
    user_messages = sum(1 for msg in messages if msg.get("role") == "user")
    assistant_messages = sum(1 for msg in messages if msg.get("role") == "assistant")

    # Calculate content lengths
    total_chars = 0
    content_blocks = 0

    for message in messages:
        content = message.get("content", [])
        if isinstance(content, list):
            content_blocks += len(content)
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total_chars += len(block["text"])
        else:
            total_chars += len(str(content))

    print(f"📊 Messages: {total_messages}")
    print(f"   • User: {user_messages}")
    print(f"   • Assistant: {assistant_messages}")
    print(f"📝 Content blocks: {content_blocks}")
    print(f"📏 Total characters: {total_chars:,}")
    print(f"📐 Average chars per message: {total_chars // total_messages if total_messages > 0 else 0}")


def print_last_exchange(messages, num_pairs=1):
    """
    Print only the last N message pairs (user + assistant).

    Args:
        messages: List of message objects from agent.messages
        num_pairs: Number of message pairs to show (default: 1)
    """
    if not messages:
        print("📭 No messages to display")
        return

    # Find the last N pairs
    pairs_found = 0
    start_index = len(messages)

    # Work backwards to find message pairs
    i = len(messages) - 1
    while i >= 0 and pairs_found < num_pairs:
        if messages[i].get("role") == "assistant" and i > 0 and messages[i - 1].get("role") == "user":
            pairs_found += 1
            if pairs_found == num_pairs:
                start_index = i - 1
        i -= 1

    recent_messages = messages[start_index:]

    print(f"🔄 LAST {pairs_found} MESSAGE PAIR{'S' if pairs_found != 1 else ''}")
    pretty_print_messages(recent_messages, show_indices=False)
