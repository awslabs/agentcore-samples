# Third-Party Observability Integration

This section demonstrates how to integrate Amazon Bedrock AgentCore Runtime hosted agents with  third-party observability platforms. Learn to leverage specialized monitoring tools while maintaining the benefits of AgentCore Runtime.

## Available Integrations

The publish folder contains:
- A Jupyter notebook demonstrating AgentCore runtime with various observability solutions
- A requirements.txt file listing necessary dependencies

### Supported Platforms

- **Arize**: AI and Agent engineering platform
- **Braintrust**: AI evaluation and monitoring platform
- **Datadog**: Unified observability platform for monitoring, APM, logs, and traces
- **Honeycomb**: Observability platform built for high-cardinality exploration
- **Instana**: Real-Time APM and Observability Platform
- **Langfuse**: LLM observability and analytics
- **OpenLIT**: Open-source observability platform for LLM applications

## Getting Started

1. Choose your observability platform
2. Create an account on the respective platform
3. Obtain API keys and configuration details
4. Install requirements: `pip install -r requirements.txt`
5. Configure environment variables in the notebook
6. Deploy your agent to AgentCore Runtime
7. Run the notebook to see integrated observability


## Framework Support

Amazon Bedrock AgentCore supports any agentic framework and model of your choice:
- CrewAI
- LangGraph
- LlamaIndex
- Strands Agents

### Strands Agents
[Strands](https://strandsagents.com/latest/) provides built-in telemetry support, making it ideal for demonstrating third-party integrations.

## Configuration Requirements

Each platform requires specific configuration:

### Arize
- API key and Space ID from Arize dashboard
- Project configuration

### Braintrust
- API key from Braintrust dashboard
- Project configuration

### Datadog
- API key from Datadog dashboard (Organization Settings → API Keys)
- Datadog site/region (US1, US3, US5, EU1, AP1) — determines the OTLP endpoint
- Uses Strands built-in telemetry with OTLP export directly to Datadog (no Datadog Agent required)

### Instana
- Instana key
- Project configuration

### Langfuse
- Public and secret keys
- Project configuration

### OpenLIT
- OpenLIT deployment (self-hosted or cloud)
- OTLP endpoint configuration

## Cleanup

After completing examples:
1. Delete AgentCore Runtime deployments
2. Remove ECR repositories
3. Clean up platform-specific resources
4. Revoke API keys if no longer needed

## Additional Resources

- [Arize Documentation](https://arize.com/docs/ax)
- [Braintrust Documentation](https://www.braintrust.dev/docs)
- [Datadog Documentation](https://docs.datadoghq.com/)
- [Datadog LLM Observability](https://docs.datadoghq.com/llm_observability/)
- [Datadog OpenTelemetry](https://docs.datadoghq.com/opentelemetry/)
- [Instana Documentation](https://www.ibm.com/docs/en/instana-observability/1.0.308?topic=overview)
- [Langfuse Documentation](https://langfuse.com/docs)
- [OpenLIT Documentation](https://docs.openlit.io/)
- [AgentCore Runtime Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/userguide/runtime.html)
