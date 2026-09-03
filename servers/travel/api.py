"""Thin async clients for free, key-less public APIs. Kept separate so tests can swap the transport."""

from __future__ import annotations

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FX_URL = "https://api.frankfurter.dev/v1/latest"

# ponytail: one shared client, no retries. Add httpx transport retries / a cache if these
# public APIs start rate-limiting you.
client = httpx.AsyncClient(timeout=10, headers={"User-Agent": "mcp-forge-travel/0.1"})


async def _get(url: str, **params) -> dict:
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()


async def geocode(name: str, count: int = 5) -> list[dict]:
    data = await _get(GEOCODE_URL, name=name, count=count, language="en", format="json")
    return data.get("results", [])


async def forecast(lat: float, lon: float, days: int) -> dict:
    return await _get(
        FORECAST_URL,
        latitude=lat,
        longitude=lon,
        forecast_days=days,
        timezone="auto",
        current="temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    )


async def air_quality(lat: float, lon: float) -> dict:
    return await _get(AIR_URL, latitude=lat, longitude=lon, current="european_aqi,pm2_5,pm10")


async def fx_rates(base: str, symbols: list[str] | None) -> dict:
    params = {"base": base}
    if symbols:
        params["symbols"] = ",".join(symbols)
    return await _get(FX_URL, **params)
