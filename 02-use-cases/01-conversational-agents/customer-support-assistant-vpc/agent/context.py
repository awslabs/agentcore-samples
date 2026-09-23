from contextvars import ContextVar
from strands import Agent
from strands.tools.mcp.mcp_client import MCPClient
from typing import Optional


class CustomerSupportContext:
    """Context Manager for Customer Support Assistant"""

    # Global state for tokens that persist across agent calls
    _mcp_token: Optional[str] = None
    _gateway_token: Optional[str] = None
    _agent: Optional[Agent] = None
    _mcp_client: Optional[MCPClient] = None
    _gateway_client: Optional[MCPClient] = None
    _aurora_mcp_client: Optional[MCPClient] = None

    # Context variables for application state
    _mcp_token_ctx: ContextVar[Optional[str]] = ContextVar("mcp_token", default=None)
    _gateway_token_ctx: ContextVar[Optional[str]] = ContextVar("gateway_token", default=None)
    _agent_ctx: ContextVar[Optional[Agent]] = ContextVar("agent", default=None)
    _mcp_client_ctx: ContextVar[Optional[MCPClient]] = ContextVar("mcp_client", default=None)
    _gateway_client_ctx: ContextVar[Optional[MCPClient]] = ContextVar("gateway_client", default=None)
    _aurora_mcp_client_ctx: ContextVar[Optional[MCPClient]] = ContextVar("aurora_client", default=None)

    @classmethod
    def get_mcp_token_ctx(
        cls,
    ) -> Optional[str]:
        # First try to get from global state for persistence across calls
        if cls._mcp_token:
            return cls._mcp_token
        try:
            return cls._mcp_token_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_mcp_token_ctx(cls, token: str) -> None:
        # Set both global state and context variable
        cls._mcp_token = token
        cls._mcp_token_ctx.set(token)

    @classmethod
    def get_aurora_mcp_client_ctx(
        cls,
    ) -> Optional[MCPClient]:
        # First try to get from global state for persistence across calls
        if cls._aurora_mcp_client:
            return cls._aurora_mcp_client
        try:
            return cls._aurora_mcp_client_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_aurora_mcp_client_ctx(cls, client: MCPClient) -> None:
        # Set both global state and context variable
        cls._aurora_mcp_client = client
        cls._aurora_mcp_client_ctx.set(client)

    # @classmethod
    # def get_response_queue_ctx(
    #     cls,
    # ) -> Optional[asyncio.Queue]:
    #     # First try to get from global state for persistence across calls
    #     if cls._response_queue:
    #         return cls._response_queue
    #     try:
    #         return cls._response_queue_ctx.get()
    #     except LookupError:
    #         return None

    # @classmethod
    # def set_response_queue_ctx(cls, queue: asyncio.Queue) -> None:
    #     # Set both global state and context variable
    #     cls._response_queue = queue
    #     cls._response_queue_ctx.set(queue)

    @classmethod
    def get_gateway_token_ctx(
        cls,
    ) -> Optional[str]:
        # First try to get from global state for persistence across calls
        if cls._gateway_token:
            return cls._gateway_token
        try:
            return cls._gateway_token_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_gateway_token_ctx(cls, token: str) -> None:
        # Set both global state and context variable
        cls._gateway_token = token
        cls._gateway_token_ctx.set(token)

    @classmethod
    def get_agent_ctx(
        cls,
    ) -> Optional[Agent]:
        # First try to get from global state for persistence across calls
        if cls._agent:
            return cls._agent
        try:
            return cls._agent_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_agent_ctx(cls, agent: Agent) -> None:
        # Set both global state and context variable
        cls._agent = agent
        cls._agent_ctx.set(agent)

    @classmethod
    def get_mcp_client_ctx(cls) -> Optional[MCPClient]:
        # First try to get from global state for persistence across calls
        if cls._mcp_client:
            return cls._mcp_client
        try:
            return cls._mcp_client_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_mcp_client_ctx(cls, client: MCPClient) -> None:
        # Set both global state and context variable
        cls._mcp_client = client
        cls._mcp_client_ctx.set(client)

    @classmethod
    def get_gateway_client_ctx(cls) -> Optional[MCPClient]:
        # First try to get from global state for persistence across calls
        if cls._gateway_client:
            return cls._gateway_client
        try:
            return cls._gateway_client_ctx.get()
        except LookupError:
            return None

    @classmethod
    def set_gateway_client_ctx(cls, client: MCPClient) -> None:
        # Set both global state and context variable
        cls._gateway_client = client
        cls._gateway_client_ctx.set(client)
