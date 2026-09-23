#!/usr/bin/env python3
"""Simple note saver - saves notes to local notes.json file."""

import json
import sys
from datetime import datetime
from pathlib import Path

# Save notes in current directory
NOTES_FILE = Path("/mnt/workspace/notes.json")


def save_note(content: str) -> None:
    """Save a note to notes.json."""
    # Load existing notes
    notes = []
    if NOTES_FILE.exists():
        try:
            with open(NOTES_FILE, "r") as f:
                notes = json.load(f)
        except json.JSONDecodeError:
            notes = []

    # Add new note
    note = {"content": content, "timestamp": datetime.now().isoformat()}
    notes.append(note)

    # Save back to file
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=2)

    print(
        json.dumps(
            {
                "status": "success",
                "message": f"Note saved to {NOTES_FILE}",
                "note": note,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 note_manager.py <note_content>")
        sys.exit(1)

    note_content = " ".join(sys.argv[1:])
    save_note(note_content)
