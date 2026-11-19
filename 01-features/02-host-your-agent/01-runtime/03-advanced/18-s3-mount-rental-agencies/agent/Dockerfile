# linux/arm64 (AgentCore Runtime): docker buildx build --platform linux/arm64 .
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8080
CMD ["python", "app.py"]
