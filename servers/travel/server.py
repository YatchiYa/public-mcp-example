"""Travel Intelligence MCP: geocoding, weather, air quality, currency, local time.

No API keys needed (Open-Meteo + Frankfurter + stdlib zoneinfo), so it runs anywhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastmcp.exceptions import ToolError
from pydantic import Field

from forge import create_server, run
from servers.travel import api

mcp = create_server(
    "travel-intel",
    instructions=(
        "Travel assistant toolkit. Typical flow: geocode_place -> get_weather / get_air_quality "
        "with the returned coordinates; convert_currency for budgets; local_time using the "
        "timezone returned by geocode_place. All data is live from public sources."
    ),
)

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
    81: "heavy showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm w/ hail",
}
AQI_BANDS = [(20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very poor")]


def _wmo(code: int | None) -> str:
    return WMO.get(code, f"code {code}")


async def _call(coro):
    """Translate upstream failures into ToolError so the LLM gets an actionable message."""
    try:
        return await coro
    except httpx.HTTPStatusError as e:
        raise ToolError(f"Upstream API error {e.response.status_code} from {e.request.url.host}")
    except httpx.HTTPError as e:
        raise ToolError(f"Network error reaching {e.request.url.host}: {type(e).__name__}")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def geocode_place(
    query: Annotated[str, Field(description="City or place name, e.g. 'Lyon' or 'Kyoto'", min_length=2)],
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[dict]:
    """Find places by name. Returns lat/lon, country, timezone, population. Call this first."""
    results = await _call(api.geocode(query, limit))
    if not results:
        raise ToolError(f"No place found for {query!r}. Try a larger city or add the country.")
    return [
        {
            "name": r["name"],
            "country": r.get("country"),
            "admin1": r.get("admin1"),
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "timezone": r.get("timezone"),
            "population": r.get("population"),
        }
        for r in results
    ]


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def get_weather(
    latitude: Annotated[float, Field(ge=-90, le=90)],
    longitude: Annotated[float, Field(ge=-180, le=180)],
    days: Annotated[int, Field(ge=1, le=16, description="Forecast horizon in days")] = 5,
) -> dict:
    """Current conditions + daily forecast (°C, km/h, rain probability) for coordinates."""
    d = await _call(api.forecast(latitude, longitude, days))
    cur = d["current"]
    daily = d["daily"]
    return {
        "timezone": d["timezone"],
        "current": {
            "time": cur["time"],
            "temperature_c": cur["temperature_2m"],
            "feels_like_c": cur["apparent_temperature"],
            "humidity_pct": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10m"],
            "conditions": _wmo(cur["weather_code"]),
        },
        "daily": [
            {
                "date": daily["time"][i],
                "min_c": daily["temperature_2m_min"][i],
                "max_c": daily["temperature_2m_max"][i],
                "rain_probability_pct": daily["precipitation_probability_max"][i],
                "conditions": _wmo(daily["weather_code"][i]),
            }
            for i in range(len(daily["time"]))
        ],
    }


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def get_air_quality(
    latitude: Annotated[float, Field(ge=-90, le=90)],
    longitude: Annotated[float, Field(ge=-180, le=180)],
) -> dict:
    """European AQI and particulate levels (PM2.5 / PM10) right now for coordinates."""
    d = await _call(api.air_quality(latitude, longitude))
    cur = d["current"]
    aqi = cur.get("european_aqi")
    band = next((label for limit, label in AQI_BANDS if aqi is not None and aqi <= limit), "extremely poor")
    return {"time": cur["time"], "european_aqi": aqi, "rating": band, "pm2_5": cur.get("pm2_5"), "pm10": cur.get("pm10")}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def convert_currency(
    amount: Annotated[float, Field(gt=0)],
    from_currency: Annotated[str, Field(description="ISO 4217 code, e.g. EUR", min_length=3, max_length=3)],
    to_currency: Annotated[str, Field(description="ISO 4217 code, e.g. JPY", min_length=3, max_length=3)],
) -> dict:
    """Convert an amount using today's ECB reference rates."""
    src, dst = from_currency.upper(), to_currency.upper()
    if src == dst:
        return {"amount": amount, "from": src, "to": dst, "rate": 1.0, "converted": amount}
    d = await _call(api.fx_rates(src, [dst]))
    if dst not in d.get("rates", {}):
        raise ToolError(f"Unsupported currency pair {src}->{dst}")
    rate = d["rates"][dst]
    return {"amount": amount, "from": src, "to": dst, "rate": rate, "converted": round(amount * rate, 2), "date": d["date"]}


@mcp.tool(annotations={"readOnlyHint": True})
def local_time(timezone: Annotated[str, Field(description="IANA name, e.g. Asia/Tokyo")]) -> dict:
    """Current local date/time and UTC offset for an IANA timezone (from geocode_place)."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError(f"Unknown timezone {timezone!r}; use the value returned by geocode_place")
    return {"timezone": timezone, "local_time": now.isoformat(timespec="minutes"), "utc_offset": now.strftime("%z")}


@mcp.prompt
def plan_trip(destination: str, days: int = 3) -> str:
    """Prompt template: build a short trip brief for a destination."""
    return (
        f"Prepare a {days}-day travel brief for {destination}. Use geocode_place, then get_weather, "
        f"get_air_quality and local_time. Convert a daily budget of 100 EUR to the local currency "
        f"with convert_currency. Summarise packing advice from the forecast."
    )


if __name__ == "__main__":
    run(mcp)
