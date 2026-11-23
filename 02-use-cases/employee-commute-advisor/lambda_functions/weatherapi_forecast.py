import os
import json
import urllib3
from datetime import datetime
from urllib.parse import quote

http = urllib3.PoolManager()

def lambda_handler(event, context):
    """
    Lambda function to fetch weather forecast data from WeatherAPI.
    Returns weather conditions that could impact commute.
    """
    
    # Get API key from environment
    WEATHERAPI_KEY = os.environ.get('WEATHERAPI_KEY')
    if not WEATHERAPI_KEY:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'WeatherAPI key not configured'})
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
        location = params.get('location')
        forecast_days = params.get('forecast_days', 1)  # Default to 1 day
        
        if not location:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameter: location'
                })
            }
        
        # Validate forecast_days
        if not isinstance(forecast_days, int) or forecast_days < 1 or forecast_days > 3:
            forecast_days = 1
        
        print(f"Fetching weather forecast for: {location}, days: {forecast_days}")
        
        # Fetch weather data
        weather_data = fetch_weather_forecast(location, forecast_days, WEATHERAPI_KEY)
        
        if not weather_data:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Failed to fetch weather data for location: {location}'
                })
            }
        
        # Format response
        response_data = format_weather_response(weather_data, location)
        
        print(f"Successfully fetched weather: {response_data['current_condition']}, "
              f"temp {response_data['current_temp_c']}°C")
        
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


def fetch_weather_forecast(location, days, api_key):
    """
    Fetch weather forecast from WeatherAPI.
    
    Args:
        location: Location string (address, city, coordinates)
        days: Number of forecast days (1-3)
        api_key: WeatherAPI key
    
    Returns:
        Dictionary with weather data or None if request fails
    """
    try:
        # URL encode the location
        encoded_location = quote(location)
        
        # WeatherAPI forecast endpoint
        url = f"http://api.weatherapi.com/v1/forecast.json"
        url += f"?key={api_key}&q={encoded_location}&days={days}&aqi=no&alerts=yes"
        
        # Log only the endpoint being called (without sensitive parameters)
        print(f"Fetching weather data via WeatherAPI")
        
        # Make the API request
        response = http.request('GET', url)
        
        if response.status != 200:
            print(f"Weather API failed with status {response.status}: {response.data}")
            return None
        
        data = json.loads(response.data.decode('utf-8'))
        return data
        
    except Exception as e:
        print(f"Error fetching weather: {str(e)}")
        return None


def format_weather_response(weather_data, location):
    """
    Format the WeatherAPI response into our expected format.
    
    Args:
        weather_data: Raw weather data from WeatherAPI
        location: Location string
    
    Returns:
        Dictionary with formatted weather data
    """
    try:
        current = weather_data.get('current', {})
        forecast = weather_data.get('forecast', {})
        location_data = weather_data.get('location', {})
        
        # Extract current conditions
        current_temp_c = current.get('temp_c', 0)
        current_temp_f = current.get('temp_f', 0)
        feels_like_c = current.get('feelslike_c', 0)
        condition = current.get('condition', {}).get('text', 'Unknown')
        
        # Extract weather factors that impact driving
        is_raining = current.get('precip_mm', 0) > 0
        rain_mm = current.get('precip_mm', 0)
        humidity = current.get('humidity', 0)
        visibility_km = current.get('vis_km', 10)
        wind_kph = current.get('wind_kph', 0)
        wind_dir = current.get('wind_dir', 'N')
        
        # Extract forecast for today
        forecast_days = forecast.get('forecastday', [])
        today_forecast = None
        commute_warnings = []
        
        if forecast_days:
            today = forecast_days[0]
            day_data = today.get('day', {})
            
            today_forecast = {
                'max_temp_c': day_data.get('maxtemp_c', 0),
                'min_temp_c': day_data.get('mintemp_c', 0),
                'chance_of_rain': day_data.get('daily_chance_of_rain', 0),
                'total_precip_mm': day_data.get('totalprecip_mm', 0),
                'max_wind_kph': day_data.get('maxwind_kph', 0),
                'condition': day_data.get('condition', {}).get('text', 'Unknown')
            }
            
            # Check hourly forecast for morning commute (6am-10am)
            hours = today.get('hour', [])
            morning_hours = [h for h in hours if 6 <= datetime.fromisoformat(h['time']).hour <= 10]
            
            if morning_hours:
                morning_rain = any(h.get('will_it_rain', 0) == 1 for h in morning_hours)
                morning_snow = any(h.get('will_it_snow', 0) == 1 for h in morning_hours)
                
                if morning_rain:
                    commute_warnings.append("Rain expected during morning commute")
                if morning_snow:
                    commute_warnings.append("Snow expected during morning commute")
        
        # Determine commute impact
        commute_impact = assess_commute_impact(
            is_raining, rain_mm, visibility_km, wind_kph, 
            today_forecast if today_forecast else {}
        )
        
        # Check for weather alerts
        alerts = weather_data.get('alerts', {}).get('alert', [])
        alert_messages = []
        if alerts:
            for alert in alerts:
                alert_messages.append({
                    'headline': alert.get('headline', ''),
                    'severity': alert.get('severity', ''),
                    'event': alert.get('event', '')
                })
                commute_warnings.append(f"Weather Alert: {alert.get('event', 'Unknown')}")
        
        # Build response
        response = {
            'location': {
                'name': location_data.get('name', location),
                'region': location_data.get('region', ''),
                'country': location_data.get('country', ''),
                'localtime': location_data.get('localtime', '')
            },
            'current_temp_c': current_temp_c,
            'current_temp_f': current_temp_f,
            'feels_like_c': feels_like_c,
            'current_condition': condition,
            'is_raining': is_raining,
            'rain_mm': rain_mm,
            'humidity_percent': humidity,
            'visibility_km': visibility_km,
            'wind_kph': wind_kph,
            'wind_direction': wind_dir,
            'forecast_today': today_forecast,
            'commute_impact': commute_impact,
            'commute_warnings': commute_warnings,
            'weather_alerts': alert_messages,
            'timestamp': datetime.now().isoformat()
        }
        
        return response
        
    except Exception as e:
        print(f"Error formatting weather response: {str(e)}")
        # Return a basic response with available data
        return {
            'location': {'name': location},
            'current_temp_c': 0,
            'current_temp_f': 0,
            'feels_like_c': 0,
            'current_condition': 'Unknown',
            'is_raining': False,
            'rain_mm': 0,
            'humidity_percent': 0,
            'visibility_km': 10,
            'wind_kph': 0,
            'wind_direction': 'N',
            'commute_impact': 'Unknown',
            'commute_warnings': ['Error fetching weather data'],
            'weather_alerts': [],
            'timestamp': datetime.now().isoformat()
        }


def assess_commute_impact(is_raining, rain_mm, visibility_km, wind_kph, forecast):
    """
    Assess how weather conditions impact commute.
    
    Args:
        is_raining: Boolean indicating if it's currently raining
        rain_mm: Amount of rain in mm
        visibility_km: Visibility in kilometers
        wind_kph: Wind speed in km/h
        forecast: Today's forecast data
    
    Returns:
        String describing commute impact level and reasons
    """
    impact_factors = []
    impact_level = "Minimal"
    
    # Check precipitation
    if is_raining or rain_mm > 0:
        if rain_mm > 10:
            impact_factors.append("Heavy rain will significantly slow traffic")
            impact_level = "Severe"
        elif rain_mm > 5:
            impact_factors.append("Moderate rain may slow traffic")
            impact_level = "Moderate"
        else:
            impact_factors.append("Light rain may cause minor delays")
            if impact_level == "Minimal":
                impact_level = "Minor"
    
    # Check forecast precipitation
    if forecast and forecast.get('chance_of_rain', 0) > 70:
        impact_factors.append(f"{forecast['chance_of_rain']}% chance of rain today")
        if impact_level == "Minimal":
            impact_level = "Minor"
    
    # Check visibility
    if visibility_km < 1:
        impact_factors.append("Very poor visibility, hazardous driving conditions")
        impact_level = "Severe"
    elif visibility_km < 3:
        impact_factors.append("Poor visibility may slow traffic")
        if impact_level not in ["Severe", "Moderate"]:
            impact_level = "Moderate"
    elif visibility_km < 5:
        impact_factors.append("Reduced visibility")
        if impact_level == "Minimal":
            impact_level = "Minor"
    
    # Check wind
    if wind_kph > 50:
        impact_factors.append("Strong winds may affect driving, especially high-sided vehicles")
        if impact_level not in ["Severe"]:
            impact_level = "Moderate"
    elif wind_kph > 30:
        impact_factors.append("Moderate winds")
        if impact_level == "Minimal":
            impact_level = "Minor"
    
    # Build impact message
    if not impact_factors:
        return "Minimal - Weather conditions are favorable for commute"
    
    impact_message = f"{impact_level} - " + "; ".join(impact_factors)
    return impact_message
