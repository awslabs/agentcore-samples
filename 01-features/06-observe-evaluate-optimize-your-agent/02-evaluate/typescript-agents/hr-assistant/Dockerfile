# ---------------------------------------------------------------------------
# Stage 1: build TypeScript → JavaScript
# ---------------------------------------------------------------------------
FROM node:20-slim AS builder

WORKDIR /build
COPY package*.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: lean production image
# ---------------------------------------------------------------------------
FROM node:20-slim AS runtime

WORKDIR /app

# Install production dependencies only
COPY package*.json ./
RUN npm ci --omit=dev

# Copy compiled output from builder
COPY --from=builder /build/dist ./dist

# node:20-slim ships with a 'node' user (UID/GID 1000)
RUN chown -R node:node /app
USER node

EXPOSE 8080

CMD ["node", "dist/agent.js"]
