"""Dependency injection container."""

import json
from dependency_injector import containers, providers

from cx_agent_backend.domain.services.conversation_service import ConversationService
from cx_agent_backend.infrastructure.adapters.memory_conversation_repository import (
    MemoryConversationRepository,
)
from cx_agent_backend.infrastructure.adapters.langgraph_agent_service import (
    LangGraphAgentService,
)
from cx_agent_backend.infrastructure.adapters.bedrock_guardrail_service import (
    BedrockGuardrailService,
)
from cx_agent_backend.infrastructure.adapters.openai_llm_service import OpenAILLMService
from cx_agent_backend.infrastructure.config.settings import settings
from cx_agent_backend.infrastructure.aws.secret_reader import AWSSecretsReader
from cx_agent_backend.infrastructure.aws.parameter_store_reader import (
    AWSParameterStoreReader,
)


class Container(containers.DeclarativeContainer):
    """Dependency injection container."""

    # Configuration
    config = providers.Configuration()
    secret_reader = AWSSecretsReader()
    parameter_store_reader = AWSParameterStoreReader()

    gateway_secret = json.loads(secret_reader.read_secret("gateway_credentials"))

    # Repositories
    conversation_repository = providers.Singleton(MemoryConversationRepository)

    # Services
    guardrail_service = (
        providers.Singleton(
            BedrockGuardrailService,
            guardrail_id=parameter_store_reader.get_parameter(
                "/amazon/guardrail_id", decrypt=True
            ),
            region=settings.aws_region,
        )
        if settings.guardrails_enabled
        else providers.Object(None)
    )

    llm_service = providers.Singleton(
        OpenAILLMService,
        api_key=gateway_secret["api_key"],
        base_url=gateway_secret["gateway_url"],
        model=settings.default_model,
    )

    agent_service = providers.Singleton(
        LangGraphAgentService,
        guardrail_service=guardrail_service,
        llm_service=llm_service,
    )

    conversation_service = providers.Factory(
        ConversationService,
        conversation_repo=conversation_repository,
        agent_service=agent_service,
        guardrail_service=guardrail_service,
    )
