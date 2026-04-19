from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from mira.obs.logging import log_event
from mira.runtime.registry import tool

_WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


# Module-level caches. A city's lat/lon/tz is stable indefinitely — cache
# it forever (and let process restart be the invalidation). Current weather
# is refreshed every 10 min; multi-day forecasts every hour. Keyed by
# normalized location string so "Austin" and "austin " dedupe.
_GEO_CACHE: dict[str, dict[str, Any]] = {}
_WX_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

_CURRENT_TTL_S = 600.0
_FORECAST_TTL_S = 3600.0

# Shared HTTP client — one TCP/TLS handshake amortized across every weather
# call in the process. open-meteo has no auth, high availability, and is
# friendly to keep-alive. Lazy-init so importing this module is still cheap.
_CLIENT: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(timeout=8.0)
    return _CLIENT


def _norm_loc(s: str) -> str:
    return " ".join(s.strip().lower().split())


async def _geocode(location: str) -> dict[str, Any] | None:
    key = _norm_loc(location)
    cached = _GEO_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        r = await _client().get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log_event("weather.geocode_error", location=location, error=repr(exc))
        return None
    results = data.get("results") or []
    if not results:
        return None
    g = results[0]
    place = {
        "name": g.get("name"),
        "admin1": g.get("admin1"),
        "country": g.get("country"),
        "lat": g.get("latitude"),
        "lon": g.get("longitude"),
        "tz": g.get("timezone") or "auto",
    }
    _GEO_CACHE[key] = place
    return place


def _place_label(place: dict[str, Any]) -> str:
    return f"{place['name']}, {place.get('admin1') or place.get('country') or ''}".strip(", ")


# ---------- summarizers ----------


def _summarize_current(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"weather error: {data.get('error') or 'unknown'}"
    loc = data.get("location") or "there"
    temp = data.get("temperature_f")
    cond = data.get("conditions") or ""
    wind = data.get("wind_mph")
    humidity = data.get("humidity_pct")
    parts = [f"{loc}: {temp}°F" if temp is not None else loc]
    if cond:
        parts.append(cond)
    extras = []
    if wind is not None:
        extras.append(f"wind {wind} mph")
    if humidity is not None:
        extras.append(f"humidity {humidity}%")
    if extras:
        parts.append(", ".join(extras))
    return ", ".join(parts)


def _summarize_forecast(data: Any, *, max_items: int = 7) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"weather error: {data.get('error') or 'unknown'}"
    loc = data.get("location") or "there"
    days = data.get("days") or []
    if not days:
        return f"{loc}: no forecast data"
    lines = [f"{loc} forecast:"]
    for d in days[:max_items]:
        date = d.get("date") or ""
        hi = d.get("high_f")
        lo = d.get("low_f")
        cond = d.get("conditions") or ""
        lines.append(f"{date}: {hi}/{lo}°F {cond}".strip())
    return " | ".join(lines)


# ---------- tools ----------


class CurrentArgs(BaseModel):
    location: str = Field(..., description="City name, e.g. 'Austin' or 'Paris, FR'.")


@tool(
    "weather.current",
    description=(
        "Current weather for a location: temperature (°F), conditions, wind. "
        "Uses open-meteo — no API key, no auth. Cached for 10 minutes."
    ),
    params=CurrentArgs,
    tags=("weather",),
    summarizer=_summarize_current,
)
async def weather_current(args: CurrentArgs) -> dict[str, Any]:
    loc_key = _norm_loc(args.location)
    now = time.time()
    hit = _WX_CACHE.get(("current", loc_key))
    if hit and now - hit[0] < _CURRENT_TTL_S:
        return hit[1]

    place = await _geocode(args.location)
    if not place:
        return {"ok": False, "error": f"couldn't find location: {args.location}"}
    try:
        r = await _client().get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["lat"], "longitude": place["lon"],
                "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": place["tz"],
            },
        )
        r.raise_for_status()
        cur = r.json().get("current", {})
    except Exception as exc:
        log_event("weather.current_error", location=args.location, error=repr(exc))
        return {"ok": False, "error": f"weather service unreachable: {exc}"}
    code = int(cur.get("weather_code", 0))
    out = {
        "ok": True,
        "location": _place_label(place),
        "temperature_f": cur.get("temperature_2m"),
        "conditions": _WMO.get(code, f"code {code}"),
        "wind_mph": cur.get("wind_speed_10m"),
        "humidity_pct": cur.get("relative_humidity_2m"),
    }
    _WX_CACHE[("current", loc_key)] = (now, out)
    return out


class ForecastArgs(BaseModel):
    location: str = Field(..., description="City name.")
    days: int = Field(default=3, ge=1, le=7)


@tool(
    "weather.forecast",
    description="Multi-day forecast: high/low temp (°F) and conditions. Cached for 1 hour.",
    params=ForecastArgs,
    tags=("weather",),
    summarizer=_summarize_forecast,
)
async def weather_forecast(args: ForecastArgs) -> dict[str, Any]:
    loc_key = _norm_loc(args.location)
    cache_key = (f"forecast:{args.days}", loc_key)
    now = time.time()
    hit = _WX_CACHE.get(cache_key)
    if hit and now - hit[0] < _FORECAST_TTL_S:
        return hit[1]

    place = await _geocode(args.location)
    if not place:
        return {"ok": False, "error": f"couldn't find location: {args.location}"}
    try:
        r = await _client().get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["lat"], "longitude": place["lon"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": place["tz"],
                "forecast_days": args.days,
            },
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
    except Exception as exc:
        log_event("weather.forecast_error", location=args.location, error=repr(exc))
        return {"ok": False, "error": f"weather service unreachable: {exc}"}
    days = []
    for i, date in enumerate(daily.get("time", [])):
        code = int(daily["weather_code"][i])
        days.append({
            "date": date,
            "high_f": daily["temperature_2m_max"][i],
            "low_f": daily["temperature_2m_min"][i],
            "conditions": _WMO.get(code, f"code {code}"),
        })
    out = {
        "ok": True,
        "location": _place_label(place),
        "days": days,
    }
    _WX_CACHE[cache_key] = (now, out)
    return out
