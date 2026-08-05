"""주변 숙소 검색 로직 (카카오 로컬 API + 동선 기반 밤 단위 추천 + 이미지 크롤링).

원래 views/page3_lodging.py(Streamlit)에서 검증된 로직을 프레임워크 독립적인
형태로 옮긴 것이다. st.cache_data는 flask_caching의 cache.memoize로,
st.session_state/st.spinner/st.error 등 UI 호출은 전부 제거했다 — 실패 시
requests.RequestException을 그대로 올려서 호출부(Flask 라우트)가 처리한다.
"""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

from flask_app.services.cache import cache

load_dotenv()

KAKAO_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
KAKAO_BASE_URL = "https://dapi.kakao.com"
LODGING_CATEGORY_CODE = "AD5"  # 카카오 카테고리 그룹 코드: 숙박
REQUEST_TIMEOUT = 5  # seconds
RADIUS_OPTIONS = [500, 1000, 2000, 3000]

LODGING_TYPE_KEYWORDS = {
    "호텔": ["호텔"],
    "모텔": ["모텔"],
    "펜션/풀빌라": ["펜션", "풀빌라"],
    "게스트하우스": ["게스트하우스", "게스트 하우스", "호스텔"],
    "리조트": ["리조트"],
    "캠핑/글램핑": ["캠핑", "글램핑"],
}
TYPE_BADGE_COLORS = {
    "호텔": "#4A90D9",
    "모텔": "#9B7BD9",
    "펜션/풀빌라": "#3FB88A",
    "게스트하우스": "#E85D8A",
    "리조트": "#4AA3D9",
    "캠핑/글램핑": "#E8A23F",
    "기타": "#8A94A6",
}
TYPE_ICONS = {
    "호텔": "🏨",
    "모텔": "🛏️",
    "펜션/풀빌라": "🏡",
    "게스트하우스": "🎒",
    "리조트": "🌴",
    "캠핑/글램핑": "⛺",
    "기타": "📍",
}

META_TAG_PATTERN = re.compile(r"<meta[^>]+>", re.IGNORECASE)
OG_IMAGE_PROPERTY_PATTERN = re.compile(r'property=["\']og:image["\']', re.IGNORECASE)
CONTENT_ATTR_PATTERN = re.compile(r'content=["\']([^"\']+)["\']', re.IGNORECASE)
IMAGE_FETCH_TIMEOUT = 4  # seconds
IMAGE_FETCH_WORKERS = 8


def classify_lodging_type(place: dict) -> str:
    """place_name/category_name 텍스트로 숙소 유형을 대략 분류한다."""
    text = f"{place.get('category_name', '')} {place.get('place_name', '')}"
    for label, keywords in LODGING_TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return label
    return "기타"


def build_night_options(plan: dict) -> list[dict]:
    """P2 일정에서 밤(하루의 마지막 일정 → 다음날 첫 일정) 단위 후보를 만든다."""
    itinerary = plan.get("itinerary") or []
    if not plan.get("start_date") or not plan.get("end_date"):
        return []

    by_date: dict[str, list[dict]] = {}
    for item in itinerary:
        if item.get("latitude") is not None and item.get("longitude") is not None:
            by_date.setdefault(item["date"], []).append(item)
    for items in by_date.values():
        items.sort(key=lambda it: it["time"])

    start = date.fromisoformat(plan["start_date"])
    end = date.fromisoformat(plan["end_date"])

    nights = []
    current = start
    while current < end:
        next_day = current + timedelta(days=1)
        today_items = by_date.get(current.isoformat())
        tomorrow_items = by_date.get(next_day.isoformat())
        nights.append(
            {
                "night_index": len(nights),
                "date_from": current,
                "date_to": next_day,
                "last_item": today_items[-1] if today_items else None,
                "first_item": tomorrow_items[0] if tomorrow_items else None,
            }
        )
        current = next_day
    return nights


def night_center(night: dict) -> dict | None:
    """밤의 검색 중심 좌표를 정한다. 이상적으로는 전날 마지막 일정과 다음날 첫
    일정의 중간 지점이며, 한쪽 좌표만 있으면 그 지점을, 둘 다 없으면 None을 준다."""
    last_item, first_item = night["last_item"], night["first_item"]
    if last_item and first_item:
        return {
            "x": (last_item["longitude"] + first_item["longitude"]) / 2,
            "y": (last_item["latitude"] + first_item["latitude"]) / 2,
            "label": f"{last_item['name']} ↔ {first_item['name']} 사이",
        }
    if last_item:
        return {"x": last_item["longitude"], "y": last_item["latitude"], "label": f"{last_item['name']} 근처"}
    if first_item:
        return {"x": first_item["longitude"], "y": first_item["latitude"], "label": f"{first_item['name']} 근처"}
    return None


def night_anchors(night: dict) -> list[dict]:
    """검색에 쓸 기준 좌표들을 모두 돌려준다.

    전날 마지막 일정 근처, 다음날 첫 일정 근처, 그리고 그 사이 중간 지점까지
    포함해서, "사이"뿐 아니라 두 일정 각각의 주변 숙소도 검색 대상에 들어오게 한다.
    한쪽 일정만 있으면 그 지점 하나만 돌려준다.
    """
    last_item, first_item = night["last_item"], night["first_item"]
    anchors = []
    if last_item:
        anchors.append(
            {"x": last_item["longitude"], "y": last_item["latitude"], "label": f"{last_item['name']} 근처"}
        )
    if first_item:
        anchors.append(
            {"x": first_item["longitude"], "y": first_item["latitude"], "label": f"{first_item['name']} 근처"}
        )
    if last_item and first_item:
        anchors.append(
            {
                "x": (last_item["longitude"] + first_item["longitude"]) / 2,
                "y": (last_item["latitude"] + first_item["latitude"]) / 2,
                "label": f"{last_item['name']} ↔ {first_item['name']} 사이",
            }
        )
    return anchors


def night_option_label(night: dict) -> str:
    date_part = f"{night['date_from'].strftime('%m/%d')} 밤"
    last_name = night["last_item"]["name"] if night["last_item"] else "정보 없음"
    first_name = night["first_item"]["name"] if night["first_item"] else "정보 없음"
    return f"{date_part} · {last_name} → {first_name}"


def haversine_m(y1: float, x1: float, y2: float, x2: float) -> float:
    """두 좌표(위도/경도) 사이의 거리를 미터 단위로 계산한다."""
    earth_radius_m = 6371000
    p1, p2 = math.radians(y1), math.radians(y2)
    d_phi = math.radians(y2 - y1)
    d_lambda = math.radians(x2 - x1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(a))


@cache.memoize(timeout=600)
def geocode_location(query: str, api_key: str) -> dict | None:
    """주소/지명 문자열을 좌표로 변환한다.

    1) 주소 검색을 먼저 시도하고, 결과가 없으면
    2) 키워드 검색으로 대표 좌표를 찾는다 (건물명, 관광지명 등 대응).
    """
    headers = {"Authorization": f"KakaoAK {api_key}"}

    address_url = f"{KAKAO_BASE_URL}/v2/local/search/address.json"
    resp = requests.get(
        address_url, headers=headers, params={"query": query}, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    documents = resp.json().get("documents", [])
    if documents:
        doc = documents[0]
        return {
            "x": float(doc["x"]),
            "y": float(doc["y"]),
            "label": doc.get("address_name", query),
        }

    keyword_url = f"{KAKAO_BASE_URL}/v2/local/search/keyword.json"
    resp = requests.get(
        keyword_url, headers=headers, params={"query": query}, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    documents = resp.json().get("documents", [])
    if documents:
        doc = documents[0]
        return {
            "x": float(doc["x"]),
            "y": float(doc["y"]),
            "label": doc.get("place_name", query),
        }

    return None


@cache.memoize(timeout=600)
def search_nearby_lodging(
    x: float, y: float, radius_m: int, api_key: str, max_pages: int = 3
) -> list[dict]:
    """주어진 좌표 반경 내 숙박시설을 거리순으로 검색한다."""
    headers = {"Authorization": f"KakaoAK {api_key}"}
    url = f"{KAKAO_BASE_URL}/v2/local/search/category.json"

    results: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "category_group_code": LODGING_CATEGORY_CODE,
            "x": x,
            "y": y,
            "radius": radius_m,
            "sort": "distance",
            "page": page,
            "size": 15,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        results.extend(payload.get("documents", []))
        if payload.get("meta", {}).get("is_end", True):
            break

    # 카카오 API는 distance를 문자열로 내려주므로, 이후 정렬/합산 연산이
    # 깨지지 않도록 여기서 한 번에 int로 정규화한다.
    for place in results:
        raw_distance = place.get("distance")
        place["distance"] = int(raw_distance) if raw_distance not in (None, "") else None

    return results


def search_lodging_for_anchors(anchors: list[dict], radius_m: int, api_key: str = KAKAO_API_KEY) -> list[dict]:
    """여러 기준 좌표 각각에서 반경 검색을 하고, 중복 숙소는 가장 가까운 기준점
    까지의 거리로 합쳐서 하나의 결과 목록(거리순)으로 만든다."""
    merged: dict[str, dict] = {}
    for anchor in anchors:
        places = search_nearby_lodging(anchor["x"], anchor["y"], radius_m, api_key)
        for place in places:
            place_key = place.get("id") or place.get("place_url") or place.get("place_name")
            dist = haversine_m(anchor["y"], anchor["x"], float(place["y"]), float(place["x"]))
            best = merged.get(place_key)
            if best is None or dist < best["distance"]:
                updated = dict(place)
                updated["distance"] = int(dist)
                merged[place_key] = updated
    return sorted(merged.values(), key=lambda p: p["distance"])


@cache.memoize(timeout=86400)
def fetch_place_image(place_url: str) -> str | None:
    """숙소 상세 페이지(카카오맵)에서 미리보기용 대표 이미지(og:image)를 가져온다.

    <meta property="og:image" content="..."> 안에서 두 속성의 순서는 페이지마다
    다를 수 있어서, 태그 전체를 먼저 뽑은 뒤 그 안에서 각각 찾는다.
    """
    if not place_url:
        return None
    try:
        resp = requests.get(
            place_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TripRollBot/1.0)"},
            timeout=IMAGE_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None
    for tag in META_TAG_PATTERN.findall(resp.text):
        if OG_IMAGE_PROPERTY_PATTERN.search(tag):
            content_match = CONTENT_ATTR_PATTERN.search(tag)
            if content_match:
                return content_match.group(1)
    return None


def fetch_place_images(place_urls: list[str]) -> dict[str, str | None]:
    """여러 숙소 이미지를 병렬로 가져온다. 하나씩 순서대로 요청하면 카드 수만큼
    느려지므로 스레드풀로 동시에 가져온다."""
    unique_urls = [url for url in dict.fromkeys(place_urls) if url]
    images: dict[str, str | None] = {}
    if not unique_urls:
        return images
    with ThreadPoolExecutor(max_workers=IMAGE_FETCH_WORKERS) as executor:
        future_to_url = {executor.submit(fetch_place_image, url): url for url in unique_urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                images[url] = future.result()
            except Exception:
                images[url] = None
    return images
