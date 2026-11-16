# syntax=docker/dockerfile:1
# linux/arm64 only: docker buildx build --platform linux/arm64 -t <ecr-uri>:fuse-example --push .
#
# USER 0 (root) is required for mount(2); see ../README.md for why
# that's safe here. mount-s3 is baked in at build time because the
# runtime denies in-container egress to its release endpoint.
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      wget fuse3 ca-certificates bash && \
    wget -q https://s3.amazonaws.com/mountpoint-s3-release/latest/arm64/mount-s3.deb && \
    apt-get install -y ./mount-s3.deb && rm mount-s3.deb && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g @strands-agents/cli@0.2.2

WORKDIR /app
COPY server.js .

ENV HOME=/root
USER 0
EXPOSE 8080
CMD ["node", "server.js"]
