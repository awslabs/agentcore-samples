"""Currency-conversion A2A agent for deployment on Databricks Apps.

A LangGraph agent with a single `get_exchange_rate` tool (Frankfurter API,
ECB reference rates) backed by a Databricks-served LLM, exposed over the A2A
protocol with a2a-sdk + Starlette. Deploy this to Databricks Apps (not AgentCore
Runtime); the gateway fronts it as an http.passthrough A2A target.

Served by uvicorn on 0.0.0.0:8000 (see app.yaml). The agent card uses a relative
url ("/") because Databricks Apps run behind a reverse proxy.

Reference implementation mirroring the Databricks A2A blog.
"""

import os

import httpx
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Part, TextPart
from a2a.utils import new_task
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

# Databricks serves an OpenAI-compatible LLM endpoint; the model name and the
# Databricks host/token come from the Apps environment.
MODEL_ID = os.getenv("MODEL_ID", "databricks-claude-sonnet-4")


@tool
def get_exchange_rate(from_currency: str, to_currency: str) -> dict:
    """Get the latest exchange rate between two currency codes.

    Args:
        from_currency: ISO 4217 code to convert from (for example, USD).
        to_currency: ISO 4217 code to convert to (for example, EUR).
    """
    resp = httpx.get(
        "https://api.frankfurter.dev/v1/latest",
        params={"base": from_currency.upper(), "symbols": to_currency.upper()},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    rate = data.get("rates", {}).get(to_currency.upper())
    return {"from": from_currency.upper(), "to": to_currency.upper(), "rate": rate}


def _build_agent():
    # databricks-langchain provides ChatDatabricks for the served LLM. Imported
    # lazily so the module imports even if the optional dep is absent locally.
    from databricks_langchain import ChatDatabricks

    model = ChatDatabricks(endpoint=MODEL_ID)
    return create_react_agent(
        model,
        tools=[get_exchange_rate],
        prompt=(
            "You are a currency-conversion assistant. Use get_exchange_rate to "
            "answer questions about exchange rates and conversions. Be concise."
        ),
    )


class CurrencyAgentExecutor(AgentExecutor):
    """Bridge A2A message/send to the LangGraph currency agent."""

    def __init__(self):
        self._agent = None

    def _agent_or_build(self):
        if self._agent is None:
            self._agent = _build_agent()
        return self._agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input()
        task = context.current_task or new_task(context.message)  # type: ignore[arg-type]
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        result = self._agent_or_build().invoke({"messages": [("user", user_text)]})
        answer = result["messages"][-1].content

        await updater.add_artifact(
            [Part(root=TextPart(text=answer))], name="currency_response"
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")


agent_card = AgentCard(
    name="Currency Agent",
    description="Converts currencies and answers exchange-rate questions.",
    # Defaults to a relative "/" (Databricks Apps serve behind a reverse proxy).
    # Set AGENT_CARD_URL to an absolute URL when an A2A client reads the card and
    # follows its `url` to send messages: clients that cannot resolve a relative
    # URL need the absolute address. Point it at the gateway target URL
    # (for example https://<gateway>/databricks-a2a/) so message/send routes back
    # through the gateway rather than directly to the app.
    url=os.getenv("AGENT_CARD_URL", "/"),
    version="1.0.0",
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
    capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
    skills=[
        AgentSkill(
            id="convert_currency",
            name="convert_currency",
            description="Convert an amount between two currencies using live rates.",
            tags=["currency", "fx", "exchange-rate"],
        )
    ],
)

request_handler = DefaultRequestHandler(
    agent_executor=CurrencyAgentExecutor(),
    task_store=InMemoryTaskStore(),
)
app = A2AStarletteApplication(
    agent_card=agent_card, http_handler=request_handler
).build()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
