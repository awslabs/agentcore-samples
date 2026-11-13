FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
RUN useradd -m -u 1000 agent && chown -R agent /app
USER agent

EXPOSE 8080
CMD ["python", "app.py"]