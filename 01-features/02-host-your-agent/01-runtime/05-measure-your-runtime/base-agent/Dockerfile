# syntax=docker/dockerfile:1
#
# AgentCore Runtime container image (0_public benchmark: prod only, no
# Lambda MicroVM variant). Built with `docker buildx build --platform
# linux/arm64` (see infrastructure/build-and-push.sh) and pushed to ECR.
#
# Base image: plain python:3.13-slim, not the uv/bookworm-slim image the
# original benchmark used. The uv-based image bundles its own compiled
# Python + the uv binary on top of Debian, which alone measured ~270 MB
# locally; python:3.13-slim measured ~215 MB for the same Python version.
# Every extra megabyte here is padding we control on purpose (see PAD_MB
# below), not padding we didn't ask for.
FROM python:3.13-slim

WORKDIR /app
# No .pyc files, no pip cache: this is a throwaway image, not a dev environment.
ENV PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
# --no-compile: skip .pyc generation. pip is uninstalled after install because
# nothing at runtime needs it (uvicorn's entry-point script survives fine; it's
# a plain shebang, not a pip wrapper) and pip itself is ~12 MB of site-packages.
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip /root/.cache

# Copy the agent source.
COPY agent.py ./

# --- Size padding for the container-size comparison ---
# PAD_MB=0 (default) is this baseline, unpadded image. build-and-push.sh
# passes a non-zero PAD_MB per IMAGE_SIZE label (500mb/750mb/1gb/2gb) to grow
# the image to roughly that total. /dev/urandom, not /dev/zero: registries
# store layers gzip-compressed, and all-zero padding would compress away to
# almost nothing, defeating the point of testing pull/unpack cost at size.
ARG PAD_MB=0
RUN if [ "${PAD_MB}" -gt 0 ]; then \
      dd if=/dev/urandom of=/app/.size-padding bs=1M count="${PAD_MB}" status=none; \
    fi

EXPOSE 8080
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
