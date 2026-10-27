FROM public.ecr.aws/amazonlinux/amazonlinux:2023

# xz is required by the Hermes installer to extract its Node.js tarball.
RUN dnf install -y --allowerasing git curl nodejs npm python3 python3-pip jq tar gzip xz unzip which findutils && dnf clean all

RUN npm install -g typescript ts-node

# Non-root user
RUN useradd -m -d /home/agent -u 1000 agent

# Hermes Agent CLI. Install as root so it lands in the FHS layout
# (/usr/local/bin/hermes) — on PATH for every shell, including the interactive
# login shell that run.sh execs into. Installing as the agent user puts it in
# ~/.hermes/bin, which the login shell drops from PATH. Non-interactive flags
# skip the API-key wizard (Bedrock auth comes from the runtime IAM role); no
# `|| true` so a failed install fails the build loudly instead of silently
# shipping a container without the hermes binary.
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \
    | bash -s -- --non-interactive --skip-setup --skip-browser

# The Bedrock provider imports the `anthropic` SDK at runtime (with its boto3
# extra for SigV4-signed Bedrock calls), but the installer doesn't pull it into
# hermes's venv. Add it explicitly so `hermes chat --provider bedrock` can
# initialize. The venv is uv-managed (no pip), so use the managed uv the
# installer dropped in /root/.hermes/bin. anthropic[bedrock] brings boto3/botocore.
RUN /root/.hermes/bin/uv pip install --python /usr/local/lib/hermes-agent/venv/bin/python 'anthropic[bedrock]>=0.39.0'

# boto3 + awscurl for AgentCore MCP tool calls
RUN pip3 install --quiet boto3 awscurl

COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Steering files (project-level instructions for hermes)
RUN mkdir -p /home/agent/.hermes && chown -R agent:agent /home/agent/.hermes
COPY --chown=agent:agent steering/agent.md /home/agent/.hermes/agent.md

RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com" && \
    git config --global --add safe.directory '*' && \
    su - agent -c 'git config --global user.name "AgentCore Bot" && git config --global user.email "agentcore@example.com"'

ENV PATH="/home/agent/.hermes/bin:/home/agent/.local/bin:${PATH}"
ENV HERMES_INFERENCE_PROVIDER=bedrock
ENV HERMES_INFERENCE_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
