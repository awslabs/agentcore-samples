---
name: persistent-notes
description: Save notes locally to /mnt/workspace/notes.json file. Use when user wants to "save a note" or "remember something".
---

# Persistent Notes Skill

Save notes to a local `/mnt/workspace/notes.json` file.

## Usage

```bash
python3 persistent-notes/scripts/note_manager.py "Your note content here"
```

## What it does

- Appends note with timestamp to `notes.json`
- Creates file if it doesn't exist
- Each note includes content and ISO timestamp
- Returns JSON confirmation

## Example

```bash
python3 persistent-notes/scripts/note_manager.py "Deploy to production on Friday"
```

**Output:**
```json
{
  "status": "success",
  "message": "Note saved to notes.json",
  "note": {
    "content": "Deploy to production on Friday",
    "timestamp": "2026-03-19T10:30:00.123456"
  }
}
```

## Notes Storage

Notes are saved to `./notes.json` in the current working directory as a JSON array:

```json
[
  {
    "content": "First note",
    "timestamp": "2026-03-19T10:00:00.000000"
  },
  {
    "content": "Second note",
    "timestamp": "2026-03-19T10:30:00.000000"
  }
]
```
