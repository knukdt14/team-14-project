"""Small TMAP transit adapter used when the traveller selects air travel."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"


class TmapTransitError(RuntimeError):
    """Raised when TMAP transit information cannot be read."""


def fetch_air_travel_info(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    travel_date: date,
    app_key: str,
) -> dict[str, Any]:
    """Return the first public-transit route that contains an airplane segment.

    TMAP's transit endpoint supplies transit-route facts, not airline seat
    availability. The result is used as an arrival/departure constraint.
    """
    if not app_key:
        raise TmapTransitError("TMAP_APP_KEY가 설정되지 않았습니다.")

    payload = {
        "startX": str(start_longitude), "startY": str(start_latitude),
        "endX": str(end_longitude), "endY": str(end_latitude),
        "count": 3, "lang": 0, "format": "json",
        "searchDttm": f"{travel_date:%Y%m%d}0800",
    }
    request = Request(
        TRANSIT_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"appKey": app_key.strip(), "accept": "application/json", "content-type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise TmapTransitError(f"TMAP 대중교통 경로 조회에 실패했습니다: {error}") from error

    metadata = data.get("metaData", {}) if isinstance(data, dict) else {}
    plan = metadata.get("plan", {}) if isinstance(metadata, dict) else {}
    routes = plan.get("itineraries", []) if isinstance(plan, dict) else []
    for route in routes if isinstance(routes, list) else []:
        if not isinstance(route, dict):
            continue
        flights = []
        for leg in route.get("legs", []):
            if not isinstance(leg, dict) or leg.get("mode") != "AIRPLANE":
                continue
            flights.append({
                "route": leg.get("route", "항공편"),
                "departure": leg.get("start", {}).get("name", "출발 공항"),
                "arrival": leg.get("end", {}).get("name", "도착 공항"),
                "minutes": leg.get("sectionTime"),
            })
        if flights:
            fare = route.get("fare", {})
            regular_fare = fare.get("regular", {}) if isinstance(fare, dict) else {}
            return {"available": True, "total_minutes": route.get("totalTime"), "estimated_fare": regular_fare.get("totalFare"), "flights": flights}
    return {"available": False, "message": "선택한 출발지와 목적지 사이의 항공 이동 경로를 찾지 못했습니다."}
