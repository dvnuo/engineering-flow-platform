"""
Weather skill - Get current weather and forecasts (no API key required).

Uses:
- wttr.in (primary) - Simple, no API key
- Open-Meteo (fallback) - JSON API, no API key
"""

import subprocess
from typing import Any, Dict, Optional

from skills.executor import SkillResult, skill

# Skill metadata
SKILL_NAME = "weather"
SKILL_DESCRIPTION = "Get current weather and forecasts (no API key required)"


@skill(name=SKILL_NAME, description=SKILL_DESCRIPTION)
async def weather(
    location: str = "current",
    format_type: str = "compact",
) -> SkillResult:
    """Get weather for a location.
    
    Args:
        location: City name or location (default: current IP location)
        format_type: Output format - "compact", "full", "unicode"
    
    Returns:
        SkillResult with weather information
    
    Examples:
        weather(location="Hong Kong")
        weather(location="Beijing", format_type="full")
    """
    try:
        # Build URL
        url = f"wttr.in/{location}"
        
        # Format options
        formats = {
            "compact": "format=3",  # "London: ⛅️ +8°C"
            "full": "format=4",     # Full details
            "unicode": "format=%l:+%c+%t+%h+%w",  # "London: ⛅️ +8°C 71% ↙5km/h"
            "metric": "m",          # Metric units
        }
        
        format_param = formats.get(format_type, formats["compact"])
        
        # Fetch weather
        result = subprocess.run(
            ["curl", "-s", f"{url}?{format_param}&u"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        output = result.stdout.strip()
        
        if not output or "Error" in output:
            # Try Open-Meteo as fallback
            return await _get_open_meteo(location)
        
        return SkillResult(
            success=True,
            output=f"🌤️ Weather for {location}:\n\n{output}",
            data={
                "location": location,
                "format": format_type,
                "source": "wttr.in"
            }
        )
        
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, output="Request timed out")
    except Exception as e:
        return SkillResult(success=False, output=f"Error: {str(e)}")


async def _get_open_meteo(location: str) -> SkillResult:
    """Get weather from Open-Meteo API (fallback)."""
    # Note: Open-Meteo requires coordinates
    # For now, return helpful message
    return SkillResult(
        success=False,
        output=f"Could not fetch weather for '{location}'. Try using a city name with wttr.in format.",
        data={"source": "open-meteo-fallback", "note": "Coordinates required for Open-Meteo"}
    )


@skill(name="forecast", description="Get weather forecast for upcoming days")
async def forecast(
    location: str = "current",
    days: int = 3,
) -> SkillResult:
    """Get weather forecast.
    
    Args:
        location: City name
        days: Number of days (1-7)
    
    Returns:
        SkillResult with forecast
    """
    try:
        result = subprocess.run(
            ["curl", "-s", f"wttr.in/{location}?{days}d"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        output = result.stdout.strip()
        
        return SkillResult(
            success=True,
            output=f"📅 Forecast for {location} (next {days} days):\n\n{output}",
            data={"location": location, "days": days}
        )
        
    except Exception as e:
        return SkillResult(success=False, output=f"Error: {str(e)}")
