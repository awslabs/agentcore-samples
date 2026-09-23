from bedrock_agentcore.memory import MemoryClient
from memory_hook import MonitoringMemoryHooks
from prompt import SYSTEM_PROMPT
from strands import Agent
from strands.models import BedrockModel
from utils import create_gateway_client


class MonitoringAgent:
    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(
        self,
        memory_id: str,
        model_id: str,
        region_name: str,
        actor_id: str,
        session_id: str,
        workload_token: str,
    ):
        bedrock_model = BedrockModel(model_id=model_id, region_name=region_name)
        memory_client = MemoryClient(region_name=region_name)

        monitoring_hooks = MonitoringMemoryHooks(
            memory_id=memory_id,
            client=memory_client,
            actor_id=actor_id,
            session_id=session_id,
        )

        self._gateway_client = create_gateway_client(workload_token)
        self._gateway_client.start()
        gateway_tools = self._gateway_client.list_tools_sync()

        self.agent = Agent(
            name="Monitoring Agent",
            description="A monitoring agent that handles CloudWatch logs, metrics, dashboards, and AWS service monitoring",
            system_prompt=SYSTEM_PROMPT,
            model=bedrock_model,
            tools=gateway_tools,
            hooks=[monitoring_hooks],
        )

    async def stream(self, query: str, session_id: str):
        response = str()
        try:
            async for event in self.agent.stream_async(query):
                if "data" in event:
                    # Only stream text chunks to the client
                    response += event["data"]
                    yield {
                        "is_task_complete": "complete" in event,
                        "require_user_input": False,
                        "content": event["data"],
                    }

        except Exception as e:
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": f"We are unable to process your request at the moment. Error: {e}",
            }
        finally:
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": response,
            }

    def invoke(self, query: str, session_id: str):
        try:
            response = str(self.agent(query))

        except Exception as e:
            raise f"Error invoking agent: {e}"
        return response
