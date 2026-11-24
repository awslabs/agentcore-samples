# Official Python 3.12.16 Bookworm Linux AMD64 manifest (ECS stack platform).
FROM python:3.12-slim-bookworm@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt \
    && useradd --uid 10001 --create-home app
COPY --chown=10001:10001 server.py ./
USER 10001
EXPOSE 8001
CMD ["python", "server.py"]
