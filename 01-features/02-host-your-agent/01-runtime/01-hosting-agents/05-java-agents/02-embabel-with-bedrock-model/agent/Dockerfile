# Build stage
FROM amazoncorretto:21-al2023 AS builder
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN yum install -y maven && mvn clean package -DskipTests

# Runtime stage — Ubuntu for Playwright/Chromium support
FROM ubuntu:24.04
WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npx playwright install --with-deps chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 agent && useradd -u 1000 -g agent -m agent
USER agent

COPY --from=builder /app/target/*.jar app.jar

ENV PLAYWRIGHT_NODEJS_PATH=/usr/bin/node
ENV AGENTCORE_BROWSER_MODE=agentcore

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
