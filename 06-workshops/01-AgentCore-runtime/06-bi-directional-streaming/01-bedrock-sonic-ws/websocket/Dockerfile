FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy agent file
COPY s2s_events.py ./
COPY s2s_session_manager.py ./
COPY server.py ./

# Expose port
EXPOSE 8080
EXPOSE 8081

ENV PORT=8080
ENV LOGLEVEL=INFO

# Run application directly
CMD ["python", "server.py"]
