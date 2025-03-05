from __future__ import annotations

import asyncio
import os
import json
import logging
import aiohttp
import datetime
from typing import Tuple, Dict, List, Optional, Any
from collections import defaultdict

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
# Use constant imports for location
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_LATITUDE, CONF_LONGITUDE

from .entity_refinement import filter_irrelevant_entities, rerank_and_filter_docs
from .vector_index import build_vector_index, query_vector_index
from .logger_helper import log_to_file

from homeassistant.core import HomeAssistant

def get_ha_states(hass):
    """
    Retrieve the full list of Home Assistant device states that are exposed to the assistant.
    It checks for the attribute "conversation_exposed" on each state; if missing, defaults to True.
    """
    devices = []
    for state in hass.states.all():
        exposed = state.attributes.get("conversation_exposed", True)
        # log_to_file(f"[DataSources] Device: {state.entity_id}, exposed: {exposed}")
        # log_to_file(f"[DataSources] RAW: {state}")
        # Only include devices that are exposed
        if exposed:
            devices.append({
                "entity_id": state.entity_id,
                "name": state.name,
                # "state": state.state,
                "attributes": state.attributes,
                # "exposed": exposed
                "domain": state.domain
                # "object_id": state.object_id
            })
    log_to_file(f"[DataSources] Retrieved {len(devices)} exposed HA device states.")
    return devices

def execute_ha_command(command, hass=None):
    """
    Actually call hass.services.call if it's a dict with 'service' and 'data'.
    Otherwise, log and do nothing.
    """
    log_to_file(f"[DataSources] Executing command: {command}")

    # return True
    
    if isinstance(command, dict) and "service" in command and "data" in command:
        # Example: command["service"] = "light.turn_on"
        #          command["data"] = {"entity_id": "light.office_outdoor_spotlight_right", "xy_color": [0.55, 0.41]}
        service_parts = command["service"].split(".")
        if len(service_parts) == 2:
            domain, service_name = service_parts

            if hass is None:
                log_to_file("[execute_ha_command] Error: No hass instance provided.")
                return False

            try:
                # Call the Home Assistant service
                hass.services.call(domain, service_name, command["data"], blocking=True)
                log_to_file(f"[execute_ha_command] Called service {domain}.{service_name} with {command['data']}")
                return True
            except Exception as e:
                log_to_file(f"[execute_ha_command] Error calling {domain}.{service_name}: {str(e)}")
                return False
        else:
            log_to_file(f"[execute_ha_command] Invalid service format: {command['service']}")
            return False
    else:
        log_to_file(f"[execute_ha_command] Command not recognized format: {command}")
        return False


_LOGGER = logging.getLogger(__name__)

async def get_devices_by_area(hass: HomeAssistant) -> Tuple[Dict, List[Dict]]:
    """
    Retrieves devices from the HA device registry, along with
    their assigned areas and associated entity domains.

    Returns:
      summary_dict: A nested dict of the form:
        {
          "Living Room": {"light": 2, "media_player": 1},
          "Kitchen":     {"light": 3},
          ...
        }

      devices_detail_list: A list of device details, for each device:
        [
          {
            "id": "abcd1234",
            "name": "Kitchen Ceiling Light",
            "area": "Kitchen",
            "domains": ["light"],
            "manufacturer": "...",
            "model": "..."
          },
          ...
        ]
    """
    area_reg = ar.async_get(hass)
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    # area_id -> area_name
    area_map = {area.id: area.name for area in area_reg.areas.values()}

    # For convenience
    devices = device_reg.devices  # dict: device_id -> DeviceEntry
    entities = entity_reg.entities  # dict: entity_id -> EntityEntry

    # Build device_id -> list of entity entries
    device_entities_map = defaultdict(list)
    for entity_entry in entities.values():
        if entity_entry.device_id:
            device_entities_map[entity_entry.device_id].append(entity_entry)


    summary_dict = defaultdict(lambda: defaultdict(int))
    devices_detail_list = []
    
    
async def get_location_info(hass: HomeAssistant) -> dict:
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
            
            # If we have a ZIP code but no coordinates, we could potentially
            # look up the coordinates using an external service here
            
        # Check for other location data in HA configuration
        if hasattr(hass.config, "city"):
            location_info["city"] = hass.config.city
        
        if hasattr(hass.config, "state"):
            location_info["region"] = hass.config.state
            
        if hasattr(hass.config, "country"):
            location_info["country"] = hass.config.country
            
        log_to_file(f"[DataSources] Location info: {location_info}")
        return location_info
        
    except Exception as e:
        log_to_file(f"[DataSources] Error getting location info: {e}")
        return {"error": str(e)}


async def get_local_weather_sensors(hass: HomeAssistant) -> dict:
    """
    Get readings from local weather-related sensors.
    Specifically looks for weather station sensors.
    """
    weather_data = {}
    
    try:
        # Look for predefined device/station IDs
        config_entries = hass.data.get("special_agent", {})
        config_data = next(iter(config_entries.values())) if config_entries else {}
        weather_station_id = config_data.get("weather_station_id", "washington_weather_station")
        
        # Get all states - use a sync method since we're running in our own async context
        all_states = list(hass.states.async_all())
        
        # Find weather-related sensors
        for state in all_states:
            if not state.entity_id.startswith(('sensor.', 'weather.', 'binary_sensor.')):
                continue
                
            # Look for weather station by ID
            if weather_station_id and weather_station_id in state.entity_id.lower():
                sensor_type = _determine_sensor_type(state)
                if sensor_type:
                    weather_data[sensor_type] = {
                        "value": state.state,
                        "unit": state.attributes.get("unit_of_measurement", ""),
                        "entity_id": state.entity_id
                    }
            
            # Also look for common weather sensor keywords
            if any(keyword in state.entity_id.lower() for keyword in 
                  ['temperature', 'humidity', 'pressure', 'wind', 'rain', 'weather']):
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
                
        log_to_file(f"[DataSources] Found {len(weather_data)} local weather sensors")
        return weather_data
        
    except Exception as e:
        log_to_file(f"[DataSources] Error getting local weather sensors: {e}")
        return {"error": str(e)}
        
        
async def get_online_weather_data(hass: HomeAssistant) -> dict:
    """
    Get weather data from online sources using the Open-Meteo API.
    Uses the location from Home Assistant config.
    """
    try:
        location = await get_location_info(hass)
        
        if not location.get("latitude") or not location.get("longitude"):
            return {"error": "No location coordinates available"}
            
        latitude = location["latitude"]
        longitude = location["longitude"]
        
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
                    log_to_file(f"[DataSources] Retrieved online weather data")
                    return {
                        "source": "open-meteo",
                        "data": data
                    }
                else:
                    error_text = await response.text()
                    log_to_file(f"[DataSources] Error fetching weather: {response.status} - {error_text}")
                    return {"error": f"API error: {response.status}"}
                    
    except Exception as e:
        log_to_file(f"[DataSources] Error getting online weather: {e}")
        return {"error": str(e)}


def _determine_sensor_type(state) -> Optional[str]:
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

    for device_id, device_entry in devices.items():
        area_name = area_map.get(device_entry.area_id, "Unassigned")

        # Collect all domains found for the entity(ies) of this device
        domains_found = set()
        for ent in device_entities_map[device_id]:
            domain = ent.entity_id.split(".")[0]  # "light.kitchen_ceiling" => "light"
            domains_found.add(domain)

        # Create a device details dict
        device_info = {
            "id": device_id,
            "name": device_entry.name or f"Device {device_id}",
            "area": area_name,
            "domains": list(domains_found),
            "manufacturer": device_entry.manufacturer,
            "model": device_entry.model,
        }
        devices_detail_list.append(device_info)

        # For each domain on this device, increment the count
        for d in domains_found:
            summary_dict[area_name][d] += 1

    # Convert nested defaultdicts to normal dicts
    summary_dict = {area: dict(domains) for area, domains in summary_dict.items()}

    return summary_dict, devices_detail_list

# async def async_rebuild_database(hass: HomeAssistant):
#     """
#     Long-running background job that:
#       1) Retrieves devices & areas (await get_devices_by_area).
#       2) Retrieves all HA states, filters them, builds vector index.
#       3) Saves to your data folder.
#     """
#     log_to_file("[DataSources] async_rebuild_database: START.")
    
#     try:
#         # 1) Devices & areas
#         summary_dict, devices_list = await get_devices_by_area(hass)
#         log_to_file(f"[DataSources] Retrieved {len(devices_list)} devices.")

#         # 2) HA states -> Filter -> Build vector index
#         #    Pass force_rebuild=True so you always get a fresh index
#         all_ha_states = get_ha_states(hass)
#         primary_states = filter_irrelevant_entities(all_ha_states)
#         index_data = build_vector_index(
#             primary_states,
#             openai_api_key="YOUR_OPENAI_KEY",  # or from config
#             force_rebuild=True
#         )

#         # 3) Store device summary to JSON in your "data" folder
#         data_folder = os.path.join(os.path.dirname(__file__), "data")
#         os.makedirs(data_folder, exist_ok=True)

#         summary_file = os.path.join(data_folder, "device_area_summary.json")
#         with open(summary_file, "w", encoding="utf-8") as f:
#             json.dump(
#                 {"summary": summary_dict, "devices_list": devices_list},
#                 f,
#                 indent=2
#             )

#         log_to_file("[DataSources] async_rebuild_database: SUCCESS. All data updated.")
    
#     except Exception as e:
#         log_to_file(f"[DataSources] async_rebuild_database: ERROR => {e}")
    
#     log_to_file("[DataSources] async_rebuild_database: END.")







# _LOGGER = logging.getLogger(__name__)

# async def get_devices_by_area(hass: HomeAssistant) -> Tuple[Dict, List[Dict]]:
#     """
#     Retrieves devices from the HA device registry, along with
#     their assigned areas and associated entity domains.

#     Returns:
#       summary_dict: A nested dict of the form:
#         {
#           "Living Room": {"light": 2, "media_player": 1},
#           "Kitchen":     {"light": 3},
#           ...
#         }

#       devices_detail_list: A list of device details, like:
#         [
#           {
#             "id": "abcd1234",
#             "name": "Kitchen Ceiling Light",
#             "area": "Kitchen",
#             "domains": ["light"],
#             "manufacturer": "...",
#             "model": "..."
#           },
#           ...
#         ]
#     """

#     # Modern HA registry helpers do NOT require 'await' — they are immediate lookups.
#     area_reg = ar.async_get(hass)
#     device_reg = dr.async_get(hass)
#     entity_reg = er.async_get(hass)

#     # Build a map of area_id -> area name
#     area_map = {area.id: area.name for area in area_reg.areas.values()}

#     # For convenience
#     devices = device_reg.devices  # dict of device_id -> DeviceEntry
#     entities = entity_reg.entities  # dict of entity_id -> EntityEntry

#     # Build device_id -> list of entity entries
#     device_entities_map = defaultdict(list)
#     for entity_entry in entities.values():
#         if entity_entry.device_id:
#             device_entities_map[entity_entry.device_id].append(entity_entry)

#     summary_dict = defaultdict(lambda: defaultdict(int))
#     devices_detail_list = []

#     # Iterate over all devices in the registry
#     for device_id, device_entry in devices.items():
#         # Resolve area name
#         area_name = area_map.get(device_entry.area_id, "Unassigned")

#         # Gather all domains used by entities of this device
#         domains_found = set()
#         for ent in device_entities_map[device_id]:
#             domain = ent.entity_id.split(".")[0]  # e.g. "light.kitchen_ceiling" => "light"
#             domains_found.add(domain)

#         # Create a device detail dict
#         device_info = {
#             "id": device_id,
#             "name": device_entry.name or f"Device {device_id}",
#             "area": area_name,
#             "domains": list(domains_found),
#             "manufacturer": device_entry.manufacturer,
#             "model": device_entry.model,
#         }
#         devices_detail_list.append(device_info)

#         # Populate the summary (area -> domain -> count of devices)
#         for d in domains_found:
#             summary_dict[area_name][d] += 1

#     # Convert summary to normal dict for easier logging or JSON serialization
#     summary_dict = {area: dict(domains) for area, domains in summary_dict.items()}

#     return summary_dict, devices_detail_list
