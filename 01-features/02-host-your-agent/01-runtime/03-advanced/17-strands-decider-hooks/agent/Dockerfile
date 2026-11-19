# syntax=docker/dockerfile:1
# linux/arm64 only (matches AgentCore Runtime). Built and pushed by ../infra/deploy.sh.
FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app
# Fixed cache path, so the build-time download below and the runtime (which
# runs as a non-root user) use the same place.
ENV HF_HOME=/app/.cache/huggingface
# BedrockAgentCoreApp.run() binds 0.0.0.0 only with /.dockerenv or this set;
# under containerd there's no /.dockerenv.
ENV DOCKER_CONTAINER=1

COPY requirements.txt .
# torch from the CPU-only index first: the default arm64 wheel pulls ~4 GB of
# CUDA libraries that can't run without a GPU.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 && \
    pip install --no-cache-dir -r requirements.txt

# The user and cache dir exist before the download, so the weights are written
# already owned by it. A `chown -R` afterwards would copy the multi-GB cache
# into a second layer.
RUN useradd -m -u 1000 bedrock_agentcore && mkdir -p "${HF_HOME}" && chown -R bedrock_agentcore:bedrock_agentcore /app
USER bedrock_agentcore

# Both models are pinned to a commit, so a rebuild can't pick up different
# weights: the decider checkpoint into /app/model, and the base model it is
# built on at the revision the checkpoint's provenance.json records.
RUN python3 -c "from huggingface_hub import snapshot_download as get; \
get('StrandsAgents/strands-decider-2B-qwen3.5-v1-2610', revision='216d1ccb46a9e9b036eb9187b248db98480f77d4', local_dir='/app/model'); \
get('Qwen/Qwen3.5-2B-Base', revision='b1485b2fa6dfa1287294f269f5fb618e03d52d7c')"

# From here on, nothing contacts the Hugging Face Hub: not this build, not the runtime.
ENV HF_HUB_OFFLINE=1

# Fails the build if the pinned files don't load offline. Loads in bf16 like
# app.py does: the library's fp32 upcast needs ~7.5 GiB, more than a typical
# Docker Desktop VM has.
RUN python3 -c "from strands_decider.infer import SystemOneEngine, load_engine; SystemOneEngine._upcast_torso_for_cpu = lambda self: None; load_engine('/app/model', device='cpu'); print('decider checkpoint loads offline')"

COPY --chown=bedrock_agentcore:bedrock_agentcore app.py .

EXPOSE 8080
CMD ["python3", "app.py"]
