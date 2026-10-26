"""A2A executor for the self-contained monitoring agent.

Builds a Strands agent with local boto3-backed CloudWatch tools and answers
A2A message/send requests. Unlike the original use-case agent, this version has
no external gateway, memory, or mandatory runtime headers, so it deploys
standalone on AgentCore Runtime.
"""

import logging
import os

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import InternalError, InvalidParamsError, Part, TextPart
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from strands import Agent
from strands.models import BedrockModel

from prompt import SYSTEM_PROMPT
from tools import ALL_TOOLS

logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")


class MonitoringAgentExecutor(AgentExecutor):
    """A2A executor backed by a Strands agent with local CloudWatch tools."""

    def __init__(self):
        self._agent = None
        logger.info("MonitoringAgentExecutor initialized")

    def _get_agent(self) -> Agent:
        if self._agent is None:
            logger.info("Creating monitoring agent (model=%s)", MODEL_ID)
            self._agent = Agent(
                name="Monitoring Agent",
                system_prompt=SYSTEM_PROMPT,
                model=BedrockModel(model_id=MODEL_ID),
                tools=ALL_TOOLS,
            )
        return self._agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_message = context.get_user_input()
        if not user_message:
            raise ServerError(error=InvalidParamsError())

        task = context.current_task
        if not task:
            task = new_task(context.message)  # type: ignore[arg-type]
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            logger.info("Executing task %s: %r", task.id, user_message)
            agent = self._get_agent()
            result = agent(user_message)
            text = str(result)

            await updater.add_artifact(
                [Part(root=TextPart(text=text))],
                name="agent_response",
            )
            await updater.complete()
            logger.info("Task %s completed", task.id)
        except Exception as e:  # noqa: BLE001
            logger.error("Error executing task %s: %s", task.id, e, exc_info=True)
            raise ServerError(error=InternalError()) from e

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Synchronous single-shot execution; nothing to cancel mid-flight.
        raise ServerError(error=InternalError())
