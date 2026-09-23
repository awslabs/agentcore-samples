FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py streamable_http_sigv4.py ./

# Create skills staging directory
RUN mkdir -p /tmp/loaded_skills

EXPOSE 8080

CMD ["python", "agent.py"]
