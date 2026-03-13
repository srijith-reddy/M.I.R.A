import re, requests
from typing import Optional, Dict, Any

# Use Nominatim (OpenStreetMap) for geocoding
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

def _extract_location(text: str) -> Optional[str]:
    """Pull location name from user text like 'weather in Hyderabad'."""
    m = re.search(r"(?:weather|forecast)?\s*(?:in|for)\s+([A-Za-z\s,.'-]{2,})", text, re.IGNORECASE)
    return m.group(1).strip() if m else None

def _geocode(place: str) -> Optional[Dict[str, Any]]:
    """Use Nominatim to resolve place name → lat/lon."""
    try:
        r = requests.get(
            _NOMINATIM,
            params={"q": place, "format": "json", "limit": 1, "addressdetails": 1},
            headers={"User-Agent": "MiraVoiceAssistant/1.0"},
            timeout=8,
        )
        r.raise_for_status()
        results = r.json()
        if not results:
            return None
        r0 = results[0]
        addr = r0.get("address", {})

        # ✅ New short-name logic
        short_name = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("state")
            or r0.get("display_name", place).split(",")[0]
        )

        return {
            "latitude": float(r0["lat"]),
            "longitude": float(r0["lon"]),
            "name": short_name.strip(),
            "country": addr.get("country", "").strip()
        }
    except Exception:
        return None

def _wcode_to_text(code: int) -> str:
    """Map Open-Meteo weather codes to readable text."""
    m = {
        0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "freezing fog",
        51: "light drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
        71: "light snow", 73: "snow", 75: "heavy snow",
        95: "thunderstorm"
    }
    return m.get(code, "mixed conditions")

class WeatherAgent:
    """Live weather via Nominatim (geocoding) + Open-Meteo (forecast)."""
    def get_weather(self, text: str) -> str:
        place = _extract_location(text) or text.strip()
        if not place:
            return "Say the location, e.g., ‘weather in Hyderabad’."

        g = _geocode(place)
        if not g:
            return f"I couldn’t find “{place}”. Try another location."

        lat, lon = g["latitude"], g["longitude"]
        nice = g.get("name", place)

        params = {
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": ["temperature_2m", "weather_code", "wind_speed_10m"],
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_probability_max"],
        }

        try:
            r = requests.get(_FORECAST, params=params, timeout=8)
            r.raise_for_status()
            j = r.json()
        except Exception:
            return f"Weather data for {nice} is unavailable right now."

        cur, daily = j.get("current", {}), j.get("daily", {})

        parts = [
            f"Weather for {nice}: {_wcode_to_text(cur.get('weather_code', -1))}.",
            f"Currently {cur.get('temperature_2m')} degrees Celsius" if cur.get("temperature_2m") is not None else None,
            f"Winds at {cur.get('wind_speed_10m')} meters per second" if cur.get("wind_speed_10m") is not None else None,
        ]

        hi, lo = (daily.get("temperature_2m_max") or [None])[0], (daily.get("temperature_2m_min") or [None])[0]
        if hi is not None and lo is not None:
                parts.append(f"Today’s low will be {lo} and the high will be {hi} degrees Celsius")

        pmax = (daily.get("precipitation_probability_max") or [None])[0]
        if pmax is not None:
                parts.append(f"Maximum chance of rain {pmax} percent")

        speak = " ".join(p for p in parts if p)
        return speak or f"Weather data for {nice} is unavailable right now."
