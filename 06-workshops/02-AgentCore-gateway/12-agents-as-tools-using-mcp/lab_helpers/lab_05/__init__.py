"""
Lab 05: Multi-Agent Orchestration Helper Modules

Contains supervisor agent prompts, IAM setup, deployment utilities, and local agent execution for Lab 05.
"""

from .supervisor_prompt import (
    SUPERVISOR_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT_CONCISE,
    get_supervisor_prompt,
)

from .iam_setup import (
    create_supervisor_runtime_iam_role,
    delete_supervisor_runtime_iam_role,
)

from .cleanup import (
    cleanup_lab_05,
    delete_supervisor_runtime,
    delete_supervisor_gateway,
    delete_ecr_repository,
)

from .local_supervisor_agent import (
    run_supervisor_agent,
    create_mcp_client,
    get_all_tools,
    create_supervisor_agent,
)

__all__ = [
    "SUPERVISOR_SYSTEM_PROMPT",
    "SUPERVISOR_SYSTEM_PROMPT_CONCISE",
    "get_supervisor_prompt",
    "create_supervisor_runtime_iam_role",
    "delete_supervisor_runtime_iam_role",
    "cleanup_lab_05",
    "delete_supervisor_runtime",
    "delete_supervisor_gateway",
    "delete_ecr_repository",
    "run_supervisor_agent",
    "create_mcp_client",
    "get_all_tools",
    "create_supervisor_agent",
]
