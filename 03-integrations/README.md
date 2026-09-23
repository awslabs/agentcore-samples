# 🔌 Amazon Bedrock AgentCore Integrations

Welcome to the integrations section of the Amazon Bedrock AgentCore samples repository!

This folder contains framework and protocol integrations that demonstrate how to connect Amazon Bedrock AgentCore with popular agentic frameworks, identity providers, observability tools, and AWS services.

## 🤖 Agentic Frameworks

* **[ADK](./agentic-frameworks/adk/)**: Agent Development Kit integration with Google Search
* **[AutoGen](./agentic-frameworks/autogen/)**: Multi-agent conversation frameworks
* **[CrewAI](./agentic-frameworks/crewai/)**: Collaborative AI agent orchestration — includes [observability examples](./agentic-frameworks/crewai/observability/)
* **[LangChain](./agentic-frameworks/langchain/)**: Chain-based agent workflows and tool integration
* **[LangGraph](./agentic-frameworks/langgraph/)**: Multi-agent workflows with web search capabilities — includes [observability examples](./agentic-frameworks/langgraph/observability/)
* **[LlamaIndex](./agentic-frameworks/llamaindex/)**: Document processing and retrieval-augmented generation — includes [observability examples](./agentic-frameworks/llamaindex/observability/)
* **[OpenAI Agents](./agentic-frameworks/openai-agents/)**: OpenAI Assistant API integration with handoff patterns
* **[PydanticAI](./agentic-frameworks/pydanticai-agents/)**: Type-safe agent development with Bedrock models
* **[Strands Agents](./agentic-frameworks/strands-agents/)**: Native integration examples with streaming, file system, and OpenAI identity — includes [observability examples](./agentic-frameworks/strands-agents/observability/)

## ☁️ AWS Services

* **[SageMaker AI](./amazon-sagemakerai/)**: MLflow integration with AgentCore Runtime
* **[Bedrock Agent](./bedrock-agent/)**: Integration between Bedrock Agents and AgentCore Gateway

## 🖥️ Agents Hosted Outside Runtime

* **[Agents on AWS Lambda](./agents-hosted-outside-runtime/agents-on-aws-lambda/)**: Running agents on Lambda with AgentCore integration
* **[Agents on EKS](./agents-hosted-outside-runtime/agents-on-eks/)**: Running agents on Elastic Kubernetes Service with AgentCore integration

## 🔐 Identity Providers

* **[EntraID](./IDP-examples/EntraID/)**: Microsoft Entra ID integration with 3LO outbound authentication
* **[Okta](./IDP-examples/Okta/)**: Step-by-step Okta integration for inbound authentication

## ☁️ Nova

* **[Nova Sonic](./nova/nova-sonic/)**: Amazon Nova model integration examples

## 📊 Observability

* **[Arize](./observability/arize/)**: LLM observability and evaluation with Arize Phoenix
* **[Braintrust](./observability/braintrust/)**: AI evaluation and observability platform integration
* **[Datadog](./observability/datadog/)**: Infrastructure and LLM monitoring with Datadog
* **[Dash0](./3p-observability/dash0/)**: OpenTelemetry-native observability platform with traces, metrics, and logs via OTLP HTTP
* **[Dynatrace](./observability/dynatrace/)**: Application performance monitoring integration with travel agent example
* **[Honeycomb](./observability/honeycomb/)**: Distributed tracing and observability with Honeycomb
* **[Instana](./observability/instana/)**: IBM Instana application performance monitoring
* **[Langfuse](./observability/langfuse/)**: Open-source LLM observability and prompt management
* **[OpenLIT](./observability/openlit/)**: OpenTelemetry-based LLM observability with OpenLIT
* **[Simple Dual Observability](./observability/simple-dual-observability/)**: Amazon CloudWatch and Braintrust integration with automatic OpenTelemetry instrumentation for AgentCore Runtime

## 🎨 UX Examples

* **[Streamlit Chat](./ux-examples/streamlit-chat/)**: Interactive chat interface with AgentCore backend integration

## 🚀 Integration Patterns

These integrations demonstrate:

- **Framework Adaptation**: Adapting existing agent frameworks to work with AgentCore
- **Authentication Flow**: Implementing various identity provider integrations
- **Monitoring Setup**: Connecting observability tools for agent performance tracking
- **UI Integration**: Building user interfaces that connect to AgentCore services
- **Service Composition**: Combining multiple AWS services with AgentCore

## 🎯 Who These Integrations Are For

These integrations are perfect for:

- Migrating existing agent applications to AgentCore
- Implementing enterprise authentication patterns
- Setting up production monitoring and observability
- Building custom user interfaces for agent interactions
- Connecting AgentCore with existing AWS infrastructure

## 🔗 Related Resources

- [Workshops](../06-workshops/) - Learn AgentCore fundamentals in Jupyter Notebooks
- [Use Cases](../02-use-cases/) - End-to-end application examples
- [AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
