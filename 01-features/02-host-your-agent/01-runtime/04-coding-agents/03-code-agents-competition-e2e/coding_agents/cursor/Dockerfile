FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf install -y --allowerasing \
    git curl python3 python3-pip jq tar gzip bzip2 nodejs npm findutils awscli \
    && dnf clean all

# Cursor CLI — official installer
RUN curl https://cursor.com/install -fsS | bash && \
    npm install -g typescript ts-node

# node must be at /usr/local/bin/node (cursor-agent wrapper expects it there)
RUN ln -sf /usr/bin/node /usr/local/bin/node

# Copy entire cursor-agent installation to a world-accessible location
RUN CURSOR_DIR=$(find /root/.local/share/cursor-agent/versions -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1) && \
    if [ -z "$CURSOR_DIR" ]; then \
        echo "ERROR: cursor-agent version directory not found after install" && exit 1; \
    fi && \
    cp -r "$CURSOR_DIR" /opt/cursor-agent && \
    chmod -R a+rX /opt/cursor-agent && \
    chmod 755 /opt/cursor-agent/cursor-agent && \
    ln -sf /opt/cursor-agent/cursor-agent /usr/local/bin/cursor-agent && \
    echo "cursor-agent installed: $(/usr/local/bin/cursor-agent --version 2>&1 || true)"

RUN useradd -m -d /home/agent -u 1000 agent

RUN pip3 install --quiet boto3

COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Project rules (cursor-agent reads .cursor/rules/ from workspace)
RUN mkdir -p /home/agent/.cursor/rules && chown -R agent:agent /home/agent/.cursor
COPY --chown=agent:agent rules/agent.mdc /home/agent/.cursor/rules/agent.mdc

RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com" && \
    git config --global --add safe.directory '*' && \
    su - agent -c 'git config --global user.name "AgentCore Bot" && git config --global user.email "agentcore@example.com"'

WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
