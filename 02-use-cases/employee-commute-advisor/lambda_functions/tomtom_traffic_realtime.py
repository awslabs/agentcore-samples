import os
import json
import urllib3
from datetime import datetime, timedelta
from urllib.parse import quote

http = urllib3.PoolManager()

def lambda_handler(event, context):
    """
    Lambda function to fetch real-time traffic data from TomTom APIs.
    Includes geocoding to convert addresses to coordinates and routing with traffic.
    """
    
    # Get API key from environment
    TOMTOM_API_KEY = os.environ.get('TOMTOM_API_KEY')
    if not TOMTOM_API_KEY:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'TomTom API key not configured'})
        }
    
    # Extract tool name from context when invoked by Gateway
    tool_name = None
    if hasattr(context, 'client_context') and context.client_context:
        # Fix: Use attribute access instead of dictionary access
        custom = context.client_context.custom if hasattr(context.client_context, 'custom') else {}
        tool_name = custom.get('bedrockagentcoreToolName') if isinstance(custom, dict) else None
    
    # Parse input - handle both direct invocation and Gateway invocation
    if isinstance(event, dict):
        params = event
    else:
        params = json.loads(event) if isinstance(event, str) else {}
    
    try:
        # Extract parameters
        from_address = params.get('from_address')
        to_address = params.get('to_address')
        departure_time = params.get('departure_time', datetime.now().isoformat())
        
        if not from_address or not to_address:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameters: from_address and to_address'
                })
            }
        
        # Step 1: Geocode the addresses to get coordinates
        print(f"Geocoding from address: {from_address}")
        from_coords = geocode_address(from_address, TOMTOM_API_KEY)
        if not from_coords:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Could not geocode from address: {from_address}'
                })
            }
        
        print(f"Geocoding to address: {to_address}")
        to_coords = geocode_address(to_address, TOMTOM_API_KEY)
        if not to_coords:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Could not geocode to address: {to_address}'
                })
            }
        
        print(f"From coordinates: {from_coords}")
        print(f"To coordinates: {to_coords}")
        
        # Step 2: Calculate route with real-time traffic
        route_data = calculate_route_with_traffic(
            from_coords, 
            to_coords, 
            TOMTOM_API_KEY,
            departure_time
        )
        
        if not route_data:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Failed to calculate route'
                })
            }
        
        # Step 3: Format response
        response_data = format_response(
            route_data, 
            from_address, 
            to_address, 
            departure_time
        )
        
        print(f"Successfully calculated route: {response_data['distance_km']}km, "
              f"{response_data['travel_time_with_traffic_minutes']}min with traffic")
        
        return {
            'statusCode': 200,
            'body': json.dumps(response_data)
        }
        
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def geocode_address(address, api_key):
    """
    Convert an address to coordinates using TomTom Search API.
    
    Args:
        address: The address string to geocode
        api_key: TomTom API key
    
    Returns:
        Tuple of (latitude, longitude) or None if geocoding fails
    """
    try:
        # URL encode the address
        encoded_address = quote(address)
        
        # TomTom Search API endpoint
        url = f"https://api.tomtom.com/search/2/search/{encoded_address}.json"
        url += f"?key={api_key}&limit=1&typeahead=false"
        
        # Log only the endpoint being called (without sensitive parameters)
        print(f"Geocoding address via TomTom Search API")
        
        # Make the API request
        response = http.request('GET', url)
        
        if response.status != 200:
            print(f"Geocoding failed with status {response.status}: {response.data}")
            return None
        
        data = json.loads(response.data.decode('utf-8'))
        
        # Extract coordinates from the first result
        if data.get('results') and len(data['results']) > 0:
            result = data['results'][0]
            position = result.get('position', {})
            lat = position.get('lat')
            lon = position.get('lon')
            
            if lat and lon:
                return (lat, lon)
        
        print(f"No results found for address: {address}")
        return None
        
    except Exception as e:
        print(f"Error geocoding address: {str(e)}")
        return None


def calculate_route_with_traffic(from_coords, to_coords, api_key, departure_time_str):
    """
    Calculate route with real-time traffic using TomTom Routing API.
    
    Args:
        from_coords: Tuple of (latitude, longitude) for origin
        to_coords: Tuple of (latitude, longitude) for destination
        api_key: TomTom API key
        departure_time_str: ISO format departure time string
    
    Returns:
        Dictionary with route data or None if routing fails
    """
    try:
        # Format coordinates for the API
        origin = f"{from_coords[0]},{from_coords[1]}"
        destination = f"{to_coords[0]},{to_coords[1]}"
        
        # TomTom Routing API endpoint
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{origin}:{destination}/json"
        
        # Parse departure time
        try:
            departure_dt = datetime.fromisoformat(departure_time_str.replace('Z', '+00:00'))
        except:
            departure_dt = datetime.now()
        
        # Build query parameters
        params = [
            f"key={api_key}",
            f"traffic=true",  # Enable real-time traffic
            f"departAt={departure_dt.strftime('%Y-%m-%dT%H:%M:%S')}",
            f"travelMode=car",
            f"routeType=fastest",
            f"computeTravelTimeFor=all",  # Get all time calculations
            f"sectionType=traffic",  # Include traffic sections
            f"report=effectiveSettings"
        ]
        
        full_url = url + "?" + "&".join(params)
        
        # Log only the endpoint being called (without sensitive parameters)
        print(f"Calculating route via TomTom Routing API")
        
        # Make the API request
        response = http.request('GET', full_url)
        
        if response.status != 200:
            print(f"Routing failed with status {response.status}: {response.data}")
            return None
        
        data = json.loads(response.data.decode('utf-8'))
        
        # Extract route information
        if data.get('routes') and len(data['routes']) > 0:
            route = data['routes'][0]
            return route
        
        print("No routes found")
        return None
        
    except Exception as e:
        print(f"Error calculating route: {str(e)}")
        return None


def format_response(route_data, from_address, to_address, departure_time_str):
    """
    Format the TomTom API response into our expected format.
    
    Args:
        route_data: Raw route data from TomTom API
        from_address: Origin address string
        to_address: Destination address string
        departure_time_str: ISO format departure time string
    
    Returns:
        Dictionary with formatted response data
    """
    try:
        summary = route_data.get('summary', {})
        
        # Extract travel times (in seconds from API)
        travel_time_seconds = summary.get('travelTimeInSeconds', 0)
        no_traffic_time_seconds = summary.get('noTrafficTravelTimeInSeconds', travel_time_seconds)
        traffic_delay_seconds = summary.get('trafficDelayInSeconds', 0)
        
        # Convert to minutes
        travel_time_minutes = round(travel_time_seconds / 60)
        no_traffic_time_minutes = round(no_traffic_time_seconds / 60)
        traffic_delay_minutes = round(traffic_delay_seconds / 60)
        
        # Extract distance (in meters from API)
        distance_meters = summary.get('lengthInMeters', 0)
        distance_km = round(distance_meters / 1000, 1)
        
        # Parse departure and arrival times
        try:
            departure_dt = datetime.fromisoformat(departure_time_str.replace('Z', '+00:00'))
        except:
            departure_dt = datetime.now()
        
        # Calculate arrival time
        arrival_dt = departure_dt + timedelta(seconds=travel_time_seconds)
        
        # Determine traffic conditions based on delay
        traffic_conditions = get_traffic_conditions(traffic_delay_minutes)
        
        # Extract route description from sections if available
        route_summary = extract_route_summary(route_data)
        
        # Build response
        response = {
            'travel_time_minutes': no_traffic_time_minutes,
            'travel_time_with_traffic_minutes': travel_time_minutes,
            'distance_km': distance_km,
            'traffic_delay_minutes': traffic_delay_minutes,
            'departure_time': departure_dt.isoformat(),
            'estimated_arrival': arrival_dt.isoformat(),
            'route_summary': route_summary,
            'traffic_conditions': traffic_conditions,
            'from_address': from_address,
            'to_address': to_address,
            'historic_travel_time_minutes': round(
                summary.get('historicTrafficTravelTimeInSeconds', travel_time_seconds) / 60
            ) if 'historicTrafficTravelTimeInSeconds' in summary else None
        }
        
        return response
        
    except Exception as e:
        print(f"Error formatting response: {str(e)}")
        # Return a basic response with available data
        return {
            'travel_time_minutes': 0,
            'travel_time_with_traffic_minutes': 0,
            'distance_km': 0,
            'traffic_delay_minutes': 0,
            'departure_time': departure_time_str,
            'estimated_arrival': departure_time_str,
            'route_summary': 'Route calculation error',
            'traffic_conditions': 'Unknown',
            'from_address': from_address,
            'to_address': to_address
        }


def get_traffic_conditions(delay_minutes):
    """
    Determine traffic conditions based on delay.
    
    Args:
        delay_minutes: Traffic delay in minutes
    
    Returns:
        String describing traffic conditions
    """
    if delay_minutes <= 5:
        return "Light"
    elif delay_minutes <= 15:
        return "Moderate"
    elif delay_minutes <= 30:
        return "Heavy"
    else:
        return "Severe"


def extract_route_summary(route_data):
    """
    Extract a readable route summary from the route data.
    
    Args:
        route_data: Raw route data from TomTom API
    
    Returns:
        String with route summary
    """
    try:
        # Look for major roads in the route
        sections = route_data.get('sections', [])
        major_roads = []
        
        for section in sections:
            if section.get('sectionType') == 'TRAVEL_MODE':
                continue
            
            # Try to extract road names or numbers
            if 'roadNumbers' in section:
                for road in section['roadNumbers']:
                    road_name = road.get('fullRoadNumber', road.get('roadNumber', ''))
                    if road_name and road_name not in major_roads:
                        major_roads.append(road_name)
        
        # Build summary
        if major_roads:
            if len(major_roads) == 1:
                return f"Route via {major_roads[0]}"
            elif len(major_roads) == 2:
                return f"Route via {major_roads[0]} and {major_roads[1]}"
            else:
                return f"Route via {', '.join(major_roads[:2])} and others"
        
        # Fallback to basic info
        legs = route_data.get('legs', [])
        if legs:
            return f"Direct route with {len(legs)} segment{'s' if len(legs) > 1 else ''}"
        
        return "Calculated route"
        
    except Exception as e:
        print(f"Error extracting route summary: {str(e)}")
        return "Route calculated"
