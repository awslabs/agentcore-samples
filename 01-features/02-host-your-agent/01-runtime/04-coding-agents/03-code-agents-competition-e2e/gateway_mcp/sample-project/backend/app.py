"""Task Manager API — a simple CRUD backend with intentional bugs."""

from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# In-memory storage
tasks = []
next_id = 1


@app.route("/tasks", methods=["GET"])
def list_tasks():
    """List all tasks, optionally filtered by status."""
    status = request.args.get("status")
    if status:
        # BUG: comparison is case-sensitive — "Done" vs "done" won't match
        filtered = [t for t in tasks if t["status"] == status]
        return jsonify(filtered)
    return jsonify(tasks)


@app.route("/tasks", methods=["POST"])
def create_task():
    """Create a new task."""
    global next_id
    data = request.get_json()

    if not data or not data.get("title"):
        return jsonify({"error": "title is required"}), 400

    task = {
        "id": next_id,
        "title": data["title"],
        "description": data.get("description", ""),
        "status": "todo",
        "priority": data.get("priority", "medium"),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    # BUG: next_id is never incremented — all tasks get id=1
    tasks.append(task)
    return jsonify(task), 201


@app.route("/tasks/<int:task_id>", methods=["GET"])
def get_task(task_id):
    """Get a single task by ID."""
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


@app.route("/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    """Update a task."""
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return jsonify({"error": "task not found"}), 404

    data = request.get_json()

    # BUG: allows setting status to any arbitrary string — no validation
    if "title" in data:
        task["title"] = data["title"]
    if "description" in data:
        task["description"] = data["description"]
    if "status" in data:
        task["status"] = data["status"]
    if "priority" in data:
        task["priority"] = data["priority"]

    # BUG: updated_at is not refreshed on update
    return jsonify(task)


@app.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    """Delete a task."""
    global tasks
    original_len = len(tasks)
    # BUG: filter logic is inverted — keeps the task and removes everything else
    tasks = [t for t in tasks if t["id"] == task_id]

    if len(tasks) == original_len:
        return jsonify({"error": "task not found"}), 404

    return "", 204


@app.route("/tasks/stats", methods=["GET"])
def task_stats():
    """Return task statistics."""
    total = len(tasks)
    by_status = {}
    for t in tasks:
        s = t["status"]
        # BUG: counter never increments properly — always sets to 1
        by_status[s] = 1

    return jsonify({"total": total, "by_status": by_status})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
