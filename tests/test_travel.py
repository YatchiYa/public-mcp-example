"""travel-intel server, upstream HTTP mocked with httpx.MockTransport (no network in tests)."""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import MCPError

from servers.travel import api
from servers.travel.server import mcp

FAKE = {
    api.GEOCODE_URL: {"results": [{"name": "Paris", "country": "France", "admin1": "Île-de-France",
                                   "latitude": 48.85, "longitude": 2.35, "timezone": "Europe/Paris", "population": 2138551}]},
    api.FORECAST_URL: {"timezone": "Europe/Paris",
                       "current": {"time": "2026-09-02T20:45", "temperature_2m": 24.4, "apparent_temperature": 24.0,
                                   "relative_humidity_2m": 50, "wind_speed_10m": 7.0, "weather_code": 2},
                       "daily": {"time": ["2026-09-02", "2026-09-03"], "temperature_2m_max": [25.6, 26.1],
                                 "temperature_2m_min": [15.3, 18.3], "precipitation_probability_max": [0, 10],
                                 "weather_code": [3, 61]}},
    api.AIR_URL: {"current": {"time": "2026-09-02T20:00", "european_aqi": 35, "pm2_5": 8.1, "pm10": 12.0}},
    api.FX_URL: {"amount": 1.0, "base": "EUR", "date": "2026-09-02", "rates": {"JPY": 184.78}},
}


@pytest.fixture(autouse=True)
def mock_upstream(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == api.GEOCODE_URL and request.url.params.get("name") == "Nowhere":
            return httpx.Response(200, json={})
        if url == api.FX_URL and request.url.params.get("base") == "XXX":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=FAKE[url])

    monkeypatch.setattr(api, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_tools_listed():
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {"geocode_place", "get_weather", "get_air_quality", "convert_currency", "local_time"}


async def test_geocode_and_weather_flow():
    async with Client(mcp) as c:
        places = (await c.call_tool("geocode_place", {"query": "Paris"})).data
        assert places[0]["timezone"] == "Europe/Paris"
        w = (await c.call_tool("get_weather", {"latitude": places[0]["latitude"], "longitude": places[0]["longitude"], "days": 2})).data
        assert w["current"]["conditions"] == "partly cloudy"
        assert w["daily"][1]["conditions"] == "light rain"
        aq = (await c.call_tool("get_air_quality", {"latitude": 48.85, "longitude": 2.35})).data
        assert aq["rating"] == "fair"


async def test_currency_and_time():
    async with Client(mcp) as c:
        fx = (await c.call_tool("convert_currency", {"amount": 100, "from_currency": "eur", "to_currency": "jpy"})).data
        assert fx["converted"] == 18478.0
        same = (await c.call_tool("convert_currency", {"amount": 5, "from_currency": "EUR", "to_currency": "EUR"})).data
        assert same["rate"] == 1.0
        t = (await c.call_tool("local_time", {"timezone": "Asia/Tokyo"})).data
        assert t["utc_offset"] == "+0900"


async def test_errors_are_actionable():
    async with Client(mcp) as c:
        with pytest.raises(MCPError, match="No place found"):
            await c.call_tool("geocode_place", {"query": "Nowhere"})
        with pytest.raises(MCPError, match="Upstream API error 404"):
            await c.call_tool("convert_currency", {"amount": 1, "from_currency": "XXX", "to_currency": "JPY"})
        with pytest.raises(MCPError, match="Unknown timezone"):
            await c.call_tool("local_time", {"timezone": "Mars/Olympus"})
        with pytest.raises(MCPError):  # pydantic validation: latitude out of range
            await c.call_tool("get_weather", {"latitude": 999, "longitude": 0})
