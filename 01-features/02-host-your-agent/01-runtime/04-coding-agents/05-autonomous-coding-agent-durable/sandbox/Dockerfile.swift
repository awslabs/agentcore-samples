# Sandbox (Swift toolchain) — AgentCore Runtime data plane (ARM64-only). NOT an agent.
# Same command-executor contract as the Python sandbox, but carries the Swift
# toolchain so it can `swift build` / `swift test` repo code. One image per language;
# the business-logic service picks which sandbox runtime to invoke per ticket.
#
# Security: non-root execution (via entrypoint), pinned Python deps, HEALTHCHECK.
FROM --platform=linux/arm64 swift:6.1-jammy
#checkov:skip=CKV_DOCKER_3:Non-root execution handled by entrypoint.sh via su

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SANDBOX_LANG=swift

# The Swift image is Ubuntu-based; add Python 3 to run the same app.py executor.
# git + ca-certificates let SwiftPM resolve package dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# swift:6.1-jammy ships Python 3.10 with an older pip (no PEP-668 / --break-system-packages).
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py policy_engine.py entrypoint.sh .
COPY policies/ /app/policies/

# Copy shared security module (from repo root's shared/ directory)
COPY shared_libs/shared/ /app/shared_libs/shared/

ENV MOUNT_PATH=/mnt/shared \
    WORKSPACE_PATH=/mnt/workspace \
    HOME=/mnt/workspace \
    SWIFTPM_CACHE_DIR=/mnt/workspace/.spm-cache

# git refuses to operate on dependency checkouts under .build when they are owned by a
# different uid than the runner ("detected dubious ownership"). This also affects the
# test gate, which runs `swift test` via InvokeAgentRuntimeCommand (a plain shell, not our
# _run_command path), so the setting must live in the image, applied to ALL users.
RUN git config --system --add safe.directory '*'

RUN useradd -m -u 1000 -d /home/sbx sbx \
    && chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

EXPOSE 8080
# entrypoint.sh ensures /mnt/workspace is writable by sbx before starting the app
CMD ["/app/entrypoint.sh"]
