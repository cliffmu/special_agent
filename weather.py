"""
Weather data retrieval and processing for Special Agent.
Functions for retrieving and processing weather data from various sources.
"""

import asyncio
import json
import sys
import subprocess
from typing import Dict, Any

from .logger_helper import log_to_file

async def fetch_weather_data(hass, api_key=None, location_query=None):
    """
    Fetches weather data from all available sources and returns a consolidated result.
    This is the main entry point for weather data retrieval.
    
    Args:
        hass: Home Assistant instance
        api_key: OpenAI API key (for possible future use)
        location_query: Optional location name to get weather for a specific location
        
    Returns:
        Dictionary containing weather data from all sources
    """
    log_to_file("[Weather] Starting weather data collection")
    
    # Get location information first
    location_info = await get_location_info(hass)
    
    # Get local weather data (sensors, forecast entities)
    local_weather = await get_local_weather_sensors(hass)
    
    # Always fetch online weather data for better forecast information
    # and to handle non-local queries
    online_weather = {}
    
    # If a specific location was requested, modify location_info for the API call
    if location_query:
        log_to_file(f"[Weather] Fetching weather for location: {location_query}")
        # We'll use the online API with the location query
        online_weather = await get_online_weather_data(hass, location_info, location_query)
    else:
        # Get online data for the user's location
        online_weather = await get_online_weather_data(hass, location_info)
    
    return {
        "location": location_info,
        "local_sensors": local_weather,
        "online_weather": online_weather
    }

async def get_location_info(hass) -> dict:
    """
    Get the location information for this Home Assistant instance.
    Returns coordinates, postal code, and other location details if available.
    """
    location_info = {}
    
    try:
        # Try to get location from HA configuration
        latitude = hass.config.latitude
        longitude = hass.config.longitude
        
        if latitude and longitude:
            location_info["latitude"] = latitude
            location_info["longitude"] = longitude
        
        # Get location from config
        config_entries = hass.data.get("special_agent", {})
        config_data = next(iter(config_entries.values())) if config_entries else {}
        
        # Use ZIP code from config if available
        if "zip_code" in config_data:
            location_info["postal_code"] = config_data["zip_code"]
            
        # Check for other location data in HA configuration
        if hasattr(hass.config, "city"):
            location_info["city"] = hass.config.city
        
        if hasattr(hass.config, "state"):
            location_info["region"] = hass.config.state
            
        if hasattr(hass.config, "country"):
            location_info["country"] = hass.config.country
            
        log_to_file(f"[Weather] Location info: {location_info}")
        return location_info
        
    except Exception as e:
        log_to_file(f"[Weather] Error getting location info: {e}")
        return {"error": str(e)}


async def get_local_weather_sensors(hass) -> dict:
    """
    Get readings from local weather-related sensors.
    Specifically looks for weather station sensors and excludes indoor sensors.
    """
    weather_data = {}
    
    try:
        # Look for predefined device/station IDs
        config_entries = hass.data.get("special_agent", {})
        config_data = next(iter(config_entries.values())) if config_entries else {}
        weather_station_id = config_data.get("weather_station_id", "washington_weather_station")
        log_to_file(f"[Weather] Looking for weather station with ID: {weather_station_id}")
        
        # Get all states
        all_states = list(hass.states.async_all())
        
        # Log all sensors for debugging
        weather_related_entities = [state.entity_id for state in all_states 
                             if state.entity_id.startswith(('sensor.', 'weather.', 'binary_sensor.')) 
                             and any(keyword in state.entity_id.lower() for keyword in 
                               ['temp', 'humid', 'pressure', 'wind', 'rain', 'precip', 'weather', 'uv'])]
        log_to_file(f"[Weather] All weather-related entities: {', '.join(weather_related_entities)}")
        
        # First check for Home Assistant's integrated weather forecast entity
        forecast_entity = None
        for state in all_states:
            if state.entity_id == "weather.forecast_home":
                forecast_entity = state
                log_to_file(f"[Weather] Found Home Assistant forecast entity: {state.entity_id}")
                log_to_file(f"[Weather] Forecast attributes: {forecast_entity.attributes}")
                
                # Log the forecast data for debugging
                forecast_data = forecast_entity.attributes.get("forecast", [])
                if forecast_data:
                    log_to_file(f"[Weather] Forecast data available: {len(forecast_data)} periods")
                    log_to_file(f"[Weather] First forecast entry: {forecast_data[0]}")
                else:
                    log_to_file("[Weather] No forecast data found in entity attributes")
                    
                break
        
        # If we found the forecast entity, use it as our primary weather source
        if forecast_entity:
            # Get all forecast data
            forecast_data = forecast_entity.attributes.get("forecast", [])
            
            # Process to ensure proper JSON serialization (some HA forecast data might contain datetime objects)
            processed_forecast = []
            for entry in forecast_data:
                # Make a copy to ensure we don't modify the original
                processed_entry = dict(entry)
                
                # Convert any datetime objects to strings if needed
                for key, value in processed_entry.items():
                    if hasattr(value, 'isoformat'):  # Check if it's a datetime-like object
                        processed_entry[key] = value.isoformat()
                
                processed_forecast.append(processed_entry)
            
            weather_data["weather_forecast"] = {
                "condition": forecast_entity.state,
                "temperature": forecast_entity.attributes.get("temperature"),
                "humidity": forecast_entity.attributes.get("humidity"),
                "pressure": forecast_entity.attributes.get("pressure"),
                "wind_speed": forecast_entity.attributes.get("wind_speed"),
                "wind_bearing": forecast_entity.attributes.get("wind_bearing"),
                "forecast": processed_forecast,
                "entity_id": forecast_entity.entity_id
            }
        
        # Find weather-related sensors, but exclude indoor sensors
        indoor_keywords = ['indoor', 'inside', 'interior', 'room']
        
        for state in all_states:
            if not state.entity_id.startswith(('sensor.', 'weather.', 'binary_sensor.')):
                continue
            
            # Skip indoor sensors
            if any(indoor_keyword in state.entity_id.lower() for indoor_keyword in indoor_keywords):
                log_to_file(f"[Weather] Skipping indoor sensor: {state.entity_id}")
                continue
                
            # Look for weather station by ID - use flexible matching
            if weather_station_id:
                # Try exact match first
                exact_match = weather_station_id in state.entity_id.lower()
                
                # Try partial matches for common patterns
                weather_parts = weather_station_id.lower().split('_')
                partial_match = all(part in state.entity_id.lower() for part in weather_parts)
                
                if (exact_match or partial_match):
                    sensor_type = _determine_sensor_type(state)
                    if sensor_type:
                        weather_data[sensor_type] = {
                            "value": state.state,
                            "unit": state.attributes.get("unit_of_measurement", ""),
                            "entity_id": state.entity_id
                        }
                        log_to_file(f"[Weather] Found weather station sensor: {state.entity_id} ({sensor_type})")
            
            # Also look for common weather sensor keywords
            if any(keyword in state.entity_id.lower() for keyword in 
                  ['temperature', 'humidity', 'pressure', 'wind', 'rain', 'weather', 'uv']):
                sensor_type = _determine_sensor_type(state)
                if sensor_type and sensor_type not in weather_data:
                    weather_data[sensor_type] = {
                        "value": state.state,
                        "unit": state.attributes.get("unit_of_measurement", ""),
                        "entity_id": state.entity_id
                    }
                    
        # Also check for integrated weather platforms
        for state in all_states:
            if state.entity_id.startswith('weather.'):
                weather_data["weather_platform"] = {
                    "condition": state.state,
                    "temperature": state.attributes.get("temperature"),
                    "humidity": state.attributes.get("humidity"),
                    "pressure": state.attributes.get("pressure"),
                    "wind_speed": state.attributes.get("wind_speed"),
                    "wind_bearing": state.attributes.get("wind_bearing"),
                    "entity_id": state.entity_id
                }
                break
                
        log_to_file(f"[Weather] Found {len(weather_data)} local weather sensors")
        return weather_data
        
    except Exception as e:
        log_to_file(f"[Weather] Error getting local weather sensors: {e}")
        return {"error": str(e)}


async def get_online_weather_data(hass, location_info=None, location_query=None) -> dict:
    """
    Get weather data from online sources using the Open-Meteo API.
    Uses the location from Home Assistant config or provided location_info.
    
    Args:
        hass: Home Assistant instance
        location_info: Optional dictionary with location details
        location_query: Optional string with location name to search for (e.g. "San Francisco")
        
    Returns:
        Dictionary with weather data or error information
    """
    try:
        # If location_info not provided, get it
        if not location_info:
            location_info = await get_location_info(hass)
        
        latitude = None
        longitude = None
        
        # If we have a location query, we need to geocode it
        if location_query:
            log_to_file(f"[Weather] Geocoding location query: {location_query}")
            try:
                # Make sure aiohttp is installed
                try:
                    import aiohttp
                except ImportError:
                    log_to_file("[Weather] aiohttp module not found, installing...")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
                    import aiohttp
                    log_to_file("[Weather] Successfully installed aiohttp module")
                
                # Use OpenMeteo geocoding API to get coordinates
                geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location_query}&count=1&language=en&format=json"
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(geocode_url) as response:
                        if response.status == 200:
                            geocode_data = await response.json()
                            
                            if geocode_data.get("results") and len(geocode_data["results"]) > 0:
                                result = geocode_data["results"][0]
                                latitude = result.get("latitude")
                                longitude = result.get("longitude")
                                log_to_file(f"[Weather] Geocoded {location_query} to lat: {latitude}, lon: {longitude}")
                                
                                # Add location name to location_info for LLM context
                                location_info["queried_location"] = {
                                    "name": result.get("name"),
                                    "country": result.get("country"),
                                    "admin1": result.get("admin1"),  # state/province
                                    "latitude": latitude,
                                    "longitude": longitude
                                }
                            else:
                                log_to_file(f"[Weather] Could not geocode location: {location_query}")
                                return {"error": f"Could not find location: {location_query}"}
                        else:
                            log_to_file(f"[Weather] Geocoding API error: {response.status}")
                            return {"error": f"Geocoding API error: {response.status}"}
            except Exception as e:
                log_to_file(f"[Weather] Error during geocoding: {e}")
                return {"error": f"Error looking up location coordinates: {e}"}
        else:
            # Use coordinates from Home Assistant config
            if not location_info.get("latitude") or not location_info.get("longitude"):
                return {"error": "No location coordinates available"}
                
            latitude = location_info["latitude"]
            longitude = location_info["longitude"]
        
        # Make sure aiohttp is installed
        try:
            import aiohttp
        except ImportError:
            log_to_file("[Weather] aiohttp module not found, installing...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
                import aiohttp
                log_to_file("[Weather] Successfully installed aiohttp module")
            except Exception as e:
                log_to_file(f"[Weather] Failed to install aiohttp: {e}")
                return {"error": f"Failed to install required module: {e}"}
        
        # Use Open-Meteo API (free, no API key required)
        url = (f"https://api.open-meteo.com/v1/forecast?"
               f"latitude={latitude}&longitude={longitude}"
               f"&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
               f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
               f"&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,weather_code"
               f"&forecast_days=7"
               f"&timeformat=unixtime&timezone=auto")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    log_to_file(f"[Weather] Retrieved online weather data with {len(data.get('daily', {}).get('time', []))} daily forecasts")
                    
                    # Add location context if we have it from geocoding
                    if location_query and "queried_location" in location_info:
                        data["location"] = location_info["queried_location"]
                    
                    return {
                        "source": "open-meteo",
                        "data": data
                    }
                else:
                    error_text = await response.text()
                    log_to_file(f"[Weather] Error fetching weather: {response.status} - {error_text}")
                    return {"error": f"API error: {response.status}"}
                    
    except Exception as e:
        log_to_file(f"[Weather] Error getting online weather: {e}")
        return {"error": str(e)}


def _determine_sensor_type(state) -> str:
    """
    Determine the type of weather sensor based on entity ID and attributes.
    Returns a standardized sensor type name or None if not weather-related.
    """
    entity_id = state.entity_id.lower()
    
    # Quick check if it's likely a weather sensor
    if not any(keyword in entity_id for keyword in 
              ['temp', 'humid', 'pressure', 'wind', 'rain', 'precip', 'weather', 'uv']):
        return None
        
    # Map entity to sensor type
    if 'temperature' in entity_id:
        return 'temperature'
    elif 'humidity' in entity_id:
        return 'humidity'
    elif 'pressure' in entity_id or 'barometer' in entity_id:
        return 'pressure'
    elif 'wind_speed' in entity_id:
        return 'wind_speed'
    elif 'wind_direction' in entity_id or 'wind_bearing' in entity_id:
        return 'wind_direction'
    elif 'rain' in entity_id or 'precip' in entity_id:
        return 'precipitation'
    elif 'uv' in entity_id or 'ultraviolet' in entity_id:
        return 'uv_index'
    elif 'weather' in entity_id:
        return 'weather_condition'
        
    # Check units to guess type
    unit = state.attributes.get("unit_of_measurement", "").lower()
    if unit in ['°c', '°f', 'c', 'f']:
        return 'temperature'
    elif unit in ['%', 'rh']:
        return 'humidity'
    elif unit in ['hpa', 'mbar', 'inhg']:
        return 'pressure'
    elif unit in ['m/s', 'mph', 'km/h', 'kn']:
        return 'wind_speed'
    elif unit in ['°', 'deg']:
        return 'wind_direction'
    elif unit in ['mm', 'in', 'mm/h', 'in/h']:
        return 'precipitation'
    elif unit in ['uv', 'index', '']:
        # UV index often has no unit or simply 'index'
        if 'uv' in entity_id or 'index' in entity_id:
            return 'uv_index'
        
    return None