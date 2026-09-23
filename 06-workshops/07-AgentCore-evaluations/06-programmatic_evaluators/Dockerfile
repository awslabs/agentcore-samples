FROM python:3.12-slim

WORKDIR /app

# Install agent runtime dependencies (no starter toolkit needed)
RUN pip install --no-cache-dir \
    "bedrock-agentcore>=1.6.0" \
    "boto3>=1.42.0" \
    "strands-agents" \
    "strands-agents-tools" \
    "aws-opentelemetry-distro"

COPY hr_assistant_agent.py .

EXPOSE 8080

CMD ["python", "hr_assistant_agent.py"]
