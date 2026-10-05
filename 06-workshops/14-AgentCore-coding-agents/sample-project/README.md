# Task Manager

A simple task manager app with a Python/Flask backend and vanilla HTML/JS frontend.

## Running Locally

### Backend

```bash
cd backend
pip install -r requirements.txt
python app.py
```

The API runs at `http://localhost:5000`.

### Frontend

```bash
cd frontend
python -m http.server 8080
```

Open `http://localhost:8080` in your browser.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/tasks` | List all tasks (optional `?status=` filter) |
| POST | `/tasks` | Create a task (`{"title": "..."}`) |
| GET | `/tasks/:id` | Get a task by ID |
| PUT | `/tasks/:id` | Update a task |
| DELETE | `/tasks/:id` | Delete a task |
| GET | `/tasks/stats` | Task counts by status |
