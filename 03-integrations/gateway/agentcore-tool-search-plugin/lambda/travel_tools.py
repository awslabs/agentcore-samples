"""
Travel Tools Lambda Function for Amazon Bedrock AgentCore Gateway

This is a plain AWS Lambda function that serves as a tool target for AgentCore Gateway.
It does NOT implement the MCP protocol itself — AgentCore Gateway handles MCP protocol
translation and routes tool calls to this function.

Input format from AgentCore Gateway:
- Event: A map of tool input properties (e.g., {"destination": "New York", "date": "2025-03-15"})
- Context: Contains metadata including the tool name in context.client_context.custom['bedrockAgentCoreToolName']
  The tool name format is: ${target_name}___${tool_name}

The function extracts the tool name from context, strips the target prefix, dispatches to
the appropriate domain handler, and returns a JSON response.

All data is static/in-memory for demonstration purposes.

Domains covered:
- Flights (9 tools): search, details, availability, seat map, baggage, status, connections, airline info, price comparison
- Hotels (4 tools): search, details, room availability, amenities
- Car Rentals (3 tools): search, details, availability
- Restaurants (5 tools): search, details, menu, reservations, reviews
- Currency (3 tools): convert, exchange rates, supported currencies
- Loyalty (3 tools): balance, redeem, program info
- Weather (2 tools): forecast, current
- Activities (3 tools): search, details, availability
- Trip Planning (2 tools): create itinerary, travel tips
"""

DELIMITER = "___"


def lambda_handler(event, context):
    """
    Main Lambda handler. Receives tool invocations from AgentCore Gateway.

    Event: Tool input properties as a flat dict (e.g., {"destination": "New York"})
    Context: client_context.custom contains bedrockAgentCoreToolName with format target___toolname

    Returns a JSON-serializable result (the gateway handles response formatting).
    """
    # Extract tool name from context (format: targetName___toolName)
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        # Fallback: check if tool_name is passed directly in event (for local testing)
        tool_name = event.pop("tool_name", "")

    # The event IS the tool arguments (flat dict of input properties)
    arguments = event

    # Dispatch to the appropriate tool handler
    handler = TOOL_DISPATCH.get(tool_name)

    if handler is None:
        return {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(TOOL_DISPATCH.keys())
        }

    result = handler(arguments)
    return result


# =============================================================================
# FLIGHTS DOMAIN (9 tools)
# =============================================================================

def search_flights(args):
    """Search for available flights based on origin, destination, and date."""
    destination = args.get("destination", "New York")
    origin = args.get("origin", "San Francisco")
    date = args.get("date", "2025-03-15")

    return {
        "flights": [
            {
                "flight_id": "FL-201",
                "airline": "SkyWest Airlines",
                "origin": origin,
                "destination": destination,
                "departure_time": f"{date}T08:00:00",
                "arrival_time": f"{date}T16:30:00",
                "duration": "5h 30m",
                "price": 349.99,
                "currency": "USD",
                "class": "Economy",
                "stops": 0
            },
            {
                "flight_id": "FL-202",
                "airline": "Pacific Air",
                "origin": origin,
                "destination": destination,
                "departure_time": f"{date}T11:45:00",
                "arrival_time": f"{date}T20:00:00",
                "duration": "5h 15m",
                "price": 289.50,
                "currency": "USD",
                "class": "Economy",
                "stops": 0
            },
            {
                "flight_id": "FL-203",
                "airline": "Continental Express",
                "origin": origin,
                "destination": destination,
                "departure_time": f"{date}T14:30:00",
                "arrival_time": f"{date}T23:00:00",
                "duration": "5h 30m",
                "price": 415.00,
                "currency": "USD",
                "class": "Business",
                "stops": 0
            }
        ],
        "total_results": 3,
        "search_criteria": {"origin": origin, "destination": destination, "date": date}
    }


def get_flight_details(args):
    """Get detailed information about a specific flight."""
    flight_id = args.get("flight_id", "FL-201")

    return {
        "flight_id": flight_id,
        "airline": "SkyWest Airlines",
        "flight_number": "SW-1042",
        "origin": {"code": "SFO", "name": "San Francisco International", "terminal": "2"},
        "destination": {"code": "JFK", "name": "John F. Kennedy International", "terminal": "4"},
        "departure_time": "2025-03-15T08:00:00",
        "arrival_time": "2025-03-15T16:30:00",
        "duration": "5h 30m",
        "aircraft": "Boeing 737-800",
        "amenities": ["Wi-Fi", "In-flight entertainment", "USB charging", "Complimentary snacks"],
        "price": {"economy": 349.99, "business": 799.99, "first": 1249.99},
        "currency": "USD"
    }


def check_availability(args):
    """Check seat availability for a specific flight."""
    flight_id = args.get("flight_id", "FL-201")
    travel_class = args.get("class", "Economy")

    return {
        "flight_id": flight_id,
        "class": travel_class,
        "available_seats": 42,
        "total_seats": 180,
        "availability_status": "available",
        "fare": 349.99,
        "currency": "USD",
        "last_updated": "2025-03-10T12:00:00Z"
    }


def get_seat_map(args):
    """Get the seat map for a specific flight."""
    flight_id = args.get("flight_id", "FL-201")

    return {
        "flight_id": flight_id,
        "aircraft": "Boeing 737-800",
        "sections": [
            {
                "class": "First",
                "rows": "1-4",
                "seats_per_row": 4,
                "layout": "2-2",
                "available": 3
            },
            {
                "class": "Business",
                "rows": "5-10",
                "seats_per_row": 6,
                "layout": "3-3",
                "available": 12
            },
            {
                "class": "Economy",
                "rows": "11-35",
                "seats_per_row": 6,
                "layout": "3-3",
                "available": 42
            }
        ],
        "exit_rows": [12, 22],
        "extra_legroom_rows": [12, 13, 22, 23]
    }


def get_baggage_policy(args):
    """Get baggage policy for a specific airline or flight."""
    airline = args.get("airline", "SkyWest Airlines")

    return {
        "airline": airline,
        "carry_on": {
            "allowed": True,
            "max_weight_kg": 10,
            "max_dimensions_cm": "55x40x20",
            "personal_item": True
        },
        "checked_baggage": {
            "first_bag_fee": 30.00,
            "second_bag_fee": 45.00,
            "max_weight_kg": 23,
            "max_dimensions_cm": "158 linear cm"
        },
        "overweight_fee": 100.00,
        "oversize_fee": 150.00,
        "special_items": {
            "sports_equipment": 75.00,
            "musical_instruments": "Carry-on or purchased seat",
            "pets": 125.00
        },
        "currency": "USD"
    }


def get_flight_status(args):
    """Get real-time status of a specific flight."""
    flight_id = args.get("flight_id", "FL-201")

    return {
        "flight_id": flight_id,
        "flight_number": "SW-1042",
        "status": "On Time",
        "departure": {
            "airport": "SFO",
            "scheduled": "2025-03-15T08:00:00",
            "estimated": "2025-03-15T08:00:00",
            "gate": "B22",
            "terminal": "2"
        },
        "arrival": {
            "airport": "JFK",
            "scheduled": "2025-03-15T16:30:00",
            "estimated": "2025-03-15T16:25:00",
            "gate": "A15",
            "terminal": "4"
        },
        "last_updated": "2025-03-15T07:30:00Z"
    }


def search_connecting_flights(args):
    """Search for connecting flight options between two cities."""
    origin = args.get("origin", "San Francisco")
    destination = args.get("destination", "London")
    date = args.get("date", "2025-03-15")

    return {
        "connections": [
            {
                "route_id": "RT-101",
                "total_duration": "14h 45m",
                "total_price": 689.99,
                "legs": [
                    {
                        "flight_id": "FL-301",
                        "origin": origin,
                        "destination": "New York (JFK)",
                        "departure": f"{date}T06:00:00",
                        "arrival": f"{date}T14:30:00",
                        "airline": "SkyWest Airlines"
                    },
                    {
                        "flight_id": "FL-302",
                        "origin": "New York (JFK)",
                        "destination": destination,
                        "departure": f"{date}T17:00:00",
                        "arrival": "2025-03-16T05:45:00",
                        "airline": "Atlantic Airways"
                    }
                ],
                "layover": "2h 30m at JFK"
            },
            {
                "route_id": "RT-102",
                "total_duration": "13h 20m",
                "total_price": 825.00,
                "legs": [
                    {
                        "flight_id": "FL-303",
                        "origin": origin,
                        "destination": "Chicago (ORD)",
                        "departure": f"{date}T07:30:00",
                        "arrival": f"{date}T13:30:00",
                        "airline": "Continental Express"
                    },
                    {
                        "flight_id": "FL-304",
                        "origin": "Chicago (ORD)",
                        "destination": destination,
                        "departure": f"{date}T15:00:00",
                        "arrival": "2025-03-16T04:50:00",
                        "airline": "Atlantic Airways"
                    }
                ],
                "layover": "1h 30m at ORD"
            }
        ],
        "search_criteria": {"origin": origin, "destination": destination, "date": date},
        "currency": "USD"
    }


def get_airline_info(args):
    """Get information about a specific airline."""
    airline = args.get("airline", "SkyWest Airlines")

    airlines_data = {
        "SkyWest Airlines": {
            "name": "SkyWest Airlines",
            "code": "SW",
            "country": "United States",
            "hub_airports": ["SFO", "LAX", "DEN"],
            "alliance": "Star Alliance",
            "fleet_size": 245,
            "founded": 1972,
            "website": "https://www.skywest-example.com",
            "rating": 4.2,
            "on_time_performance": "82%"
        },
        "Pacific Air": {
            "name": "Pacific Air",
            "code": "PA",
            "country": "United States",
            "hub_airports": ["LAX", "HNL"],
            "alliance": "OneWorld",
            "fleet_size": 180,
            "founded": 1985,
            "website": "https://www.pacificair-example.com",
            "rating": 4.0,
            "on_time_performance": "79%"
        }
    }

    return airlines_data.get(airline, {
        "name": airline,
        "code": "XX",
        "country": "Unknown",
        "hub_airports": [],
        "alliance": "Independent",
        "fleet_size": 0,
        "founded": None,
        "website": None,
        "rating": None,
        "on_time_performance": "N/A"
    })


def compare_flight_prices(args):
    """Compare prices for the same route across multiple airlines."""
    origin = args.get("origin", "San Francisco")
    destination = args.get("destination", "New York")
    date = args.get("date", "2025-03-15")

    return {
        "route": {"origin": origin, "destination": destination, "date": date},
        "comparisons": [
            {"airline": "Pacific Air", "price": 289.50, "class": "Economy", "duration": "5h 15m", "stops": 0},
            {"airline": "SkyWest Airlines", "price": 349.99, "class": "Economy", "duration": "5h 30m", "stops": 0},
            {"airline": "Continental Express", "price": 415.00, "class": "Business", "duration": "5h 30m", "stops": 0},
            {"airline": "Budget Wings", "price": 199.99, "class": "Economy", "duration": "7h 45m", "stops": 1}
        ],
        "cheapest": {"airline": "Budget Wings", "price": 199.99},
        "fastest": {"airline": "Pacific Air", "duration": "5h 15m"},
        "currency": "USD"
    }


# =============================================================================
# HOTELS DOMAIN (4 tools)
# =============================================================================

def search_hotels(args):
    """Search for hotels in a specified location."""
    location = args.get("location", "Manhattan, New York")
    check_in = args.get("check_in", "2025-03-15")
    check_out = args.get("check_out", "2025-03-18")
    guests = args.get("guests", 2)

    return {
        "hotels": [
            {
                "hotel_id": "HT-101",
                "name": "Grand Central Hotel",
                "location": location,
                "rating": 4.5,
                "stars": 4,
                "price_per_night": 259.00,
                "total_price": 777.00,
                "amenities": ["Pool", "Gym", "Spa", "Restaurant", "Free Wi-Fi"],
                "distance_to_center": "0.3 miles"
            },
            {
                "hotel_id": "HT-102",
                "name": "The Metropolitan Inn",
                "location": location,
                "rating": 4.2,
                "stars": 3,
                "price_per_night": 189.00,
                "total_price": 567.00,
                "amenities": ["Gym", "Restaurant", "Free Wi-Fi", "Business Center"],
                "distance_to_center": "0.8 miles"
            },
            {
                "hotel_id": "HT-103",
                "name": "Luxury Suites NYC",
                "location": location,
                "rating": 4.8,
                "stars": 5,
                "price_per_night": 499.00,
                "total_price": 1497.00,
                "amenities": ["Pool", "Gym", "Spa", "Restaurant", "Rooftop Bar", "Concierge", "Free Wi-Fi"],
                "distance_to_center": "0.1 miles"
            }
        ],
        "total_results": 3,
        "search_criteria": {
            "location": location,
            "check_in": check_in,
            "check_out": check_out,
            "guests": guests
        },
        "currency": "USD"
    }


def get_hotel_details(args):
    """Get detailed information about a specific hotel."""
    hotel_id = args.get("hotel_id", "HT-101")

    return {
        "hotel_id": hotel_id,
        "name": "Grand Central Hotel",
        "address": "123 Park Avenue, Manhattan, New York, NY 10017",
        "phone": "+1-212-555-0100",
        "rating": 4.5,
        "stars": 4,
        "total_reviews": 2847,
        "check_in_time": "15:00",
        "check_out_time": "11:00",
        "room_types": [
            {"type": "Standard", "price": 259.00, "max_guests": 2, "beds": "1 King"},
            {"type": "Deluxe", "price": 349.00, "max_guests": 3, "beds": "1 King + 1 Sofa"},
            {"type": "Suite", "price": 549.00, "max_guests": 4, "beds": "2 Kings"}
        ],
        "amenities": ["Pool", "Gym", "Spa", "Restaurant", "Bar", "Free Wi-Fi", "Parking", "Concierge"],
        "policies": {
            "cancellation": "Free cancellation up to 24 hours before check-in",
            "pets": "Pets allowed ($50/night fee)",
            "smoking": "Non-smoking property"
        },
        "currency": "USD"
    }


def check_room_availability(args):
    """Check room availability for specific dates."""
    hotel_id = args.get("hotel_id", "HT-101")
    check_in = args.get("check_in", "2025-03-15")
    check_out = args.get("check_out", "2025-03-18")
    room_type = args.get("room_type", "Standard")

    return {
        "hotel_id": hotel_id,
        "room_type": room_type,
        "check_in": check_in,
        "check_out": check_out,
        "available": True,
        "rooms_remaining": 5,
        "price_per_night": 259.00,
        "total_price": 777.00,
        "nights": 3,
        "includes_breakfast": True,
        "currency": "USD"
    }


def get_hotel_amenities(args):
    """Get detailed amenity information for a hotel."""
    hotel_id = args.get("hotel_id", "HT-101")

    return {
        "hotel_id": hotel_id,
        "amenities": {
            "pool": {"available": True, "type": "Indoor heated", "hours": "6:00 AM - 10:00 PM"},
            "gym": {"available": True, "hours": "24/7", "equipment": ["Treadmills", "Weights", "Yoga studio"]},
            "spa": {"available": True, "hours": "9:00 AM - 8:00 PM", "services": ["Massage", "Facial", "Sauna"]},
            "restaurant": {"available": True, "name": "The Park Grill", "cuisine": "American", "hours": "6:30 AM - 11:00 PM"},
            "wifi": {"available": True, "free": True, "speed": "100 Mbps"},
            "parking": {"available": True, "type": "Valet", "price_per_day": 45.00},
            "business_center": {"available": True, "hours": "24/7", "services": ["Printing", "Meeting rooms"]},
            "concierge": {"available": True, "hours": "24/7"}
        },
        "currency": "USD"
    }


# =============================================================================
# CAR RENTALS DOMAIN (3 tools)
# =============================================================================

def search_car_rentals(args):
    """Search for available car rentals at a location."""
    location = args.get("location", "JFK Airport")
    pickup_date = args.get("pickup_date", "2025-03-15")
    return_date = args.get("return_date", "2025-03-18")

    return {
        "rentals": [
            {
                "rental_id": "CR-101",
                "company": "National Car Rental",
                "car_type": "Economy",
                "model": "Toyota Corolla",
                "price_per_day": 45.00,
                "total_price": 135.00,
                "features": ["Automatic", "A/C", "4 seats", "2 bags"],
                "pickup_location": location
            },
            {
                "rental_id": "CR-102",
                "company": "Premier Auto Rentals",
                "car_type": "SUV",
                "model": "Ford Explorer",
                "price_per_day": 85.00,
                "total_price": 255.00,
                "features": ["Automatic", "A/C", "7 seats", "4 bags", "GPS"],
                "pickup_location": location
            },
            {
                "rental_id": "CR-103",
                "company": "Luxury Drive",
                "car_type": "Luxury",
                "model": "BMW 5 Series",
                "price_per_day": 150.00,
                "total_price": 450.00,
                "features": ["Automatic", "A/C", "5 seats", "3 bags", "GPS", "Leather seats"],
                "pickup_location": location
            }
        ],
        "total_results": 3,
        "search_criteria": {
            "location": location,
            "pickup_date": pickup_date,
            "return_date": return_date
        },
        "currency": "USD"
    }


def get_rental_details(args):
    """Get detailed information about a specific car rental."""
    rental_id = args.get("rental_id", "CR-101")

    return {
        "rental_id": rental_id,
        "company": "National Car Rental",
        "car_type": "Economy",
        "model": "Toyota Corolla",
        "year": 2024,
        "color": "Silver",
        "features": ["Automatic transmission", "Air conditioning", "Bluetooth", "USB charging", "Backup camera"],
        "capacity": {"passengers": 4, "bags_large": 1, "bags_small": 2},
        "fuel_policy": "Full-to-full",
        "mileage": "Unlimited",
        "insurance_options": [
            {"type": "Basic", "price_per_day": 15.00, "coverage": "Liability only"},
            {"type": "Full", "price_per_day": 30.00, "coverage": "Comprehensive + collision"}
        ],
        "pickup_location": {"address": "JFK Airport, Terminal 4, Level 1", "hours": "24/7"},
        "requirements": {"min_age": 21, "license": "Valid driver's license", "deposit": 200.00},
        "price_per_day": 45.00,
        "currency": "USD"
    }


def check_car_availability(args):
    """Check availability of a specific car rental."""
    rental_id = args.get("rental_id", "CR-101")
    pickup_date = args.get("pickup_date", "2025-03-15")
    return_date = args.get("return_date", "2025-03-18")

    return {
        "rental_id": rental_id,
        "available": True,
        "cars_remaining": 8,
        "pickup_date": pickup_date,
        "return_date": return_date,
        "price_per_day": 45.00,
        "total_days": 3,
        "total_price": 135.00,
        "extras_available": ["GPS ($10/day)", "Child seat ($8/day)", "Additional driver ($12/day)"],
        "currency": "USD"
    }


# =============================================================================
# RESTAURANTS DOMAIN (5 tools)
# =============================================================================

def search_restaurants(args):
    """Search for restaurants in a specified area."""
    location = args.get("location", "Times Square, New York")
    cuisine = args.get("cuisine", "Italian")

    return {
        "restaurants": [
            {
                "restaurant_id": "RS-101",
                "name": "Bella Italia",
                "cuisine": cuisine,
                "location": location,
                "rating": 4.6,
                "price_range": "$$$",
                "distance": "0.2 miles",
                "open_now": True
            },
            {
                "restaurant_id": "RS-102",
                "name": "Trattoria Roma",
                "cuisine": cuisine,
                "location": location,
                "rating": 4.3,
                "price_range": "$$",
                "distance": "0.5 miles",
                "open_now": True
            },
            {
                "restaurant_id": "RS-103",
                "name": "Il Palazzo",
                "cuisine": cuisine,
                "location": location,
                "rating": 4.8,
                "price_range": "$$$$",
                "distance": "0.7 miles",
                "open_now": False
            }
        ],
        "total_results": 3,
        "search_criteria": {"location": location, "cuisine": cuisine}
    }


def get_restaurant_details(args):
    """Get detailed information about a specific restaurant."""
    restaurant_id = args.get("restaurant_id", "RS-101")

    return {
        "restaurant_id": restaurant_id,
        "name": "Bella Italia",
        "cuisine": "Italian",
        "address": "456 Broadway, Times Square, New York, NY 10036",
        "phone": "+1-212-555-0200",
        "rating": 4.6,
        "total_reviews": 1523,
        "price_range": "$$$",
        "hours": {
            "monday_friday": "11:00 AM - 11:00 PM",
            "saturday": "10:00 AM - 12:00 AM",
            "sunday": "10:00 AM - 10:00 PM"
        },
        "features": ["Outdoor seating", "Private dining", "Full bar", "Live music on weekends"],
        "dress_code": "Smart casual",
        "reservations": "Recommended",
        "parking": "Street parking available"
    }


def get_menu(args):
    """Get the menu for a specific restaurant."""
    restaurant_id = args.get("restaurant_id", "RS-101")

    return {
        "restaurant_id": restaurant_id,
        "restaurant_name": "Bella Italia",
        "menu": {
            "appetizers": [
                {"name": "Bruschetta", "price": 12.00, "description": "Toasted bread with tomatoes, garlic, and basil"},
                {"name": "Calamari Fritti", "price": 15.00, "description": "Crispy fried calamari with marinara sauce"},
                {"name": "Caprese Salad", "price": 14.00, "description": "Fresh mozzarella, tomatoes, and basil"}
            ],
            "main_courses": [
                {"name": "Spaghetti Carbonara", "price": 22.00, "description": "Classic pasta with pancetta and egg"},
                {"name": "Osso Buco", "price": 34.00, "description": "Braised veal shank with gremolata"},
                {"name": "Margherita Pizza", "price": 18.00, "description": "San Marzano tomatoes, mozzarella, fresh basil"},
                {"name": "Risotto ai Funghi", "price": 24.00, "description": "Arborio rice with wild mushrooms and truffle oil"}
            ],
            "desserts": [
                {"name": "Tiramisu", "price": 12.00, "description": "Classic Italian coffee-flavored dessert"},
                {"name": "Panna Cotta", "price": 10.00, "description": "Vanilla cream with berry compote"}
            ]
        },
        "currency": "USD"
    }


def check_reservations(args):
    """Check reservation availability at a restaurant."""
    restaurant_id = args.get("restaurant_id", "RS-101")
    date = args.get("date", "2025-03-15")
    party_size = args.get("party_size", 2)
    time = args.get("time", "19:00")

    return {
        "restaurant_id": restaurant_id,
        "restaurant_name": "Bella Italia",
        "date": date,
        "requested_time": time,
        "party_size": party_size,
        "available": True,
        "available_times": ["18:30", "19:00", "19:30", "20:00", "20:30"],
        "estimated_wait": "No wait with reservation",
        "special_notes": "Window table available for parties of 2"
    }


def get_restaurant_reviews(args):
    """Get reviews for a specific restaurant."""
    restaurant_id = args.get("restaurant_id", "RS-101")

    return {
        "restaurant_id": restaurant_id,
        "restaurant_name": "Bella Italia",
        "average_rating": 4.6,
        "total_reviews": 1523,
        "reviews": [
            {
                "reviewer": "John D.",
                "rating": 5,
                "date": "2025-02-28",
                "comment": "Absolutely fantastic! The carbonara was the best I've had outside of Rome.",
                "helpful_votes": 24
            },
            {
                "reviewer": "Sarah M.",
                "rating": 4,
                "date": "2025-02-25",
                "comment": "Great food and atmosphere. Service was a bit slow during peak hours.",
                "helpful_votes": 18
            },
            {
                "reviewer": "Michael R.",
                "rating": 5,
                "date": "2025-02-20",
                "comment": "The tiramisu is to die for. Romantic setting perfect for date night.",
                "helpful_votes": 31
            }
        ],
        "rating_breakdown": {"5_star": 892, "4_star": 421, "3_star": 142, "2_star": 48, "1_star": 20}
    }


# =============================================================================
# CURRENCY DOMAIN (3 tools)
# =============================================================================

def convert_currency(args):
    """Convert an amount from one currency to another."""
    amount = args.get("amount", 100)
    from_currency = args.get("from_currency", "USD")
    to_currency = args.get("to_currency", "EUR")

    # Static exchange rates for demonstration
    rates = {
        ("USD", "EUR"): 0.92,
        ("USD", "GBP"): 0.79,
        ("USD", "JPY"): 149.50,
        ("USD", "CAD"): 1.36,
        ("USD", "AUD"): 1.53,
        ("EUR", "USD"): 1.09,
        ("GBP", "USD"): 1.27,
        ("JPY", "USD"): 0.0067,
    }

    rate = rates.get((from_currency, to_currency), 1.0)
    converted_amount = round(amount * rate, 2)

    return {
        "from": {"currency": from_currency, "amount": amount},
        "to": {"currency": to_currency, "amount": converted_amount},
        "exchange_rate": rate,
        "last_updated": "2025-03-10T12:00:00Z",
        "source": "Market rate (demo data)"
    }


def get_exchange_rates(args):
    """Get current exchange rates for a base currency."""
    base_currency = args.get("base_currency", "USD")

    return {
        "base_currency": base_currency,
        "rates": {
            "EUR": 0.92,
            "GBP": 0.79,
            "JPY": 149.50,
            "CAD": 1.36,
            "AUD": 1.53,
            "CHF": 0.88,
            "CNY": 7.24,
            "INR": 83.10,
            "MXN": 17.15,
            "BRL": 4.97
        },
        "last_updated": "2025-03-10T12:00:00Z",
        "source": "Demo exchange rates"
    }


def get_supported_currencies(args):
    """Get list of supported currencies for conversion."""
    return {
        "currencies": [
            {"code": "USD", "name": "US Dollar", "symbol": "$"},
            {"code": "EUR", "name": "Euro", "symbol": "\u20ac"},
            {"code": "GBP", "name": "British Pound", "symbol": "\u00a3"},
            {"code": "JPY", "name": "Japanese Yen", "symbol": "\u00a5"},
            {"code": "CAD", "name": "Canadian Dollar", "symbol": "C$"},
            {"code": "AUD", "name": "Australian Dollar", "symbol": "A$"},
            {"code": "CHF", "name": "Swiss Franc", "symbol": "CHF"},
            {"code": "CNY", "name": "Chinese Yuan", "symbol": "\u00a5"},
            {"code": "INR", "name": "Indian Rupee", "symbol": "\u20b9"},
            {"code": "MXN", "name": "Mexican Peso", "symbol": "MX$"},
            {"code": "BRL", "name": "Brazilian Real", "symbol": "R$"}
        ],
        "total": 11
    }


# =============================================================================
# LOYALTY DOMAIN (3 tools)
# =============================================================================

def get_loyalty_balance(args):
    """Get loyalty program points balance for a member."""
    member_id = args.get("member_id", "LM-12345")
    program = args.get("program", "TravelRewards")

    return {
        "member_id": member_id,
        "program": program,
        "points_balance": 47500,
        "tier": "Gold",
        "tier_progress": {"current": 47500, "next_tier": 75000, "next_tier_name": "Platinum"},
        "points_expiring_soon": {"amount": 5000, "expiry_date": "2025-06-30"},
        "recent_activity": [
            {"date": "2025-03-01", "description": "Flight SFO-JFK", "points": 2500, "type": "earned"},
            {"date": "2025-02-15", "description": "Hotel stay - Grand Central", "points": 1200, "type": "earned"},
            {"date": "2025-02-10", "description": "Car rental redemption", "points": -3000, "type": "redeemed"}
        ]
    }


def redeem_points(args):
    """Redeem loyalty points for rewards."""
    member_id = args.get("member_id", "LM-12345")
    points = args.get("points", 5000)
    reward_type = args.get("reward_type", "flight_discount")

    redemption_values = {
        "flight_discount": {"value": points * 0.01, "description": f"${points * 0.01:.2f} off your next flight"},
        "hotel_night": {"value": points * 0.008, "description": f"${points * 0.008:.2f} hotel credit"},
        "car_rental": {"value": points * 0.007, "description": f"${points * 0.007:.2f} rental credit"},
        "gift_card": {"value": points * 0.005, "description": f"${points * 0.005:.2f} gift card"}
    }

    reward = redemption_values.get(reward_type, redemption_values["flight_discount"])

    return {
        "member_id": member_id,
        "redemption": {
            "points_redeemed": points,
            "reward_type": reward_type,
            "value": reward["value"],
            "description": reward["description"],
            "status": "confirmed",
            "confirmation_code": "RDM-78901"
        },
        "remaining_balance": 42500,
        "currency": "USD"
    }


def get_loyalty_program_info(args):
    """Get information about a loyalty program."""
    program = args.get("program", "TravelRewards")

    return {
        "program": program,
        "tiers": [
            {"name": "Silver", "min_points": 0, "benefits": ["Basic earning rate", "Member-only fares"]},
            {"name": "Gold", "min_points": 25000, "benefits": ["1.5x earning rate", "Priority boarding", "Lounge access"]},
            {"name": "Platinum", "min_points": 75000, "benefits": ["2x earning rate", "Free upgrades", "Companion pass"]},
            {"name": "Diamond", "min_points": 150000, "benefits": ["3x earning rate", "All Platinum benefits", "Personal concierge"]}
        ],
        "earning_rates": {
            "flights": "1 point per $1 spent",
            "hotels": "2 points per $1 spent",
            "car_rentals": "1 point per $2 spent",
            "dining": "3 points per $1 spent"
        },
        "redemption_options": ["Flight discounts", "Hotel nights", "Car rental credits", "Gift cards", "Experience packages"],
        "partners": ["SkyWest Airlines", "Grand Central Hotels", "National Car Rental", "Bella Italia Restaurant Group"]
    }


# =============================================================================
# WEATHER DOMAIN (2 tools)
# =============================================================================

def get_weather_forecast(args):
    """Get weather forecast for a location."""
    location = args.get("location", "New York")
    days = args.get("days", 5)

    forecast_data = [
        {"date": "2025-03-15", "high_f": 55, "low_f": 42, "condition": "Partly Cloudy", "precipitation": "10%"},
        {"date": "2025-03-16", "high_f": 58, "low_f": 45, "condition": "Sunny", "precipitation": "0%"},
        {"date": "2025-03-17", "high_f": 52, "low_f": 38, "condition": "Rain", "precipitation": "80%"},
        {"date": "2025-03-18", "high_f": 50, "low_f": 36, "condition": "Cloudy", "precipitation": "30%"},
        {"date": "2025-03-19", "high_f": 60, "low_f": 44, "condition": "Sunny", "precipitation": "5%"},
        {"date": "2025-03-20", "high_f": 62, "low_f": 46, "condition": "Partly Cloudy", "precipitation": "15%"},
        {"date": "2025-03-21", "high_f": 57, "low_f": 41, "condition": "Overcast", "precipitation": "25%"}
    ]

    return {
        "location": location,
        "forecast": forecast_data[:days],
        "units": "Fahrenheit",
        "source": "Demo weather data"
    }


def get_current_weather(args):
    """Get current weather conditions for a location."""
    location = args.get("location", "New York")

    return {
        "location": location,
        "current": {
            "temperature_f": 54,
            "feels_like_f": 50,
            "condition": "Partly Cloudy",
            "humidity": "62%",
            "wind": {"speed_mph": 12, "direction": "NW"},
            "visibility_miles": 10,
            "uv_index": 3
        },
        "updated_at": "2025-03-15T10:30:00Z",
        "units": "Fahrenheit",
        "source": "Demo weather data"
    }


# =============================================================================
# ACTIVITIES DOMAIN (3 tools)
# =============================================================================

def search_activities(args):
    """Search for activities and attractions in a location."""
    location = args.get("location", "New York")
    category = args.get("category", "sightseeing")

    return {
        "activities": [
            {
                "activity_id": "AC-101",
                "name": "Statue of Liberty & Ellis Island Tour",
                "category": "sightseeing",
                "location": location,
                "duration": "4 hours",
                "price": 45.00,
                "rating": 4.7,
                "available": True
            },
            {
                "activity_id": "AC-102",
                "name": "Central Park Bike Tour",
                "category": "outdoor",
                "location": location,
                "duration": "2.5 hours",
                "price": 35.00,
                "rating": 4.5,
                "available": True
            },
            {
                "activity_id": "AC-103",
                "name": "Broadway Show - The Lion King",
                "category": "entertainment",
                "location": location,
                "duration": "2.5 hours",
                "price": 125.00,
                "rating": 4.9,
                "available": True
            },
            {
                "activity_id": "AC-104",
                "name": "NYC Food Walking Tour",
                "category": "food",
                "location": location,
                "duration": "3 hours",
                "price": 65.00,
                "rating": 4.6,
                "available": True
            }
        ],
        "total_results": 4,
        "search_criteria": {"location": location, "category": category},
        "currency": "USD"
    }


def get_activity_details(args):
    """Get detailed information about a specific activity."""
    activity_id = args.get("activity_id", "AC-101")

    return {
        "activity_id": activity_id,
        "name": "Statue of Liberty & Ellis Island Tour",
        "description": "Visit two of New York's most iconic landmarks. Includes ferry tickets, guided tour of Liberty Island, and access to the Ellis Island Immigration Museum.",
        "category": "sightseeing",
        "location": "Battery Park, New York",
        "duration": "4 hours",
        "start_times": ["9:00 AM", "10:30 AM", "12:00 PM", "1:30 PM"],
        "price": {"adult": 45.00, "child": 25.00, "senior": 38.00},
        "includes": ["Ferry tickets", "Guided tour", "Museum access", "Audio guide"],
        "requirements": ["Comfortable walking shoes", "Valid ID for security check"],
        "cancellation_policy": "Free cancellation up to 24 hours before",
        "meeting_point": "Castle Clinton, Battery Park",
        "rating": 4.7,
        "total_reviews": 3420,
        "currency": "USD"
    }


def check_activity_availability(args):
    """Check availability for a specific activity on a date."""
    activity_id = args.get("activity_id", "AC-101")
    date = args.get("date", "2025-03-15")
    participants = args.get("participants", 2)

    return {
        "activity_id": activity_id,
        "activity_name": "Statue of Liberty & Ellis Island Tour",
        "date": date,
        "participants": participants,
        "available_slots": [
            {"time": "9:00 AM", "spots_remaining": 15},
            {"time": "10:30 AM", "spots_remaining": 8},
            {"time": "12:00 PM", "spots_remaining": 22},
            {"time": "1:30 PM", "spots_remaining": 30}
        ],
        "total_price": 45.00 * participants,
        "currency": "USD"
    }


# =============================================================================
# TRIP PLANNING DOMAIN (2 tools)
# =============================================================================

def create_itinerary(args):
    """Create a trip itinerary based on destination and preferences."""
    destination = args.get("destination", "New York")
    days = args.get("days", 3)

    itinerary = {
        "destination": destination,
        "duration": f"{days} days",
        "daily_plan": [
            {
                "day": 1,
                "theme": "Iconic Landmarks",
                "activities": [
                    {"time": "9:00 AM", "activity": "Statue of Liberty & Ellis Island", "duration": "4 hours"},
                    {"time": "1:30 PM", "activity": "Lunch at Battery Park area", "duration": "1 hour"},
                    {"time": "3:00 PM", "activity": "9/11 Memorial & Museum", "duration": "2 hours"},
                    {"time": "6:00 PM", "activity": "Dinner in Tribeca", "duration": "1.5 hours"},
                    {"time": "8:00 PM", "activity": "Brooklyn Bridge walk at sunset", "duration": "1 hour"}
                ]
            },
            {
                "day": 2,
                "theme": "Culture & Entertainment",
                "activities": [
                    {"time": "9:00 AM", "activity": "Metropolitan Museum of Art", "duration": "3 hours"},
                    {"time": "12:30 PM", "activity": "Lunch on Museum Mile", "duration": "1 hour"},
                    {"time": "2:00 PM", "activity": "Central Park walking tour", "duration": "2 hours"},
                    {"time": "5:00 PM", "activity": "Times Square exploration", "duration": "1.5 hours"},
                    {"time": "7:30 PM", "activity": "Broadway show", "duration": "2.5 hours"}
                ]
            },
            {
                "day": 3,
                "theme": "Food & Neighborhoods",
                "activities": [
                    {"time": "9:00 AM", "activity": "Chelsea Market food tour", "duration": "2 hours"},
                    {"time": "11:30 AM", "activity": "High Line walk", "duration": "1.5 hours"},
                    {"time": "1:30 PM", "activity": "Lunch in Greenwich Village", "duration": "1 hour"},
                    {"time": "3:00 PM", "activity": "SoHo shopping", "duration": "2 hours"},
                    {"time": "6:00 PM", "activity": "Farewell dinner in Little Italy", "duration": "2 hours"}
                ]
            }
        ]
    }

    # Trim to requested days
    itinerary["daily_plan"] = itinerary["daily_plan"][:days]

    return {
        "itinerary": itinerary,
        "tips": [
            "Get a MetroCard for unlimited subway rides",
            "Book Broadway tickets in advance for best prices",
            "Wear comfortable walking shoes — NYC is best explored on foot"
        ],
        "estimated_budget": {"low": 150 * days, "high": 350 * days, "currency": "USD"}
    }


def get_travel_tips(args):
    """Get travel tips and recommendations for a destination."""
    destination = args.get("destination", "New York")

    tips = {
        "destination": destination,
        "tips": {
            "general": [
                "Best time to visit: April-June and September-November for mild weather",
                "Get a 7-day unlimited MetroCard ($33) for subway and bus travel",
                "Many museums offer 'pay what you wish' hours",
                "Tipping is expected: 18-20% at restaurants, $1-2 per drink at bars",
                "Download offline maps — cell service can be spotty underground"
            ],
            "safety": [
                "NYC is generally safe for tourists, but stay aware of your surroundings",
                "Keep valuables secure in crowded areas like Times Square and subway",
                "Stick to well-lit streets at night",
                "Use official yellow cabs or ride-sharing apps"
            ],
            "budget": [
                "Free attractions: Central Park, Brooklyn Bridge, Staten Island Ferry, High Line",
                "Eat at food trucks and delis for affordable meals ($8-12)",
                "TKTS booth in Times Square offers same-day Broadway tickets at 20-50% off",
                "Happy hour deals at restaurants typically run 4-7 PM"
            ],
            "packing": [
                "Layers are key — weather can change quickly",
                "Comfortable walking shoes are essential (expect 10-15k steps/day)",
                "Compact umbrella for unexpected rain",
                "Portable phone charger for navigation and photos"
            ]
        },
        "local_phrases": [
            {"phrase": "How do I get to...?", "context": "Asking for directions"},
            {"phrase": "What's good here?", "context": "Asking restaurant staff for recommendations"},
            {"phrase": "Can I get the check?", "context": "Requesting the bill at a restaurant"}
        ]
    }

    return tips


# =============================================================================
# TOOL DISPATCH TABLE
# =============================================================================

TOOL_DISPATCH = {
    # Flights domain (9 tools)
    "search_flights": search_flights,
    "get_flight_details": get_flight_details,
    "check_availability": check_availability,
    "get_seat_map": get_seat_map,
    "get_baggage_policy": get_baggage_policy,
    "get_flight_status": get_flight_status,
    "search_connecting_flights": search_connecting_flights,
    "get_airline_info": get_airline_info,
    "compare_flight_prices": compare_flight_prices,
    # Hotels domain (4 tools)
    "search_hotels": search_hotels,
    "get_hotel_details": get_hotel_details,
    "check_room_availability": check_room_availability,
    "get_hotel_amenities": get_hotel_amenities,
    # Car Rentals domain (3 tools)
    "search_car_rentals": search_car_rentals,
    "get_rental_details": get_rental_details,
    "check_car_availability": check_car_availability,
    # Restaurants domain (5 tools)
    "search_restaurants": search_restaurants,
    "get_restaurant_details": get_restaurant_details,
    "get_menu": get_menu,
    "check_reservations": check_reservations,
    "get_restaurant_reviews": get_restaurant_reviews,
    # Currency domain (3 tools)
    "convert_currency": convert_currency,
    "get_exchange_rates": get_exchange_rates,
    "get_supported_currencies": get_supported_currencies,
    # Loyalty domain (3 tools)
    "get_loyalty_balance": get_loyalty_balance,
    "redeem_points": redeem_points,
    "get_loyalty_program_info": get_loyalty_program_info,
    # Weather domain (2 tools)
    "get_weather_forecast": get_weather_forecast,
    "get_current_weather": get_current_weather,
    # Activities domain (3 tools)
    "search_activities": search_activities,
    "get_activity_details": get_activity_details,
    "check_activity_availability": check_activity_availability,
    # Trip Planning domain (2 tools)
    "create_itinerary": create_itinerary,
    "get_travel_tips": get_travel_tips,
}
