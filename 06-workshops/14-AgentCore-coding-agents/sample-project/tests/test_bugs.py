"""
Regression tests — one per seeded bug.
Each test FAILS against the buggy code and PASSES once the bug is fixed.
Run with: pytest tests/test_bugs.py -v
"""

import time
import pytest
from app import app as flask_app


@pytest.fixture(autouse=True)
def reset_state():
    """Reset in-memory state between tests."""
    import app as m
    m.tasks.clear()
    m.next_id = 1
    yield
    m.tasks.clear()
    m.next_id = 1


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


# ── Bug 1: POST /tasks always assigns id=1 ────────────────────────────────────
def test_tasks_get_unique_ids(client):
    r1 = client.post("/tasks", json={"title": "First"})
    r2 = client.post("/tasks", json={"title": "Second"})
    r3 = client.post("/tasks", json={"title": "Third"})
    ids = [r1.get_json()["id"], r2.get_json()["id"], r3.get_json()["id"]]
    assert len(set(ids)) == 3, f"Expected 3 unique IDs, got {ids}"


# ── Bug 2: DELETE /tasks/:id removes all tasks except the target ───────────────
def test_delete_removes_only_target(client):
    ids = []
    for title in ("A", "B", "C"):
        ids.append(client.post("/tasks", json={"title": title}).get_json()["id"])

    assert client.delete(f"/tasks/{ids[1]}").status_code == 204

    remaining_ids = [t["id"] for t in client.get("/tasks").get_json()]
    assert ids[0] in remaining_ids, "Task A was wrongly deleted"
    assert ids[1] not in remaining_ids, "Deleted task B still present"
    assert ids[2] in remaining_ids, "Task C was wrongly deleted"


# ── Bug 3: GET /tasks/stats always reports count of 1 per status ──────────────
def test_stats_counts_correctly(client):
    for _ in range(3):
        client.post("/tasks", json={"title": "task"})

    all_tasks = client.get("/tasks").get_json()
    for t in all_tasks[:2]:
        client.put(f"/tasks/{t['id']}", json={"status": "done"})

    stats = client.get("/tasks/stats").get_json()["by_status"]
    assert stats.get("todo") == 1, f"Expected todo=1, got {stats.get('todo')}"
    assert stats.get("done") == 2, f"Expected done=2, got {stats.get('done')}"


# ── Bug 4: PUT /tasks/:id does not update updated_at ─────────────────────────
def test_update_refreshes_updated_at(client):
    task_id = client.post("/tasks", json={"title": "My task"}).get_json()["id"]
    original_ts = client.get(f"/tasks/{task_id}").get_json()["updated_at"]

    time.sleep(0.05)  # ensure clock advances
    client.put(f"/tasks/{task_id}", json={"title": "Renamed"})
    new_ts = client.get(f"/tasks/{task_id}").get_json()["updated_at"]

    assert new_ts != original_ts, (
        f"updated_at was not refreshed after PUT (still {new_ts!r})"
    )


# ── Bug 5: GET /tasks?status= filter is case-sensitive ───────────────────────
def test_status_filter_is_case_insensitive(client):
    task_id = client.post("/tasks", json={"title": "My task"}).get_json()["id"]
    client.put(f"/tasks/{task_id}", json={"status": "done"})

    # Querying with different casing should still return the task
    results = client.get("/tasks?status=Done").get_json()
    assert len(results) == 1, (
        f"Expected 1 result for status=Done, got {len(results)}"
    )
    results_upper = client.get("/tasks?status=DONE").get_json()
    assert len(results_upper) == 1, (
        f"Expected 1 result for status=DONE, got {len(results_upper)}"
    )
