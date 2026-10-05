# Fake AgentCore Runtime data plane for the local stack. Built as devbox-fake-agentcore:dev by compose.yaml.
FROM public.ecr.aws/docker/library/node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY server.mjs ./
COPY lib ./lib
USER node
ENV FAKE_AC_HOST=0.0.0.0 FAKE_AC_PORT=9401
EXPOSE 9401
CMD ["node", "server.mjs"]
