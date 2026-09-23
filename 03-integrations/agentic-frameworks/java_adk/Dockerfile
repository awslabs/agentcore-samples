# Stage 1: Build the application
# Using Amazon ECR Public Gallery to avoid Docker Hub rate limits
FROM --platform=linux/arm64 public.ecr.aws/docker/library/maven:3.9-amazoncorretto-17 AS build

WORKDIR /app

# Copy pom.xml and download dependencies (cached layer)
COPY pom.xml .
RUN mvn dependency:go-offline -B

# Copy source code and build
COPY src ./src
RUN mvn clean package -DskipTests

# Stage 2: Create the runtime image
# Using Amazon ECR Public Gallery to avoid Docker Hub rate limits
FROM --platform=linux/arm64 public.ecr.aws/amazoncorretto/amazoncorretto:17-al2023

WORKDIR /app

# Copy the built JAR from the build stage
COPY --from=build /app/target/*.jar app.jar

# Amazon Bedrock AgentCore Runtime requires:
# - Host: 0.0.0.0
# - Port: 8080
# - Platform: ARM64
EXPOSE 8080

# Create non-root user and transfer ownership
RUN useradd -r -m -u 1001 appuser && chown -R appuser /app
USER appuser

# Run the application
ENTRYPOINT ["java", "-jar", "app.jar"]
