"""
Travel Tools MCP Server

Exposes raw travel-related tools via MCP protocol.
No agent logic - just pure tool implementations.
"""

import os
import logging
import boto3
from typing import Optional
from mcp.server import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment configuration
REGION = os.getenv("AWS_REGION")
if not REGION:
    raise ValueError("AWS_REGION environment variable is required")

# Initialize AWS clients
ssm_client = boto3.client("ssm", region_name=REGION)

# Create MCP server
mcp = FastMCP("Travel Tools", host="0.0.0.0", stateless_http=True)  # nosec B104:standard pattern for containerized MCP serverss


def get_ssm_parameter(parameter_name: str) -> str | None:
    """Retrieve a parameter from SSM Parameter Store."""
    try:
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except Exception as e:
        logger.warning(f"Could not retrieve SSM parameter {parameter_name}: {e}")
        return None


def load_api_keys():
    """Load API keys from SSM and set as environment variables."""
    keys = {
        "OPENWEATHER_API_KEY": "/concierge-agent/travel/openweather-api-key",
        # "TAVILY_API_KEY": "/concierge-agent/travel/tavily-api-key",
        "SERP_API_KEY": "/concierge-agent/travel/serp-api-key",
        "GOOGLE_MAPS_KEY": "/concierge-agent/travel/google-maps-key",
        # "AMADEUS_PUBLIC": "/concierge-agent/travel/amadeus-public",
        # "AMADEUS_SECRET": "/concierge-agent/travel/amadeus-secret",
    }

    for env_var, ssm_param in keys.items():
        if not os.getenv(env_var):
            value = get_ssm_parameter(ssm_param)
            if value:
                os.environ[env_var] = value
                logger.info(f"✓ Loaded {env_var} from SSM")
            else:
                logger.warning(f"⚠️  {env_var} not configured")


# Load API keys before importing tools
load_api_keys()

# Import tools after API keys are loaded
from tools import (  # noqa: E402
    serp_search_tool,
    serp_hotel_search,
    # get_flight_offers,
    serp_flight_search,
    # get_hotel_data,
    google_places_search,
)


# =============================================================================
# MCP TOOLS - Raw tool exposure
# =============================================================================

# @mcp.tool()
# def travel_get_weather(city: str) -> str:
#     """
#     Get 5-day weather forecast for a city.

#     Args:
#         city: City name (e.g., "Paris", "Tokyo", "New York")

#     Returns:
#         Weather forecast with daily temperatures and conditions.
#     """
#     return get_weather(city)


@mcp.tool()
def travel_search(query: str) -> str:
    """
    Search the internet for travel-related information.

    Args:
        query: Search query (e.g., "best restaurants in Rome", "Tokyo travel tips")

    Returns:
        Search results with titles, snippets, and source URLs.
    """
    return serp_search_tool(query)


# @mcp.tool()
# def travel_get_flights(
#     origin: str,
#     destination: str,
#     departure_date: str,
#     adults: int = 1,
#     max_price: int = 400,
#     currency: str = "USD"
# ) -> dict:
#     """
#     Search for flight offers between two cities.

#     Args:
#         origin: Origin airport IATA code (e.g., "BOS", "JFK", "LAX")
#         destination: Destination airport IATA code (e.g., "PAR", "ROM", "TYO")
#         departure_date: Departure date in YYYY-MM-DD format (e.g., "2025-12-25")
#         adults: Number of adult passengers (default: 1)
#         max_price: Maximum price filter (default: 400)
#         currency: Currency code (default: "USD")

#     Returns:
#         Flight offers with pricing and schedule details.
#     """
#     return get_flight_offers(origin, destination, departure_date, adults, max_price, currency)


# @mcp.tool()
# def travel_get_hotels(
#     city_code: str,
#     ratings: str = "4,5",
#     amenities: str = "AIR_CONDITIONING"
# ) -> dict:
#     """
#     Search for hotels in a city.

#     Args:
#         city_code: City IATA code (e.g., "ROM", "NYC", "PAR", "MAD", "BOS")
#         ratings: Hotel star ratings to filter (e.g., "3,4,5" for 3+ stars)
#         amenities: Amenities filter. Options: SWIMMING_POOL, SPA, FITNESS_CENTER,
#                    AIR_CONDITIONING, RESTAURANT, PARKING, PETS_ALLOWED, WIFI, etc.

#     Returns:
#         Hotel listings with names, ratings, and amenities.
#     """
#     return get_hotel_data(city_code, ratings, amenities)


@mcp.tool()
def travel_places_search(query: str) -> dict:
    """
    Search for places, restaurants, and attractions using Google Places.

    Args:
        query: Search query (e.g., "best sushi restaurants in Tokyo",
               "museums near Eiffel Tower", "coffee shops in Seattle")

    Returns:
        Places with names, addresses, ratings, and Google Maps links.
    """
    return google_places_search(query)


@mcp.tool()
def travel_hotel_search(query: str, check_in_date: str, check_out_date: str) -> str:
    """
    Search for hotels using Google Hotels via SerpAPI.

    Args:
        query: Hotel search query (e.g., "fancy hotels in Paris",
               "hotels near Times Square", "beachfront hotels in Miami")
        check_in_date: Check-in date in YYYY-MM-DD format (e.g., "2025-12-20")
        check_out_date: Check-out date in YYYY-MM-DD format (e.g., "2025-12-25")

    Returns:
        Formatted hotel results with ratings, prices, amenities, and booking links.
    """
    return serp_hotel_search(query, check_in_date, check_out_date)


@mcp.tool()
def travel_flight_search(
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: Optional[str] = None,
) -> str:
    """
    Search for flights using Google Flights via SerpAPI.

    Args:
        departure_id: Departure airport code (e.g., "DCA", "JFK", "LAX")
        arrival_id: Arrival airport code (e.g., "LGA", "SFO", "ORD")
        outbound_date: Outbound flight date in YYYY-MM-DD format (e.g., "2025-12-20")
        return_date: Return flight date in YYYY-MM-DD format (optional, omit for one-way)

    Returns:
        Formatted flight results with prices, durations, layovers, and carbon emissions.
    """
    return serp_flight_search(departure_id, arrival_id, outbound_date, return_date)


# =============================================================================
# SERVER STARTUP
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting Travel Tools MCP Server...")
    mcp.run(transport="streamable-http")
