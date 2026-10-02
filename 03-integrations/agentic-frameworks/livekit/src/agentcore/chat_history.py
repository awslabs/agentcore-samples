import json
from datetime import datetime
from typing import Any


class ChatMessage:
    def __init__(self, role: str, content: str, timestamp: float | None = None):
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now().timestamp()
        self.type = "text"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }


class ChatHistory:
    def __init__(self):
        self.messages: list[ChatMessage] = []

    def add_message(self, role: str, content: str) -> ChatMessage:
        message = ChatMessage(role, content)
        self.messages.append(message)
        return message

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {"messages": [msg.to_dict() for msg in self.messages]}

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
