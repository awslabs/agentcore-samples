const API_URL = "http://localhost:5000";

async function fetchTasks() {
    const response = await fetch(`${API_URL}/tasks`);
    const tasks = await response.json();
    renderTasks(tasks);
}

function renderTasks(tasks) {
    const list = document.getElementById("task-list");
    // BUG: uses innerHTML += in a loop — re-parses entire DOM each iteration, loses event listeners
    list.innerHTML = "";

    if (tasks.length === 0) {
        list.innerHTML = '<p style="color:#999; text-align:center;">No tasks yet. Create one above.</p>';
        return;
    }

    tasks.forEach(task => {
        const badgeClass = `badge-${task.status}`;
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `
            <h3>${task.title}</h3>
            <div class="meta">
                <span class="badge ${badgeClass}">${task.status}</span>
                Priority: ${task.priority} | Created: ${formatDate(task.created_at)}
            </div>
            <div class="actions">
                <button class="btn-secondary" onclick="cycleStatus(${task.id}, '${task.status}')">Next Status</button>
                <button class="btn-danger" onclick="deleteTask(${task.id})">Delete</button>
            </div>
        `;
        list.appendChild(card);
    });
}

function formatDate(isoString) {
    // BUG: doesn't handle null/undefined — crashes if created_at is missing
    const date = new Date(isoString);
    return date.toLocaleDateString();
}

function cycleStatus(taskId, currentStatus) {
    // BUG: status cycle is wrong — goes todo -> done, skipping "doing"
    const order = ["todo", "done"];
    const nextIndex = (order.indexOf(currentStatus) + 1) % order.length;
    const nextStatus = order[nextIndex];

    fetch(`${API_URL}/tasks/${taskId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
    }).then(() => fetchTasks());
}

async function deleteTask(taskId) {
    await fetch(`${API_URL}/tasks/${taskId}`, { method: "DELETE" });
    fetchTasks();
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = document.getElementById("title").value.trim();
    const priority = document.getElementById("priority").value;

    if (!title) return;

    const response = await fetch(`${API_URL}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, priority }),
    });

    if (!response.ok) {
        const err = await response.json();
        showError(err.error || "Failed to create task");
        return;
    }

    document.getElementById("title").value = "";
    fetchTasks();
});

function showError(msg) {
    const el = document.getElementById("error");
    el.textContent = msg;
    el.style.display = "block";
    // BUG: error never gets hidden — stays visible forever after first error
}

fetchTasks();
