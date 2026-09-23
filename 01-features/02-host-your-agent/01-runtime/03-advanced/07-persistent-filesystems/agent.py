"""
Persistent filesystem agent — reads and writes notes to /mnt/data.

When deployed with filesystemConfigurations, files written to /mnt/data
persist across session stop/resume cycles. This agent maintains a
JSON-based notes file that survives session restarts.
"""

import json
import os
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

STORAGE_PATH = "/mnt/data"
NOTES_FILE = f"{STORAGE_PATH}/notes.json"


def _load_notes() -> list[dict]:
    """Load notes from persistent storage."""
    try:
        with open(NOTES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_notes(notes: list[dict]):
    """Save notes to persistent storage."""
    os.makedirs(STORAGE_PATH, exist_ok=True)
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=2)


@tool
def add_note(content: str) -> str:
    """Add a new note to persistent storage.

    Args:
        content: The note text to save.

    Returns:
        Confirmation message with the note ID.
    """
    notes = _load_notes()
    note = {
        "id": len(notes) + 1,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notes.append(note)
    _save_notes(notes)
    return f"Note #{note['id']} saved: '{content}'"


@tool
def list_notes() -> str:
    """List all saved notes from persistent storage.

    Returns:
        JSON string with all notes, or a message if no notes exist.
    """
    notes = _load_notes()
    if not notes:
        return "No notes found. Use add_note to create one."
    return json.dumps(notes, indent=2)


@tool
def delete_note(note_id: int) -> str:
    """Delete a note by its ID.

    Args:
        note_id: The ID of the note to delete.

    Returns:
        Confirmation message.
    """
    notes = _load_notes()
    original_count = len(notes)
    notes = [n for n in notes if n["id"] != note_id]
    if len(notes) == original_count:
        return f"Note #{note_id} not found."
    _save_notes(notes)
    return f"Note #{note_id} deleted."


model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[add_note, list_notes, delete_note],
    system_prompt=(
        "You are a note-taking assistant. You can add, list, and delete notes. "
        "Notes are stored in persistent storage at /mnt/data and survive session restarts."
    ),
)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke_agent(payload: dict) -> str:
    prompt = payload.get("prompt", "Hello!")
    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
