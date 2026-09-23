# Stage 1: Build
FROM --platform=linux/arm64 public.ecr.aws/docker/library/node:20-alpine AS build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy source code
COPY tsconfig.json ./
COPY src ./src

# Build TypeScript
RUN npm install typescript && npm run build

# Stage 2: Runtime
FROM --platform=linux/arm64 public.ecr.aws/docker/library/node:20-alpine

WORKDIR /app

# Copy package files and install production dependencies only
COPY package*.json ./
RUN npm ci --only=production

# Copy built application from build stage
COPY --from=build /app/dist ./dist

# Amazon Bedrock AgentCore Runtime requires:
# - Host: 0.0.0.0
# - Port: 8080
# - Platform: ARM64
ENV PORT=8080
ENV NODE_ENV=production

EXPOSE 8080

# Create non-root user and transfer ownership
RUN addgroup -S appgroup && adduser -S -u 1001 appuser -G appgroup && chown -R appuser:appgroup /app
USER appuser

# Run the application
CMD ["node", "dist/index.js"]
