"""
P3. 주변 숙소  (담당: C 축 — 백엔드 / D 축 — 화면)

카카오 로컬 API로 숙소를 조회한다. P2 일정이 있으면 하루의 마지막 일정과
다음날 첫 일정의 중간 지점을 기준으로 숙박일별 숙소를 추천한다.

필요한 환경변수:
    KAKAO_REST_API_KEY : 카카오 디벨로퍼스에서 발급받은 REST API 키
"""

import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))
from views._common import page_header, require_region  # noqa: E402

load_dotenv()

KAKAO_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
KAKAO_BASE_URL = "https://dapi.kakao.com"
LODGING_CATEGORY_CODE = "AD5"  # 카카오 카테고리 그룹 코드: 숙박
REQUEST_TIMEOUT = 5  # seconds
RADIUS_OPTIONS = [500, 1000, 2000, 3000]
MAX_RECENT_SEARCHES = 5
CENTER_MARKER_COLOR = "#E9B949"  # 검색한 위치
LODGING_MARKER_COLOR = "#4FC3D9"  # 숙박시설

LODGING_TYPE_KEYWORDS = {
    "호텔": ["호텔"],
    "모텔": ["모텔"],
    "펜션/풀빌라": ["펜션", "풀빌라"],
    "게스트하우스": ["게스트하우스", "게스트 하우스", "호스텔"],
    "리조트": ["리조트"],
    "캠핑/글램핑": ["캠핑", "글램핑"],
}
TYPE_BADGE_COLORS = {
    "호텔": "#4FC3D9",
    "모텔": "#A97BE8",
    "펜션/풀빌라": "#5FD9A0",
    "게스트하우스": "#E8618C",
    "리조트": "#4FA1FF",
    "캠핑/글램핑": "#E9B949",
    "기타": "#7A8199",
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

# 이 페이지 전용 스타일 — 앱 전체 다크 테마(app.py) 위에 얹는 카드 UI
PAGE_CSS = """
<style>
.stay-card {
    border: 1px solid #232B45;
    border-radius: 16px;
    overflow: hidden;
    margin-bottom: 16px;
    background: #101728;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
    height: 100%;
}
.stay-card:hover {
    box-shadow: 0 10px 26px rgba(233,185,73,0.12);
    transform: translateY(-2px);
}
.stay-card-img { width: 100%; height: 150px; object-fit: cover; display: block; }
.stay-card-noimg {
    width: 100%; height: 150px; display: flex; align-items: center; justify-content: center;
    font-size: 2.6rem;
}
.stay-card-body { padding: 12px 16px 6px 16px; }
.stay-name-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.stay-name-link {
    font-size: 1.02rem; font-weight: 700; color: #EDEBE4; text-decoration: none;
}
.stay-name-link:hover { color: #E9B949; text-decoration: underline; }
.stay-address {
    color: #7A8199; font-size: 0.82rem; margin: 4px 0 0 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.stay-badge {
    display: inline-block; color: #0B0F1C; font-size: 0.72rem; font-weight: 700;
    padding: 2px 10px; border-radius: 999px; vertical-align: middle;
}
.stay-selected { color: #E9B949; font-size: 0.8rem; font-weight: 700; }
.type-count-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.type-count-badge {
    display: inline-block; color: #0B0F1C; font-size: 0.78rem; font-weight: 700;
    padding: 4px 12px; border-radius: 999px;
}
</style>
"""


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


def haversine_m(y1: float, x1: float, y2: float, x2: float) -> float:
    """두 좌표(위도/경도) 사이의 거리를 미터 단위로 계산한다."""
    earth_radius_m = 6371000
    p1, p2 = math.radians(y1), math.radians(y2)
    d_phi = math.radians(y2 - y1)
    d_lambda = math.radians(x2 - x1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(a))


def night_option_label(night: dict) -> str:
    date_part = f"{night['date_from'].strftime('%m/%d')} 밤"
    last_name = night["last_item"]["name"] if night["last_item"] else "정보 없음"
    first_name = night["first_item"]["name"] if night["first_item"] else "정보 없음"
    return f"{date_part} · {last_name} → {first_name}"


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
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


META_TAG_PATTERN = re.compile(r"<meta[^>]+>", re.IGNORECASE)
OG_IMAGE_PROPERTY_PATTERN = re.compile(r'property=["\']og:image["\']', re.IGNORECASE)
CONTENT_ATTR_PATTERN = re.compile(r'content=["\']([^"\']+)["\']', re.IGNORECASE)
IMAGE_FETCH_TIMEOUT = 4  # seconds
IMAGE_FETCH_WORKERS = 8


@st.cache_data(ttl=86400, show_spinner=False)
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


def add_recent_search(query: str) -> None:
    recents: list[str] = st.session_state.setdefault("recent_searches", [])
    if query in recents:
        recents.remove(query)
    recents.insert(0, query)
    del recents[MAX_RECENT_SEARCHES:]


def toggle_favorite(place: dict) -> None:
    fav_key = place.get("id") or place.get("place_url") or place["place_name"]
    favorites: dict = st.session_state.setdefault("favorites", {})
    if fav_key in favorites:
        del favorites[fav_key]
    else:
        favorites[fav_key] = place


def select_lodging(night_key: str, night_label: str, place: dict, lodging_type: str, address: str) -> None:
    """이 숙소를 해당 숙박일의 숙소로 확정하고 trip_context에 기록한다."""
    lodgings: dict = st.session_state.trip_context.setdefault("lodging", {})
    lodgings[night_key] = {
        "night_label": night_label,
        "name": place.get("place_name"),
        "type": lodging_type,
        "address": address,
        "distance_m": place.get("distance"),
        "phone": place.get("phone"),
        "place_url": place.get("place_url"),
        "latitude": float(place["y"]),
        "longitude": float(place["x"]),
    }


def run_search_for_anchors(
    anchors: list[dict], radius_m: int, night_key: str, night_label: str, display_center: dict
) -> None:
    """여러 기준 좌표 각각에서 반경 검색을 하고, 중복 숙소는 가장 가까운 기준점
    까지의 거리로 합쳐서 하나의 결과 목록으로 만든다."""
    merged: dict[str, dict] = {}
    with st.spinner("주변 숙소를 검색하는 중..."):
        for anchor in anchors:
            try:
                places = search_nearby_lodging(anchor["x"], anchor["y"], radius_m, KAKAO_API_KEY)
            except requests.RequestException as exc:
                st.error(f"카카오 API 호출 중 오류가 발생했습니다: {exc}")
                return
            for place in places:
                place_key = place.get("id") or place.get("place_url") or place.get("place_name")
                dist = haversine_m(anchor["y"], anchor["x"], float(place["y"]), float(place["x"]))
                best = merged.get(place_key)
                if best is None or dist < best["distance"]:
                    updated = dict(place)
                    updated["distance"] = int(dist)
                    merged[place_key] = updated

    st.session_state["nearby_stays_result"] = {
        "center": display_center,
        "anchors": anchors,
        "radius_m": radius_m,
        "places": sorted(merged.values(), key=lambda p: p["distance"]),
        "night_key": night_key,
        "night_label": night_label,
    }
    # 새 검색 결과의 숙소 유형 구성이 이전과 다를 수 있으므로, 필터 위젯의
    # 이전 선택 상태를 초기화해서 다음 렌더링에서 새 옵션 전체로 다시 시작한다.
    st.session_state.pop("type_filter", None)


def run_search_for_center(center: dict, radius_m: int, night_key: str, night_label: str) -> None:
    run_search_for_anchors([center], radius_m, night_key, night_label, center)


def run_search(query: str, radius_m: int) -> None:
    query = query.strip()
    if not query:
        st.warning("위치를 입력해주세요.")
        return

    with st.spinner("위치를 찾는 중..."):
        try:
            center = geocode_location(query, KAKAO_API_KEY)
        except requests.RequestException as exc:
            st.error(f"카카오 API 호출 중 오류가 발생했습니다: {exc}")
            return

    if center is None:
        st.warning("입력하신 위치를 찾을 수 없습니다. 다른 표현으로 다시 시도해보세요.")
        st.session_state.pop("nearby_stays_result", None)
        return

    run_search_for_center(center, radius_m, "manual", f"'{query}' 직접 검색")
    add_recent_search(query)


def render_result_card(
    place: dict, night_key: str, night_label: str, total_nights: int = 0, image_url: str | None = None
) -> None:
    name = place.get("place_name", "이름 없음")
    address = place.get("road_address_name") or place.get("address_name", "")
    distance_m = place.get("distance")
    phone = place.get("phone") or "전화번호 정보 없음"
    place_url = place.get("place_url")
    lodging_type = classify_lodging_type(place)
    badge_color = TYPE_BADGE_COLORS.get(lodging_type, TYPE_BADGE_COLORS["기타"])
    fav_key = place.get("id") or place_url or name
    is_favorite = fav_key in st.session_state.get("favorites", {})
    is_selected = (
        st.session_state.trip_context.get("lodging", {}).get(night_key, {}).get("name") == name
    )

    icon = TYPE_ICONS.get(lodging_type, "📍")
    selected_badge = '<span class="stay-selected">✓ 선택됨</span>' if is_selected else ""

    if image_url:
        # 이미지 로드에 실패하면(만료된 링크 등) 자바스크립트로 숨기고 자리만 접는다.
        media_html = f'<img class="stay-card-img" src="{image_url}" alt="{name}" onerror="this.remove()"/>'
    else:
        media_html = f'<div class="stay-card-noimg" style="background:{badge_color};">{icon}</div>'

    # 리스트에서 숙소 이름을 누르면 카카오맵 상세 페이지로 바로 들어간다.
    name_html = (
        f'<a class="stay-name-link" href="{place_url}" target="_blank" rel="noopener">{name}</a>'
        if place_url
        else f'<span class="stay-name-link">{name}</span>'
    )

    # 마크다운은 빈 줄을 만나면 이어지던 raw HTML 블록을 끊고 뒷부분을 코드블록으로
    # 취급해버리므로, 조건부 뱃지는 반드시 앞 태그와 같은 줄에 붙여야 한다.
    st.markdown(
        f"""
        <div class="stay-card">
            {media_html}
            <div class="stay-card-body">
                <div class="stay-name-row">
                    {name_html}
                    <span class="stay-badge" style="background:{badge_color};">{lodging_type}</span>{selected_badge}
                </div>
                <div class="stay-address" title="{address}">{address}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    info_col, phone_col = st.columns(2)
    info_col.caption(f"📍 {int(distance_m):,}m" if distance_m else "📍 거리 정보 없음")
    phone_col.caption(f"📞 {phone}")

    fav_label = "💛 찜 해제" if is_favorite else "🤍 찜하기"
    select_label = "✓ 선택됨" if is_selected else "이 숙소로 정하기"
    fav_col, select_col = st.columns(2)
    if fav_col.button(fav_label, key=f"fav_{fav_key}", width="stretch"):
        toggle_favorite(place)
        st.rerun()
    if select_col.button(
        select_label,
        key=f"select_{fav_key}",
        type="primary" if not is_selected else "secondary",
        width="stretch",
    ):
        select_lodging(night_key, night_label, place, lodging_type, address)
        st.success(f"'{name}'을(를) {night_label} 숙소로 저장했습니다.")
        if night_key.startswith("night_") and total_nights:
            next_idx = int(night_key.split("_", 1)[1]) + 1
            if next_idx < total_nights:
                # "숙박일 선택" selectbox는 이미 이번 스크립트 실행에서 만들어졌으므로
                # 지금 바로 session_state를 덮어쓸 수 없다. 다음 실행 맨 앞에서
                # selectbox가 생성되기 전에 반영되도록 override 플래그로 넘긴다.
                st.session_state["night_idx_override"] = next_idx
        st.rerun()


def render_summary(places: list[dict]) -> None:
    distances = [p["distance"] for p in places if p.get("distance")]
    types = [classify_lodging_type(p) for p in places]

    cols = st.columns(4)
    cols[0].metric("검색 결과", f"{len(places)}개")
    cols[1].metric("최단 거리", f"{int(min(distances)):,} m" if distances else "-")
    cols[2].metric(
        "평균 거리", f"{int(sum(distances) / len(distances)):,} m" if distances else "-"
    )
    cols[3].metric("숙소 유형 수", f"{len(set(types))}종")

    # 그래프 대신, 유형별 개수를 뱃지로 한 줄에 모아서 한눈에 보이게 한다.
    type_counts = pd.Series(types).value_counts()
    badges = "".join(
        f'<span class="type-count-badge" style="background:{TYPE_BADGE_COLORS.get(t, TYPE_BADGE_COLORS["기타"])};">'
        f'{TYPE_ICONS.get(t, "📍")} {t} {count}개</span>'
        for t, count in type_counts.items()
    )
    st.markdown(f'<div class="type-count-row">{badges}</div>', unsafe_allow_html=True)


def render_sidebar() -> None:
    with st.sidebar:
        recents: list[str] = st.session_state.get("recent_searches", [])
        if recents:
            st.divider()
            st.caption("🕘 최근 검색 (숙소)")
            for q in recents:
                if st.button(q, key=f"recent_{q}", width="stretch"):
                    st.session_state["location_input"] = q
                    st.session_state["search_trigger"] = True
                    st.session_state["search_mode"] = "직접 검색"
                    st.rerun()

        favorites: dict = st.session_state.get("favorites", {})
        if favorites:
            st.divider()
            st.caption(f"⭐ 찜한 숙소 ({len(favorites)})")
            for fav_key, place in list(favorites.items()):
                st.markdown(f"**{place.get('place_name')}**")
                st.caption(place.get("road_address_name") or place.get("address_name", ""))
                if st.button("제거", key=f"remove_fav_{fav_key}"):
                    del st.session_state["favorites"][fav_key]
                    st.rerun()


def render_night_status(route_options: list[dict], lodging_map: dict) -> None:
    """숙박일별로 숙소가 정해졌는지 한눈에 보여준다."""
    st.caption("📋 숙박일별 진행 현황")
    cols = st.columns(len(route_options))
    for col, night in zip(cols, route_options):
        night_key = f"night_{night['night_index']}"
        chosen = lodging_map.get(night_key)
        label = f"{night['date_from'].strftime('%m/%d')} 밤"
        if chosen:
            col.success(f"{label}\n\n✓ {chosen['name']}")
        else:
            col.warning(f"{label}\n\n미정")


page_header(
    "주변 숙소",
    "짜 놓은 동선에서 가까운 순으로 숙소를 골라 보세요.",
)

region = require_region()
if region:
    if not st.session_state.trip_context.get("plan"):
        st.info("일정이 아직 없습니다. 일정을 먼저 만들면 동선 기준 정렬을 쓸 수 있습니다.")

    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    if not KAKAO_API_KEY:
        st.error(
            "**KAKAO_REST_API_KEY**가 설정되지 않았습니다.\n\n"
            "1. [Kakao Developers](https://developers.kakao.com/)에서 애플리케이션을 생성하고 "
            "**REST API 키**를 발급받으세요.\n"
            "2. `.env` 파일에 `KAKAO_REST_API_KEY=발급받은키` 형태로 추가하세요.\n"
            "3. (Docker로 실행 중이라면) `--env-file .env` 옵션으로 컨테이너를 재시작하세요."
        )
        st.stop()

    render_sidebar()

    plan = st.session_state.trip_context.get("plan") or {}
    nights = build_night_options(plan)
    route_options = []
    for night in nights:
        center_candidate = night_center(night)
        if center_candidate:
            route_options.append({**night, "center": center_candidate})

    if not route_options and "location_input" not in st.session_state:
        # 동선 기반 추천을 쓸 수 없을 때는 이미 확정된 여행지 위치로 바로 검색해 준다.
        st.session_state["location_input"] = region["name"]
        st.session_state["search_trigger"] = True

    if "radius_override" in st.session_state:
        st.session_state["radius_choice"] = st.session_state.pop("radius_override")
    if "night_idx_override" in st.session_state:
        st.session_state["selected_night_idx"] = st.session_state.pop("night_idx_override")
    auto_triggered = st.session_state.pop("search_trigger", False)

    # P2에서 일정이 바뀌면 밤 후보 개수도 바뀔 수 있으므로, 이전에 골라둔 인덱스가
    # 더 이상 유효하지 않으면 선택값을 초기화해서 selectbox 오류를 막는다.
    if st.session_state.get("selected_night_idx", 0) >= len(route_options):
        st.session_state.pop("selected_night_idx", None)

    with st.container(border=True):
        if route_options:
            mode = st.segmented_control(
                "검색 방식",
                options=["동선 기반 추천", "직접 검색"],
                default="동선 기반 추천",
                key="search_mode",
            )
            mode = mode or "동선 기반 추천"
        else:
            mode = "직접 검색"
            st.caption(
                "일정에 좌표 정보가 있는 장소가 없어 직접 검색만 가능합니다. "
                "P2에서 일정을 등록하면 하루의 마지막 일정과 다음날 첫 일정 사이로 자동 추천됩니다."
            )

        if mode == "동선 기반 추천":
            current_idx = st.session_state.get("selected_night_idx", 0)
            prev_col, select_col, radius_col = st.columns([1, 3, 1.2])
            with prev_col:
                if current_idx > 0 and st.button("◀ 이전 밤", key="prev_night_btn", width="stretch"):
                    st.session_state["selected_night_idx"] = current_idx - 1
                    st.rerun()
            with select_col:
                night_idx = st.selectbox(
                    "숙박일 선택",
                    options=list(range(len(route_options))),
                    format_func=lambda i: night_option_label(route_options[i]),
                    key="selected_night_idx",
                )
            with radius_col:
                radius_m = st.pills(
                    "검색 반경",
                    options=RADIUS_OPTIONS,
                    default=RADIUS_OPTIONS[1],
                    format_func=lambda m: f"{m}m",
                    key="radius_choice",
                )
            if radius_m is None:
                radius_m = RADIUS_OPTIONS[1]

            selected_night = route_options[night_idx]
            anchors = night_anchors(selected_night)
            display_center = selected_night["center"]
            night_key = f"night_{selected_night['night_index']}"
            night_label = night_option_label(selected_night)
            last_item, first_item = selected_night["last_item"], selected_night["first_item"]
            if last_item and first_item:
                st.caption(
                    f"🕐 {selected_night['date_from'].strftime('%m/%d')} {last_item['time']} "
                    f"{last_item['name']} → {selected_night['date_to'].strftime('%m/%d')} "
                    f"{first_item['time']} {first_item['name']} — 두 일정 근처와 그 사이를 함께 검색합니다."
                )
            elif last_item:
                st.caption(f"🕐 {last_item['name']} 근처로 검색합니다. (다음날 일정에 좌표 정보가 없습니다.)")
            else:
                st.caption(f"🕐 {first_item['name']} 근처로 검색합니다. (전날 일정에 좌표 정보가 없습니다.)")

            current = st.session_state.get("nearby_stays_result")
            if (
                current is None
                or current.get("night_key") != night_key
                or current.get("radius_m") != radius_m
            ):
                run_search_for_anchors(anchors, radius_m, night_key, night_label, display_center)
        else:
            col_main, col_radius = st.columns([3, 1])
            with col_main:
                location_query = st.text_input(
                    "검색 위치",
                    placeholder="예: 부산 해운대해수욕장, 서울 강남역",
                    key="location_input",
                )
            with col_radius:
                radius_m = st.pills(
                    "검색 반경",
                    options=RADIUS_OPTIONS,
                    default=RADIUS_OPTIONS[1],
                    format_func=lambda m: f"{m}m",
                    key="radius_choice",
                )
            if radius_m is None:
                radius_m = RADIUS_OPTIONS[1]
            if st.button("🔍 숙소 검색", type="primary") or auto_triggered:
                run_search(location_query, radius_m)

    result = st.session_state.get("nearby_stays_result")
    if result:
        center = result["center"]
        places = result["places"]
        result_night_key = result["night_key"]
        result_night_label = result["night_label"]

        st.subheader(f"📍 {center['label']} 주변 {result['radius_m']}m 검색 결과")

        if not places:
            st.info("검색 반경 내에 숙박시설이 없습니다.")
            current_idx = RADIUS_OPTIONS.index(result["radius_m"])
            if current_idx < len(RADIUS_OPTIONS) - 1:
                next_radius = RADIUS_OPTIONS[current_idx + 1]
                if st.button(f"반경을 {next_radius}m로 넓혀서 재검색"):
                    st.session_state["radius_override"] = next_radius
                    st.session_state["search_trigger"] = True
                    st.rerun()
        else:
            render_summary(places)
            st.divider()

            type_options = sorted({classify_lodging_type(p) for p in places})
            filter_col, sort_col = st.columns([2, 1])
            with filter_col:
                selected_types = st.pills(
                    "숙소 유형 필터",
                    options=type_options,
                    selection_mode="multi",
                    default=type_options,
                    key="type_filter",
                )
            with sort_col:
                sort_by = st.segmented_control(
                    "정렬 기준", options=["거리순", "이름순"], default="거리순", key="sort_by"
                )
            if sort_by is None:
                sort_by = "거리순"

            filtered = [p for p in places if classify_lodging_type(p) in (selected_types or [])]
            if sort_by == "거리순":
                filtered.sort(key=lambda p: p.get("distance") or 0)
            else:
                filtered.sort(key=lambda p: p.get("place_name", ""))

            st.write(f"총 **{len(filtered)}개**의 숙박시설을 표시하고 있습니다.")

            if filtered:
                lodging_points = [
                    {
                        "lat": float(p["y"]),
                        "lon": float(p["x"]),
                        "color": LODGING_MARKER_COLOR,
                        "size": 18,
                    }
                    for p in filtered
                ]
                anchor_points = [
                    {"lat": a["y"], "lon": a["x"], "color": CENTER_MARKER_COLOR, "size": 45}
                    for a in result.get("anchors", [center])
                ]
                map_df = pd.DataFrame(lodging_points + anchor_points)
                st.caption("🟡 검색 기준 위치 · 🔵 숙박시설")
                st.map(map_df, latitude="lat", longitude="lon", color="color", size="size")

                csv_df = pd.DataFrame(
                    [
                        {
                            "이름": p.get("place_name"),
                            "유형": classify_lodging_type(p),
                            "주소": p.get("road_address_name") or p.get("address_name"),
                            "거리(m)": p.get("distance"),
                            "전화번호": p.get("phone"),
                            "카카오맵": p.get("place_url"),
                        }
                        for p in filtered
                    ]
                )
                st.download_button(
                    "📥 CSV로 다운로드",
                    data=csv_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name="nearby_stays.csv",
                    mime="text/csv",
                )

                cards_per_row = 3
                with st.spinner("숙소 이미지를 불러오는 중..."):
                    place_images = fetch_place_images([p.get("place_url") for p in filtered])
                for row_start in range(0, len(filtered), cards_per_row):
                    row_places = filtered[row_start : row_start + cards_per_row]
                    row_cols = st.columns(cards_per_row)
                    for col, place in zip(row_cols, row_places):
                        with col:
                            render_result_card(
                                place,
                                result_night_key,
                                result_night_label,
                                len(route_options),
                                place_images.get(place.get("place_url")),
                            )
            else:
                st.info("선택한 유형에 해당하는 숙소가 없습니다. 필터를 조정해보세요.")

    if route_options:
        st.divider()
        render_night_status(route_options, st.session_state.trip_context.get("lodging", {}))

    lodging_map = st.session_state.trip_context.get("lodging", {})
    if lodging_map:
        st.divider()
        if st.button("다음 단계로 (여행자 보험) →", type="primary", width="stretch"):
            st.switch_page("views/page4_insurance.py")

    with st.expander("물려받은 상태 전체 보기"):
        st.json(st.session_state.trip_context)
