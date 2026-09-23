FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# AgentCore Runtime listens on 8080
EXPOSE 8080

# Entrypoint
CMD ["python", "backend/agent_runtime.py"]
