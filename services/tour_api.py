"""Korea Tourism Organization TourAPI adapter for the P2 planner."""

from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://apis.data.go.kr/B551011/KorService2"
SIGUNGU_CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "sigungu.csv"
SIDO_CODES = {
    "서울특별시": "11", "부산광역시": "26", "대구광역시": "27", "인천광역시": "28",
    "광주광역시": "29", "대전광역시": "30", "울산광역시": "31", "세종특별자치시": "36",
    "경기도": "41", "충청북도": "43", "충청남도": "44", "전라북도": "46",
    "경상북도": "47", "경상남도": "48", "강원특별자치도": "51", "전북특별자치도": "52",
    "제주특별자치도": "50",
}
CONTENT_TYPES = {"12": "관광지", "14": "문화시설", "15": "축제", "39": "음식점"}


class TourApiError(RuntimeError):
    pass


def _region_key(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _province_aliases(sido: str) -> set[str]:
    return {
        sido,
        sido.removesuffix("특별자치도").removesuffix("도"),
        sido.replace("충청", "충").replace("전라", "전").replace("경상", "경").removesuffix("도"),
    }


@lru_cache(maxsize=1)
def _region_normalisations() -> dict[str, dict[str, Any] | None]:
    normalisations: dict[str, dict[str, Any] | None] = {}
    with SIGUNGU_CSV_PATH.open(encoding="utf-8-sig", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            sido, sigungu = str(row.get("sido", "")).strip(), str(row.get("sigungu", "")).strip()
            if not sido or not sigungu:
                continue
            canonical = {"sido": sido, "sigungu": sigungu, "lat": row.get("lat"), "lng": row.get("lng")}
            aliases = {sigungu}
            for province in _province_aliases(sido):
                aliases.update({f"{province}{sigungu}", f"{province} {sigungu}"})
            for alias in aliases:
                key = _region_key(alias)
                if key not in normalisations:
                    normalisations[key] = canonical
                elif normalisations[key] != canonical:
                    normalisations[key] = None
    return normalisations


def normalise_region(region: dict[str, Any]) -> dict[str, Any]:
    normalisations = _region_normalisations()
    for candidate in (region.get("name", ""), f"{region.get('sido', '')} {region.get('sigungu', '')}"):
        canonical = normalisations.get(_region_key(candidate))
        if canonical:
            return {**region, **canonical, "name": f"{canonical['sido']} {canonical['sigungu']}"}
    return region


def _request(endpoint: str, key: str, **params: Any) -> dict[str, Any]:
    if not key:
        raise TourApiError(".env에 TOUR_API_KEY를 설정해 주세요.")
    query = {"serviceKey": unquote(key.strip()), "MobileOS": "ETC", "MobileApp": "TripRoll", "_type": "json", **params}
    try:
        with urlopen(Request(f"{BASE_URL}/{endpoint}?{urlencode(query)}", headers={"User-Agent": "TripRoll/1.0"}), timeout=20) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as error:
        raise TourApiError(f"TourAPI 요청 실패: {error}") from error
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise TourApiError(f"TourAPI가 JSON이 아닌 응답을 반환했습니다: {payload[:160]}") from error
    if not isinstance(data, dict):
        raise TourApiError("TourAPI 응답 형식이 올바르지 않습니다.")
    response_data = data.get("response", {})
    header = response_data.get("header", {}) if isinstance(response_data, dict) else {}
    if not isinstance(header, dict):
        raise TourApiError("TourAPI 응답의 header 필드 형식이 올바르지 않습니다.")
    if str(header.get("resultCode", "0000")) != "0000":
        raise TourApiError(str(header.get("resultMsg", "TourAPI 오류")))
    return data


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    response_data = data.get("response", {})
    body = response_data.get("body", {}) if isinstance(response_data, dict) else {}
    items = body.get("items", {}) if isinstance(body, dict) else {}
    value = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _resolve_region(region: dict[str, Any], key: str) -> tuple[str, str]:
    region = normalise_region(region)
    sido, sigungu = str(region.get("sido", "")).strip(), str(region.get("sigungu", "")).strip()
    area = SIDO_CODES.get(sido)
    if not area:
        raise TourApiError(f"TourAPI 지역 코드가 없는 시도입니다: {sido}")
    if not sigungu or sigungu == sido:
        return area, ""
    records = _items(_request("ldongCode2", key, lDongRegnCd=area, lDongListYn="Y", numOfRows=1000, pageNo=1))
    target = sigungu.replace(" ", "")
    for record in records:
        name = str(record.get("lDongSignguNm", "")).replace(" ", "")
        if name == target or target in name or name in target:
            code = str(record.get("lDongSignguCd", ""))
            return area, code[len(area):] if code.startswith(area) else code
    raise TourApiError(f"TourAPI에서 {sido} {sigungu}의 시군구 코드를 찾지 못했습니다.")


def _overview(key: str, content_id: str) -> str:
    details = _items(_request("detailCommon2", key, contentId=content_id, numOfRows=10, pageNo=1))
    return _clean(str(details[0].get("overview", ""))) if details else ""


def _nearby_candidates(region: dict[str, Any], key: str, rows_per_type: int) -> list[dict[str, Any]]:
    try:
        latitude, longitude = float(region["lat"]), float(region["lng"])
    except (KeyError, TypeError, ValueError):
        return []
    raw: list[dict[str, Any]] = []
    for content_type in ("12", "39", "14"):
        raw.extend(_items(_request("locationBasedList2", key, mapX=longitude, mapY=latitude, radius=20000, contentTypeId=content_type, arrange="E", numOfRows=rows_per_type, pageNo=1)))
    return raw


def fetch_place_candidates(region: dict[str, Any], start: date, end: date, key: str, rows_per_type: int = 6) -> list[dict[str, Any]]:
    region = normalise_region(region)
    area, sigungu = _resolve_region(region, key)
    common = {"lDongRegnCd": area, "numOfRows": rows_per_type, "pageNo": 1}
    if sigungu:
        common["lDongSignguCd"] = sigungu
    raw: list[dict[str, Any]] = []
    for content_type in ("12", "39", "14"):
        raw.extend(_items(_request("areaBasedList2", key, contentTypeId=content_type, arrange="Q", **common)))
    raw.extend(_items(_request("searchFestival2", key, eventStartDate=start.strftime("%Y%m%d"), eventEndDate=end.strftime("%Y%m%d"), arrange="A", **common)))
    if not raw:
        raw = _nearby_candidates(region, key, rows_per_type)
    unique = {str(item.get("contentid")): item for item in raw if item.get("contentid") and item.get("title")}
    descriptions: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_overview, key, content_id): content_id for content_id in unique}
        for future in as_completed(futures):
            descriptions[futures[future]] = future.result() if not future.exception() else ""
    return [{
        "content_id": content_id, "name": str(item["title"]).strip(),
        "category": CONTENT_TYPES.get(str(item.get("contenttypeid")), "관광지"),
        "address": " ".join(filter(None, (str(item.get("addr1", "")).strip(), str(item.get("addr2", "")).strip()))),
        "latitude": _number(item.get("mapy")), "longitude": _number(item.get("mapx")),
        "overview": descriptions.get(content_id, ""), "event_start": str(item.get("eventstartdate", "")),
        "event_end": str(item.get("eventenddate", "")), "source": "한국관광공사 TourAPI",
    } for content_id, item in unique.items()]
