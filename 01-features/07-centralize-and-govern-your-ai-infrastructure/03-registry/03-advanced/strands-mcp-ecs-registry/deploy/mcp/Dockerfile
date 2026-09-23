FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .

# Port exposed by ECS — internal ALB routes to this
EXPOSE 8080

CMD ["python", "mcp_server.py"]
