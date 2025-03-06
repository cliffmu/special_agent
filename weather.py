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

async def fetch_weather_data(hass, api_key=None):
    """
    Fetches weather data from all available sources and returns a consolidated result.
    This is the main entry point for weather data retrieval.
    
    Args:
        hass: Home Assistant instance
        api_key: OpenAI API key (for possible future use)
        
    Returns:
        Dictionary containing weather data from all sources
    """
    log_to_file("[Weather] Starting weather data collection")
    
    # Get location information first
    location_info = await get_location_info(hass)
    
    # Get local weather data (sensors, forecast entities)
    local_weather = await get_local_weather_sensors(hass)
    
    # Only get online data if we don't have forecast data from Home Assistant
    online_weather = {}
    if not local_weather.get("weather_forecast"):
        log_to_file("[Weather] No Home Assistant forecast found, fetching from online API")
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
                break
        
        # If we found the forecast entity, use it as our primary weather source
        if forecast_entity:
            weather_data["weather_forecast"] = {
                "condition": forecast_entity.state,
                "temperature": forecast_entity.attributes.get("temperature"),
                "humidity": forecast_entity.attributes.get("humidity"),
                "pressure": forecast_entity.attributes.get("pressure"),
                "wind_speed": forecast_entity.attributes.get("wind_speed"),
                "wind_bearing": forecast_entity.attributes.get("wind_bearing"),
                "forecast": forecast_entity.attributes.get("forecast", []),
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


async def get_online_weather_data(hass, location_info=None) -> dict:
    """
    Get weather data from online sources using the Open-Meteo API.
    Uses the location from Home Assistant config or provided location_info.
    """
    try:
        # If location_info not provided, get it
        if not location_info:
            location_info = await get_location_info(hass)
        
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
               f"&timeformat=unixtime&timezone=auto")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    log_to_file(f"[Weather] Retrieved online weather data")
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