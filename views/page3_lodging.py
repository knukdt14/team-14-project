"""
P3. 주변 숙소  (담당: C 축 — 백엔드 / D 축 — 화면)

카카오 로컬 API로 숙소를 조회하고, P2 일정 동선을 기준으로 정렬한다.

필요한 환경변수:
    KAKAO_REST_API_KEY : 카카오 디벨로퍼스에서 발급받은 REST API 키
"""

import os
import sys
from pathlib import Path

import altair as alt
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
    padding: 14px 18px 10px 18px;
    margin-bottom: 10px;
    background: #101728;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.stay-card:hover {
    box-shadow: 0 10px 26px rgba(233,185,73,0.12);
    transform: translateY(-2px);
}
.stay-row { display: flex; align-items: flex-start; gap: 14px; }
.stay-thumb {
    flex: 0 0 52px; width: 52px; height: 52px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center; font-size: 1.4rem;
}
.stay-info { flex: 1; min-width: 0; }
.stay-name-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.stay-name { font-size: 1.05rem; font-weight: 700; color: #EDEBE4; }
.stay-address { color: #7A8199; font-size: 0.85rem; margin: 4px 0 0 0; }
.stay-badge {
    display: inline-block; color: #0B0F1C; font-size: 0.72rem; font-weight: 700;
    padding: 2px 10px; border-radius: 999px; vertical-align: middle;
}
.stay-selected { color: #E9B949; font-size: 0.8rem; font-weight: 700; }
</style>
"""


def classify_lodging_type(place: dict) -> str:
    """place_name/category_name 텍스트로 숙소 유형을 대략 분류한다."""
    text = f"{place.get('category_name', '')} {place.get('place_name', '')}"
    for label, keywords in LODGING_TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return label
    return "기타"


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


def select_lodging(place: dict, lodging_type: str, address: str) -> None:
    """이 숙소를 이번 여행의 숙소로 확정하고 trip_context에 기록한다."""
    st.session_state.trip_context["lodging"] = {
        "name": place.get("place_name"),
        "type": lodging_type,
        "address": address,
        "distance_m": place.get("distance"),
        "phone": place.get("phone"),
        "place_url": place.get("place_url"),
        "latitude": float(place["y"]),
        "longitude": float(place["x"]),
    }


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

    with st.spinner("주변 숙소를 검색하는 중..."):
        try:
            places = search_nearby_lodging(center["x"], center["y"], radius_m, KAKAO_API_KEY)
        except requests.RequestException as exc:
            st.error(f"카카오 API 호출 중 오류가 발생했습니다: {exc}")
            return

    st.session_state["nearby_stays_result"] = {
        "center": center,
        "radius_m": radius_m,
        "places": places,
    }
    add_recent_search(query)
    # 새 검색 결과의 숙소 유형 구성이 이전과 다를 수 있으므로, 필터 위젯의
    # 이전 선택 상태를 초기화해서 다음 렌더링에서 새 옵션 전체로 다시 시작한다.
    st.session_state.pop("type_filter", None)


def render_result_card(place: dict) -> None:
    name = place.get("place_name", "이름 없음")
    address = place.get("road_address_name") or place.get("address_name", "")
    distance_m = place.get("distance")
    phone = place.get("phone") or "전화번호 정보 없음"
    place_url = place.get("place_url")
    lodging_type = classify_lodging_type(place)
    badge_color = TYPE_BADGE_COLORS.get(lodging_type, TYPE_BADGE_COLORS["기타"])
    fav_key = place.get("id") or place_url or name
    is_favorite = fav_key in st.session_state.get("favorites", {})
    is_selected = st.session_state.trip_context.get("lodging", {}).get("name") == name

    icon = TYPE_ICONS.get(lodging_type, "📍")
    selected_badge = (
        '<span class="stay-selected">✓ 이번 여행 숙소</span>' if is_selected else ""
    )
    # 마크다운은 빈 줄을 만나면 이어지던 raw HTML 블록을 끊고 뒷부분을 코드블록으로
    # 취급해버리므로, 조건부 뱃지는 반드시 앞 태그와 같은 줄에 붙여야 한다.
    st.markdown(
        f"""
        <div class="stay-card">
            <div class="stay-row">
                <div class="stay-thumb" style="background:{badge_color};">{icon}</div>
                <div class="stay-info">
                    <div class="stay-name-row">
                        <span class="stay-name">{name}</span>
                        <span class="stay-badge" style="background:{badge_color};">{lodging_type}</span>{selected_badge}
                    </div>
                    <div class="stay-address">{address}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 1.3, 1.1, 1, 1.3])
    if distance_m:
        cols[0].metric("거리", f"{int(distance_m):,} m", label_visibility="collapsed")
    cols[1].write(f"📞 {phone}")
    if place_url:
        cols[2].markdown(f"[카카오맵에서 보기]({place_url})")
    fav_label = "💛 찜 해제" if is_favorite else "🤍 찜하기"
    if cols[3].button(fav_label, key=f"fav_{fav_key}"):
        toggle_favorite(place)
        st.rerun()
    select_label = "✓ 선택됨" if is_selected else "이 숙소로 정하기"
    if cols[4].button(
        select_label, key=f"select_{fav_key}", type="primary" if not is_selected else "secondary"
    ):
        select_lodging(place, lodging_type, address)
        st.switch_page("views/page4_insurance.py")


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

    type_counts = (
        pd.Series(types)
        .value_counts()
        .sort_values(ascending=False)
        .rename_axis("숙소 유형")
        .reset_index(name="개수")
    )
    chart = (
        alt.Chart(type_counts)
        .mark_bar(color="#E9B949")
        .encode(
            x=alt.X("숙소 유형:N", sort="-y", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("개수:Q"),
            tooltip=["숙소 유형", "개수"],
        )
    )
    st.altair_chart(chart, width="stretch")


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

    if "location_input" not in st.session_state:
        # 이 페이지에 처음 들어왔을 때는 이미 확정된 여행지 위치로 바로 검색해 준다.
        st.session_state["location_input"] = region["name"]
        st.session_state["search_trigger"] = True

    if "radius_override" in st.session_state:
        st.session_state["radius_choice"] = st.session_state.pop("radius_override")

    with st.container(border=True):
        col_input, col_radius = st.columns([3, 1])
        with col_input:
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

        auto_triggered = st.session_state.pop("search_trigger", False)
        if st.button("🔍 숙소 검색", type="primary") or auto_triggered:
            run_search(location_query, radius_m)

    result = st.session_state.get("nearby_stays_result")
    if result:
        center = result["center"]
        places = result["places"]

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
                center_point = {
                    "lat": center["y"],
                    "lon": center["x"],
                    "color": CENTER_MARKER_COLOR,
                    "size": 45,
                }
                map_df = pd.DataFrame(lodging_points + [center_point])
                st.caption("🟡 검색한 위치 · 🔵 숙박시설")
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

                for place in filtered:
                    render_result_card(place)
            else:
                st.info("선택한 유형에 해당하는 숙소가 없습니다. 필터를 조정해보세요.")

    with st.expander("물려받은 상태 전체 보기"):
        st.json(st.session_state.trip_context)
