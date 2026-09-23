"""
Travel Tools Implementation

Pure tool functions for weather, search, flights, hotels, and places.
No agent logic - these are called directly by the MCP server.
"""

import csv
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import requests
from rapidfuzz import process, fuzz
from tavily import TavilyClient
from serpapi import GoogleSearch

# =============================================================================
# CONFIGURATION
# =============================================================================

REGION = os.getenv("AWS_REGION")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY")
SERP_API_KEY = os.getenv("SERP_API_KEY")
AMADEUS_PUBLIC = os.getenv("AMADEUS_PUBLIC")
AMADEUS_SECRET = os.getenv("AMADEUS_SECRET")


# =============================================================================
# CITY DATABASE
# =============================================================================


def load_cities(filepath: str) -> list[dict[str, Any]]:
    """Load city database from CSV file."""
    cities = []
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cities.append(
                    {
                        "city": row["city"].strip(),
                        "country": row.get("country", "").strip(),
                        "latitude": float(row["lat"]),
                        "longitude": float(row["lng"]),
                        "population": row["population"],
                    }
                )
    except FileNotFoundError:
        print(f"Warning: City database not found at {filepath}")
    except Exception as e:
        print(f"Error loading city database: {e}")
    return cities


def get_city_database() -> tuple[list[dict], list[str]]:
    """Get city database and names list."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    cities_file = os.path.join(current_dir, "worldcities.csv")
    cities = load_cities(cities_file)
    city_names = [c["city"].lower() for c in cities]
    return cities, city_names


CITIES, CITY_NAMES = get_city_database()


def match_city(user_input: str, top_n: int = 3, score_cutoff: int = 80) -> list[tuple[str, dict]]:
    """Match user input to city using fuzzy matching."""
    if not CITY_NAMES:
        return []

    matches = process.extract(user_input.lower(), CITY_NAMES, scorer=fuzz.WRatio, limit=top_n)

    return [(match, CITIES[idx]) for match, score, idx in matches if score >= score_cutoff]


# =============================================================================
# WEATHER TOOL
# =============================================================================


def call_openweather_api(lat: str, lon: str, call_type: str = "forecast") -> str | None:
    """Call OpenWeatherMap API."""
    if not OPENWEATHER_API_KEY:
        return None

    url = f"https://api.openweathermap.org/data/2.5/{call_type}"
    params = {"lat": lat, "lon": lon, "appid": OPENWEATHER_API_KEY}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.content.decode()
    except requests.RequestException as e:
        print(f"OpenWeather API error: {e}")
        return None


def process_weather_forecast(weather_json: dict) -> list[str]:
    """Process weather data into daily summaries."""
    if not weather_json or "list" not in weather_json:
        return []

    daily_forecast = {}

    for entry in weather_json["list"]:
        try:
            dt = datetime.strptime(entry["dt_txt"], "%Y-%m-%d %H:%M:%S")
            day = dt.date()
            hour = dt.hour

            if hour == 12 and day not in daily_forecast:
                daily_forecast[day] = entry
            elif day not in daily_forecast:
                daily_forecast[day] = entry
        except (KeyError, ValueError):
            continue

    forecast_output = []
    for day in sorted(daily_forecast.keys()):
        try:
            entry = daily_forecast[day]
            desc = entry["weather"][0]["description"].title()
            temp_k = entry["main"]["temp"]
            temp_f = round((temp_k - 273.15) * 9 / 5 + 32)
            forecast_output.append(f"{day.strftime('%A, %b %d')}: {desc}, {temp_f}°F")
        except (KeyError, IndexError):
            continue

    return forecast_output


def get_weather(city: str) -> str:
    """Get 5-day weather forecast for a city."""
    if not OPENWEATHER_API_KEY:
        return "Error: OpenWeather API key not configured."

    try:
        # Match city to database
        matches = match_city(city, score_cutoff=70)

        if not matches:
            return f"Error: Could not find city '{city}' in database."

        # Use best match
        _, city_data = matches[0]
        lat = str(city_data["latitude"])
        lon = str(city_data["longitude"])
        matched_city = city_data["city"]
        country = city_data["country"]

        # Call API
        weather_raw = call_openweather_api(lat, lon, "forecast")
        if not weather_raw:
            return "Error: Could not retrieve weather data."

        weather_json = json.loads(weather_raw)
        forecast_lines = process_weather_forecast(weather_json)

        if not forecast_lines:
            return "Error: No forecast data available."

        header = f"5-Day Forecast for {matched_city}, {country}:\n"
        return header + "\n".join(forecast_lines)

    except Exception as e:
        return f"Error: {str(e)}"


# =============================================================================
# SEARCH TOOL
# =============================================================================


def search_tool(query: str) -> str:
    """Perform internet search using Tavily."""
    if not TAVILY_API_KEY:
        return "Error: Tavily API key not configured."

    try:
        client = TavilyClient(TAVILY_API_KEY)
        response = client.search(query=query, max_results=5)

        if not response or "results" not in response:
            return "No search results found."

        formatted = []
        for i, result in enumerate(response["results"], 1):
            title = result.get("title", "No title")
            content = result.get("content", "")[:200]
            url = result.get("url", "")

            formatted.append(f"{i}. **{title}**\n   {content}...\n   Source: {url}")

        return "\n\n".join(formatted) if formatted else "No results found."

    except Exception as e:
        return f"Search error: {str(e)}"


# =============================================================================
# AMADEUS API (Flights & Hotels)
# =============================================================================


def get_amadeus_token() -> str | None:
    """Get OAuth token for Amadeus API."""
    if not AMADEUS_PUBLIC or not AMADEUS_SECRET:
        return None

    token_url = "https://test.api.amadeus.com/v1/security/oauth2/token"

    try:
        response = requests.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": AMADEUS_PUBLIC,
                "client_secret": AMADEUS_SECRET,
            },
            timeout=10,
        )

        if response.status_code == 200:
            return response.json().get("access_token")
        return None

    except Exception as e:
        print(f"Amadeus token error: {e}")
        return None


def get_flight_offers(
    origin: str,
    destination: str,
    departure_date: str,
    adults: int = 1,
    max_price: int = 400,
    currency: str = "USD",
) -> dict:
    """Search for flight offers."""
    token = get_amadeus_token()
    if not token:
        return {"error": "Amadeus API not configured or token failed."}

    try:
        response = requests.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date,
                "adults": adults,
                "currencyCode": currency,
                "maxPrice": max_price,
            },
            timeout=15,
        )

        if response.status_code == 200:
            return response.json()
        return {
            "error": f"Flight search failed: {response.status_code}",
            "details": response.text,
        }

    except Exception as e:
        return {"error": f"Flight search error: {str(e)}"}


def get_hotel_data(city_code: str, ratings: str = "4,5", amenities: str = "AIR_CONDITIONING") -> dict:
    """Search for hotels in a city."""
    token = get_amadeus_token()
    if not token:
        return {"error": "Amadeus API not configured or token failed."}

    try:
        params = {"cityCode": city_code}
        if ratings:
            params["ratings"] = ratings
        if amenities:
            params["amenities"] = amenities

        response = requests.get(
            "https://test.api.amadeus.com/v1/reference-data/locations/hotels/by-city",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )

        if response.status_code == 200:
            return response.json()
        return {
            "error": f"Hotel search failed: {response.status_code}",
            "details": response.text,
        }

    except Exception as e:
        return {"error": f"Hotel search error: {str(e)}"}


# =============================================================================
# GOOGLE PLACES TOOL
# =============================================================================


def google_places_search(query: str) -> dict:
    """Search for places using Google Places API."""
    if not GOOGLE_MAPS_KEY:
        return {"error": "Google Maps API key not configured."}

    try:
        response = requests.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": GOOGLE_MAPS_KEY,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.priceLevel,places.googleMapsUri,places.rating",
            },
            json={"textQuery": query},
            timeout=10,
        )

        if response.status_code == 200:
            return response.json()
        return {"error": f"Places search failed: {response.status_code}"}

    except Exception as e:
        return {"error": f"Places search error: {str(e)}"}


def serp_search_tool(query: str) -> str:
    """Perform internet search using SerpAPI."""
    if not SERP_API_KEY:
        return "Error: SERP API key not configured."

    try:
        # Search using SerpAPI
        params = {
            "engine": "google",  # google_hotels, # google_flights
            "q": query,
            "api_key": SERP_API_KEY,
        }

        search = GoogleSearch(params)
        response = search.get_dict()

        if not response or "organic_results" not in response:
            return "No search results found."

        formatted = []
        for i, result in enumerate(response["organic_results"], 1):
            title = result.get("title", "No title")
            snippet = result.get("snippet", "")[:200]
            link = result.get("link", "")
            source = result.get("source", "")

            # Build formatted result
            result_text = f"{i}. **{title}**"
            if snippet:
                result_text += f"\n   {snippet}"
                if len(result.get("snippet", "")) > 200:
                    result_text += "..."
            if source:
                result_text += f"\n   Source: {source}"
            if link:
                result_text += f"\n   URL: {link}"

            formatted.append(result_text)

        return "\n\n".join(formatted) if formatted else "No results found."

    except Exception as e:
        return f"Search error: {str(e)}"


def serp_hotel_search(query: str, check_in_date: str, check_out_date: str) -> str:
    """
    Search for hotels using SerpAPI Google Hotels engine.

    Args:
        query: Hotel search query (e.g., "fancy hotels in Paris", "hotels near Times Square")
        check_in_date: Check-in date in YYYY-MM-DD format
        check_out_date: Check-out date in YYYY-MM-DD format

    Returns:
        Formatted string with hotel results
    """
    if not SERP_API_KEY:
        return "Error: SERP API key not configured."

    try:
        # Search hotels using SerpAPI
        params = {
            "engine": "google_hotels",
            "q": query,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "api_key": SERP_API_KEY,
        }

        search = GoogleSearch(params)
        response = search.get_dict()

        if not response or "properties" not in response:
            return "No hotel results found."

        formatted = []
        for i, hotel in enumerate(response["properties"][:10], 1):  # Limit to top 10
            name = hotel.get("name", "No name")

            # Build formatted result
            result_text = f"{i}. **{name}**"

            # Hotel class (star rating)
            hotel_class = hotel.get("hotel_class")
            if hotel_class:
                try:
                    # Extract numeric value from strings like "3-star hotel" or just "3"
                    if isinstance(hotel_class, str):
                        # Try to extract the first number from the string
                        match = re.search(r"\d+", hotel_class)
                        if match:
                            stars = int(match.group())
                            result_text += f" {'⭐' * stars}"
                    else:
                        # If it's already a number, use it directly
                        result_text += f" {'⭐' * int(hotel_class)}"
                except (ValueError, TypeError):
                    # If conversion fails, just show the text
                    result_text += f" ({hotel_class})"

            # Rating
            rating = hotel.get("overall_rating")
            reviews = hotel.get("reviews")
            if rating:
                result_text += f"\n   Rating: {rating}"
                if reviews:
                    result_text += f" ({reviews} reviews)"

            # Price per night
            rate = hotel.get("rate_per_night")
            if rate:
                lowest = rate.get("extracted_lowest")
                highest = rate.get("extracted_highest")
                if lowest and highest:
                    result_text += f"\n   Price: ${lowest} - ${highest} per night"
                elif lowest:
                    result_text += f"\n   Price: ${lowest} per night"

            # Description
            description = hotel.get("description", "")
            if description:
                # Truncate description to 150 characters
                desc_short = description[:150]
                if len(description) > 150:
                    desc_short += "..."
                result_text += f"\n   {desc_short}"

            # Amenities (if available)
            amenities = hotel.get("amenities", [])
            if amenities and len(amenities) > 0:
                # Show first 3 amenities
                amenities_str = ", ".join(amenities[:3])
                result_text += f"\n   Amenities: {amenities_str}"
                if len(amenities) > 3:
                    result_text += f" (+{len(amenities) - 3} more)"

            # Link
            link = hotel.get("link")
            if link:
                result_text += f"\n   URL: {link}"

            formatted.append(result_text)

        header = f"Hotel Search Results for '{query}' ({check_in_date} to {check_out_date}):\n\n"
        return header + "\n\n".join(formatted) if formatted else "No hotel results found."

    except Exception as e:
        return f"Hotel search error: {str(e)}"


def serp_flight_search(
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: Optional[str] = None,
) -> str:
    """
    Search for flights using SerpAPI Google Flights engine.

    Args:
        departure_id: Departure airport code (e.g., "DCA", "JFK")
        arrival_id: Arrival airport code (e.g., "LGA", "LAX")
        outbound_date: Outbound flight date in YYYY-MM-DD format
        return_date: Return flight date in YYYY-MM-DD format (optional, omit for one-way)

    Returns:
        Formatted string with flight results
    """
    if not SERP_API_KEY:
        return "Error: SERP API key not configured."

    try:
        # Search flights using SerpAPI
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "api_key": SERP_API_KEY,
        }

        # Add return date if provided
        if return_date:
            params["return_date"] = return_date

        search = GoogleSearch(params)
        results = search.get_dict()

        if not results:
            return "No flight results found."

        formatted = []

        # Process best flights
        best_flights = results.get("best_flights", [])
        if best_flights:
            formatted.append("=== Best Flights ===\n")
            for i, flight in enumerate(best_flights, 1):
                result_text = f"{i}. "

                # Flight segments
                segments = []
                for segment in flight.get("flights", []):
                    dep_airport = segment.get("departure_airport", {})
                    arr_airport = segment.get("arrival_airport", {})
                    airline = segment.get("airline", "Unknown")
                    flight_num = segment.get("flight_number", "")

                    seg_text = f"{airline} {flight_num}: {dep_airport.get('id', '')} ({dep_airport.get('time', '')}) → {arr_airport.get('id', '')} ({arr_airport.get('time', '')})"
                    segments.append(seg_text)

                result_text += "\n   ".join(segments)

                # Price and duration
                price = flight.get("price")
                total_duration = flight.get("total_duration")
                if price:
                    result_text += f"\n   Price: ${price}"
                if total_duration:
                    hours = total_duration // 60
                    minutes = total_duration % 60
                    result_text += f" | Duration: {hours}h {minutes}m"

                # Layovers
                layovers = flight.get("layovers", [])
                if layovers:
                    layover_details = []
                    for layover in layovers:
                        duration = layover.get("duration", 0)
                        hours = duration // 60
                        minutes = duration % 60
                        layover_details.append(f"{layover.get('id', 'Unknown')} ({hours}h {minutes}m)")
                    result_text += f"\n   Layovers: {', '.join(layover_details)}"

                # Carbon emissions
                carbon = flight.get("carbon_emissions", {})
                if carbon:
                    diff = carbon.get("difference_percent", 0)
                    this_flight = carbon.get("this_flight", 0) / 1000  # Convert to kg
                    result_text += f"\n   Carbon: {this_flight:.0f} kg ({diff:+d}% vs typical)"

                formatted.append(result_text)

        # Process other flights
        other_flights = results.get("other_flights", [])
        if other_flights:
            if formatted:
                formatted.append("\n\n=== Other Flights ===\n")

            for i, flight in enumerate(other_flights[:10], 1):
                result_text = f"{i}. "

                # Flight segments
                segments = []
                for segment in flight.get("flights", []):
                    dep_airport = segment.get("departure_airport", {})
                    arr_airport = segment.get("arrival_airport", {})
                    airline = segment.get("airline", "Unknown")
                    flight_num = segment.get("flight_number", "")

                    seg_text = f"{airline} {flight_num}: {dep_airport.get('id', '')} ({dep_airport.get('time', '')}) → {arr_airport.get('id', '')} ({arr_airport.get('time', '')})"
                    segments.append(seg_text)

                result_text += "\n   ".join(segments)

                # Price and duration
                price = flight.get("price")
                total_duration = flight.get("total_duration")
                if price:
                    result_text += f"\n   Price: ${price}"
                if total_duration:
                    hours = total_duration // 60
                    minutes = total_duration % 60
                    result_text += f" | Duration: {hours}h {minutes}m"

                # Layovers
                layovers = flight.get("layovers", [])
                if layovers:
                    layover_details = []
                    for layover in layovers:
                        duration = layover.get("duration", 0)
                        hours = duration // 60
                        minutes = duration % 60
                        layover_details.append(f"{layover.get('id', 'Unknown')} ({hours}h {minutes}m)")
                    result_text += f"\n   Layovers: {', '.join(layover_details)}"

                # Carbon emissions
                carbon = flight.get("carbon_emissions", {})
                if carbon:
                    diff = carbon.get("difference_percent", 0)
                    this_flight = carbon.get("this_flight", 0) / 1000  # Convert to kg
                    result_text += f"\n   Carbon: {this_flight:.0f} kg ({diff:+d}% vs typical)"

                formatted.append(result_text)

        if not formatted:
            return "No flight results found."

        # Build header
        trip_type = "Round-trip" if return_date else "One-way"
        header = f"Flight Search Results ({trip_type}): {departure_id} → {arrival_id}\n"
        header += f"Outbound: {outbound_date}"
        if return_date:
            header += f" | Return: {return_date}"
        header += "\n\n"

        return header + "\n\n".join(formatted)

    except Exception as e:
        return f"Flight search error: {str(e)}"
