import os
import time
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException

# from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import calculator, current_time
from ddgs import DDGS
import uuid

app = FastAPI(title="Travel API")

# Configuration from environment variables
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MODEL_TEMPERATURE = float(os.environ.get("MODEL_TEMPERATURE", "0"))
MODEL_MAX_TOKENS = int(os.environ.get("MODEL_MAX_TOKENS", "1028"))
DDGS_DELAY_SECONDS = int(os.environ.get("DDGS_DELAY_SECONDS", "10"))


# Shared helper function to avoid rate limiting
def ddgs_search_with_delay(query: str, max_results: int = 3) -> list:
    """
    Shared function to search using DDGS with rate limit protection.
    """
    try:
        time.sleep(DDGS_DELAY_SECONDS)
        ddgs = DDGS()
        results = ddgs.text(query, max_results=max_results)
        return list(results) if results else []
    except Exception as e:
        return f"DDGS search error: {str(e)}"


@tool
def web_search(query: str) -> str:
    """Search the web for current information about travel destinations, attractions, and events."""
    try:
        results = ddgs_search_with_delay(query, max_results=2)

        if not results:
            return "No results found."

        formatted_results = []
        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )

        return "\n".join(formatted_results)
    except Exception as e:
        return f"Search error: {str(e)}"


@tool
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """
    Convert currency amounts for travel budgeting.

    Args:
        amount: The amount to convert
        from_currency: Source currency code (e.g., 'USD', 'EUR', 'GBP')
        to_currency: Target currency code (e.g., 'THB', 'JPY', 'MXN')

    Returns:
        Conversion result with exchange rate information
    """
    try:
        query = f"convert {amount} {from_currency} to {to_currency} exchange rate"
        results = ddgs_search_with_delay(query, max_results=2)

        if not results:
            return f"Could not find exchange rate for {from_currency} to {to_currency}."

        # Format results with source citations
        formatted_results = [f"Currency conversion: {amount} {from_currency} to {to_currency}\n"]
        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )

        return "\n".join(formatted_results)
    except Exception as e:
        return f"Currency conversion error: {str(e)}"


@tool
def get_climate_data(location: str, month: str) -> str:
    """
    Get historical average weather data for travel planning.

    Args:
        location: City or region name (e.g., 'Bali', 'Paris', 'Tokyo')
        month: Month name (e.g., 'February', 'July', 'December')

    Returns:
        Average temperature, rainfall, and weather conditions for that location and month
    """
    try:
        query = f"{location} weather in {month} average temperature rainfall climate"
        results = ddgs_search_with_delay(query, max_results=2)

        if not results:
            return f"Could not find climate data for {location} in {month}."

        # Format results with source citations
        formatted_results = [f"Climate data for {location} in {month}:\n"]
        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )

        return "\n".join(formatted_results)
    except Exception as e:
        return f"Climate data error: {str(e)}"


@tool
def search_flight_info(origin: str, destination: str) -> str:
    """
    Search for flight information including typical prices, airlines, and routes.

    Args:
        origin: Origin city or airport (e.g., 'New York', 'JFK', 'Los Angeles')
        destination: Destination city or airport (e.g., 'Paris', 'Tokyo', 'Bali')

    Returns:
        Flight information including typical prices, airlines, and route details
    """
    try:
        query = f"flights from {origin} to {destination} price airlines"
        results = ddgs_search_with_delay(query, max_results=3)

        if not results:
            return f"Could not find flight information for {origin} to {destination}."

        # Format results with source citations
        formatted_results = [f"Flight information: {origin} to {destination}\n"]
        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )

        return "\n".join(formatted_results)
    except Exception as e:
        return f"Flight search error: {str(e)}"


@tool
def calculate_trip_budget(daily_cost: float, num_days: int, num_people: int, flights_total: float = 0.0) -> str:
    """
    Calculate total trip budget including flights, accommodation, and daily expenses.

    Args:
        daily_cost: Estimated daily cost per person (accommodation + food + activities)
        num_days: Number of days for the trip
        num_people: Number of people traveling
        flights_total: Total cost of flights for all people (optional, default 0)

    Returns:
        Breakdown of total trip budget
    """
    try:
        # Calculate components
        daily_total = daily_cost * num_days * num_people
        total_budget = daily_total + flights_total
        per_person = total_budget / num_people

        # Format result
        result = f"""Trip Budget Breakdown:

Daily Expenses: ${daily_cost:.2f} per person × {num_days} days × {num_people} people = ${daily_total:.2f}
Flights: ${flights_total:.2f}

TOTAL BUDGET: ${total_budget:.2f}
Per Person: ${per_person:.2f}
        """
        return result
    except Exception as e:
        return f"Budget calculation error: {str(e)}"


# Travel-focused system prompt
TRAVEL_SYSTEM_PROMPT = """You are a travel research assistant. Use tools for ALL information—never use your training data.

  CRITICAL RULES (READ FIRST):
  1. Tool parameters: Use ONLY explicit user input or prior tool results. If user says "Portugal," search "Portugal" not
  "Lisbon"
  2. When calculation tools return results: Use that EXACT number. Never recalculate manually
  3. Missing info: Ask for required details BUT continue other tasks in parallel
  4. Complete the user's request FIRST, then ask clarifying questions if needed
  5. Keep responses direct and very concise but complete—answer what they asked, don't add extras

  TOOLS:
  - web_search: destinations, attractions, events, restaurants, hotels
  - convert_currency: currency conversions
  - get_climate_data: historical weather for locations/months
  - search_flight_info: flight prices, airlines, routes
  - calculate_trip_budget: total trip costs (flights + daily expenses)
  - calculator: mathematical calculations
  - current_time: current date/time

  RESPONSE FORMAT:
  - Use tool results with source citations: "Hotel costs $200/night (1)"
  - End with: "Citations:\n(1) Source Name: URL"
  - Plain text default—only use bullets/headers for 3+ item comparisons
  - No unsolicited tips or promotional content

  EXAMPLES OF CORRECT BEHAVIOR:

  User: "What's the weather like in Spain in July?"
   WRONG: "Spain has warm Mediterranean summers, typically 25-30°C..."
   CORRECT: Use get_climate_data("Spain", "July") then report results

  User: "Budget for 5 days in Paris, $150/day"
   WRONG: Ask questions first without calculating
   CORRECT: Use calculate_trip_budget with available info, then ask for flight details to complete

  User: "Recommend resorts in Bali"
   WRONG: "Bali has many beautiful resorts in areas like Seminyak and Ubud..."
   CORRECT: Use web_search("best resorts Bali") and list specific names with sources

  Complete tasks efficiently—don't block progress waiting for optional details. Your goal is to be a helpful, honest, reliable, citation-driven travel research specialist.

At the point where tools are done being invoked and a summary can be presented to the user, invoke the ready_to_summarize tool and then continue with the summary."""

# Initialize model at module level (stateless, reusable)
model = BedrockModel(model_id=MODEL_ID, temperature=MODEL_TEMPERATURE, max_tokens=MODEL_MAX_TOKENS)

# Tools list
TRAVEL_TOOLS = [
    web_search,
    convert_currency,
    get_climate_data,
    search_flight_info,
    calculate_trip_budget,
    calculator,
    current_time,
]

# Session-based agent pool
agent_sessions: Dict[str, Agent] = {}


def get_or_create_agent(session_id: str) -> Agent:
    """Get an existing agent for the session or create a new one."""
    if session_id not in agent_sessions:
        agent_sessions[session_id] = Agent(
            model=model,
            system_prompt=TRAVEL_SYSTEM_PROMPT,
            tools=TRAVEL_TOOLS,
            trace_attributes={
                "service.name": "strands-agents-travel",
                "session.id": session_id,
            },
        )
    return agent_sessions[session_id]


class PromptRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None  # If not provided, generates new one


class TravelResponse(BaseModel):
    response: str
    session_id: str


@app.get("/health")
def health_check():
    """Health check endpoint for the load balancer."""
    return {"status": "healthy"}


@app.post("/travel")
async def get_travel_info(request: PromptRequest):
    """Endpoint to get travel information."""
    prompt = request.prompt

    if not prompt:
        raise HTTPException(status_code=400, detail="No prompt provided")

    # Generate session_id if not provided
    session_id = request.session_id or str(uuid.uuid4())

    try:
        agent = get_or_create_agent(session_id)
        response = agent(prompt)
        return TravelResponse(response=str(response), session_id=session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104
