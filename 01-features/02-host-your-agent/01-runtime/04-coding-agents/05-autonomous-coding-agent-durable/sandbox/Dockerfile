# Sandbox — AgentCore Runtime data plane (ARM64-only). NOT an agent.
# A command executor with persistent workspace for installed dependencies.
#
# Security: non-root execution (via entrypoint), pinned deps, HEALTHCHECK,
# minimal attack surface (no curl in final image).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Build/runtime toolchain for code execution + dependency installs.
# Note: curl excluded from final image to reduce exfiltration surface.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py policy_engine.py entrypoint.sh .
COPY policies/ /app/policies/

# Copy shared security module (from repo root's shared/ directory)
COPY shared_libs/shared/ /app/shared_libs/shared/

ENV MOUNT_PATH=/mnt/shared \
    WORKSPACE_PATH=/mnt/workspace \
    HOME=/mnt/workspace

RUN useradd -m -u 1000 -d /home/sbx sbx \
    && chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

EXPOSE 8080
# entrypoint.sh ensures /mnt/workspace is writable by sbx before starting the app
CMD ["/app/entrypoint.sh"]
