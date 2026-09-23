from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Part,
    TaskState,
    TextPart,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError
import logging
import os
from agent import MonitoringAgent

logger = logging.getLogger(__name__)


class MonitoringAgentExecutor(AgentExecutor):
    """
    Agent executor for the Strands-based monitoring agent
    """

    def __init__(self):
        """Initialize the executor"""
        self._agent = None
        self._active_tasks = {}
        logger.info("MonitoringAgentExecutor initialized")

    async def _get_agent(self, session_id: str, actor_id: str, workload_token: str):
        """Get or create the agent instance"""
        if self._agent is None:
            logger.info("Creating monitoring agent...")

            # Get configuration from environment
            memory_id = os.getenv("MEMORY_ID")
            model_id = os.getenv("MODEL_ID", "global.anthropic.claude-sonnet-4-20250514-v1:0")
            region_name = os.getenv("MCP_REGION")

            if not memory_id or not region_name:
                raise RuntimeError("Missing required environment variables: MEMORY_ID or MCP_REGION")

            # Create agent instance
            self._agent = MonitoringAgent(
                memory_id=memory_id,
                model_id=model_id,
                region_name=region_name,
                actor_id=actor_id,
                session_id=session_id,
                workload_token=workload_token,
            )
            logger.info("Monitoring agent created successfully")

        return self._agent

    async def _execute_streaming(
        self,
        agent,
        user_message: str,
        updater: TaskUpdater,
        task_id: str,
        session_id: str,
    ) -> None:
        """Execute agent with streaming and update task status incrementally."""
        accumulated_text = ""

        try:
            # Use the agent's stream method
            async for event in agent.stream(user_message, session_id):
                # Check if task was cancelled
                if not self._active_tasks.get(task_id, False):
                    logger.info(f"Task {task_id} was cancelled during streaming")
                    return

                # Handle error events
                if "error" in event:
                    error_msg = event.get("content", "Unknown error")
                    logger.error(f"Error in stream: {error_msg}")
                    raise Exception(error_msg)

                # Stream content updates
                content = event.get("content", "")
                if content and not event.get("is_task_complete", False):
                    accumulated_text += content
                    # Send incremental update
                    await updater.update_status(
                        TaskState.working,
                        new_agent_text_message(
                            accumulated_text,
                            updater.context_id,
                            updater.task_id,
                        ),
                    )

            # Add final result as artifact
            if accumulated_text:
                await updater.add_artifact(
                    [Part(root=TextPart(text=accumulated_text))],
                    name="agent_response",
                )

            await updater.complete()

        except Exception as e:
            logger.error(f"Error in streaming execution: {e}", exc_info=True)
            raise

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Execute the agent's logic for a given request context.
        """
        # Extract required headers
        session_id = None
        actor_id = None
        workload_token = None

        if context.call_context:
            headers = context.call_context.state.get("headers", {})
            session_id = headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
            actor_id = headers.get("x-amzn-bedrock-agentcore-runtime-custom-actorid")
            workload_token = headers.get("x-amzn-bedrock-agentcore-runtime-workload-accesstoken")

        if not actor_id:
            logger.error("Actor ID is not set")
            raise ServerError(error=InvalidParamsError())

        if not session_id:
            logger.error("Session ID is not set")
            raise ServerError(error=InvalidParamsError())

        if not workload_token:
            logger.error("Workload token is not set")
            raise ServerError(error=InvalidParamsError())

        # Get or create task
        task = context.current_task
        if not task:
            logger.info("No current task, creating new task")
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        task_id = context.task_id

        try:
            logger.info(f"Executing task {task.id}")

            # Extract user input
            user_message = context.get_user_input()
            if not user_message:
                logger.error("No user message found in context")
                raise ServerError(error=InvalidParamsError())

            logger.info(f"User message: '{user_message}'")

            # Get the agent instance
            agent = await self._get_agent(session_id, actor_id, workload_token)

            # Mark task as active
            self._active_tasks[task_id] = True

            # Execute the agent
            logger.info("Calling agent...")
            await self._execute_streaming(agent, user_message, updater, task_id, session_id)

            logger.info(f"Task {task_id} completed successfully")

        except ServerError:
            # Re-raise ServerError as-is
            raise
        except Exception as e:
            logger.error(f"Error executing task {task_id}: {e}", exc_info=True)
            raise ServerError(error=InternalError()) from e
        finally:
            # Clean up task from active tasks
            self._active_tasks.pop(task_id, None)

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Request the agent to cancel an ongoing task.
        """
        task_id = context.task_id
        logger.info(f"Cancelling task {task_id}")

        try:
            # Mark task as cancelled
            self._active_tasks[task_id] = False

            task = context.current_task
            if task:
                updater = TaskUpdater(event_queue, task.id, task.context_id)
                await updater.cancel()
                logger.info(f"Task {task_id} cancelled successfully")
            else:
                logger.warning(f"No task found for task_id {task_id}")

        except Exception as e:
            logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
            raise ServerError(error=InternalError()) from e
