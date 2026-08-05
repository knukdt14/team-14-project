"""주변 숙소 리스트 (Kakao Local API 기반)

사용자가 입력한 위치(주소 또는 지명) 주변의 숙박시설을
카카오 로컬 API로 검색해서 지도와 목록으로 보여주는 Streamlit 앱.

필요한 환경변수:
    KAKAO_REST_API_KEY : 카카오 디벨로퍼스에서 발급받은 REST API 키
"""

import os

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

KAKAO_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
KAKAO_BASE_URL = "https://dapi.kakao.com"
LODGING_CATEGORY_CODE = "AD5"  # 카카오 카테고리 그룹 코드: 숙박
REQUEST_TIMEOUT = 5  # seconds
RADIUS_OPTIONS = [500, 1000, 2000, 3000]
MAX_RECENT_SEARCHES = 5

LODGING_TYPE_KEYWORDS = {
    "호텔": ["호텔"],
    "모텔": ["모텔"],
    "펜션/풀빌라": ["펜션", "풀빌라"],
    "게스트하우스": ["게스트하우스", "게스트 하우스", "호스텔"],
    "리조트": ["리조트"],
    "캠핑/글램핑": ["캠핑", "글램핑"],
}
TYPE_BADGE_COLORS = {
    "호텔": "#ff8a3d",
    "모텔": "#7c8fee",
    "펜션/풀빌라": "#43b581",
    "게스트하우스": "#e8618c",
    "리조트": "#22b8cf",
    "캠핑/글램핑": "#a0866b",
    "기타": "#999999",
}

CARD_CSS = """
<style>
.stay-card {
    border: 1px solid #eaeaea;
    border-radius: 14px;
    padding: 14px 18px 10px 18px;
    margin-bottom: 6px;
    background: linear-gradient(135deg, #fffdf9 0%, #fff6ea 100%);
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.stay-card:hover {
    box-shadow: 0 6px 18px rgba(0,0,0,0.09);
    transform: translateY(-2px);
}
.stay-name { font-size: 1.05rem; font-weight: 700; }
.stay-address { color: #7a7a7a; font-size: 0.85rem; margin: 2px 0 6px 0; }
.stay-badge {
    display: inline-block;
    color: white;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 999px;
    margin-left: 8px;
    vertical-align: middle;
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

    st.markdown(
        f"""
        <div class="stay-card">
            <span class="stay-name">🏨 {name}</span>
            <span class="stay-badge" style="background:{badge_color};">{lodging_type}</span>
            <div class="stay-address">{address}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 1.4, 1.2, 1])
    if distance_m:
        cols[0].metric("거리", f"{int(distance_m):,} m", label_visibility="collapsed")
    cols[1].write(f"📞 {phone}")
    if place_url:
        cols[2].markdown(f"[카카오맵에서 보기]({place_url})")
    fav_label = "💛 찜 해제" if is_favorite else "🤍 찜하기"
    if cols[3].button(fav_label, key=f"fav_{fav_key}"):
        toggle_favorite(place)
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

    type_counts = pd.Series(types).value_counts().sort_values(ascending=False)
    st.bar_chart(type_counts, x_label="숙소 유형", y_label="개수")


def render_sidebar() -> None:
    with st.sidebar:
        st.header("ℹ️ 사용 방법")
        st.markdown(
            "1. 여행지 주소나 지명을 입력하세요.\n"
            "2. 검색 반경을 선택하세요.\n"
            "3. **숙소 검색** 버튼을 누르면 주변 숙박시설이 거리순으로 표시됩니다."
        )
        st.divider()
        if KAKAO_API_KEY:
            st.success("카카오 API 키가 설정되어 있습니다.")
        else:
            st.error("카카오 API 키가 설정되지 않았습니다.")

        recents: list[str] = st.session_state.get("recent_searches", [])
        if recents:
            st.divider()
            st.subheader("🕘 최근 검색")
            for q in recents:
                if st.button(q, key=f"recent_{q}", use_container_width=True):
                    st.session_state["location_input"] = q
                    st.session_state["search_trigger"] = True
                    st.rerun()

        favorites: dict = st.session_state.get("favorites", {})
        if favorites:
            st.divider()
            st.subheader(f"⭐ 찜한 숙소 ({len(favorites)})")
            for fav_key, place in list(favorites.items()):
                st.markdown(f"**{place.get('place_name')}**")
                st.caption(place.get("road_address_name") or place.get("address_name", ""))
                if st.button("제거", key=f"remove_fav_{fav_key}"):
                    del st.session_state["favorites"][fav_key]
                    st.rerun()


def main() -> None:
    st.set_page_config(page_title="주변 숙소 리스트", page_icon="🏨", layout="wide")
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    st.title("🏨 주변 숙소 리스트")
    st.caption("카카오 로컬 API로 여행지 주변 숙박시설을 검색합니다.")

    render_sidebar()

    if not KAKAO_API_KEY:
        st.error(
            "**KAKAO_REST_API_KEY**가 설정되지 않았습니다.\n\n"
            "1. [Kakao Developers](https://developers.kakao.com/)에서 애플리케이션을 생성하고 "
            "**REST API 키**를 발급받으세요.\n"
            "2. `.env` 파일에 `KAKAO_REST_API_KEY=발급받은키` 형태로 추가하세요.\n"
            "3. (Docker로 실행 중이라면) `--env-file .env` 옵션으로 컨테이너를 재시작하세요."
        )
        st.stop()

    if "radius_override" in st.session_state:
        st.session_state["radius_choice"] = st.session_state.pop("radius_override")

    col_input, col_radius = st.columns([3, 1])
    with col_input:
        location_query = st.text_input(
            "여행지 주소 또는 지명",
            placeholder="예: 부산 해운대해수욕장, 서울 강남역",
            key="location_input",
        )
    with col_radius:
        radius_m = st.radio(
            "검색 반경",
            options=RADIUS_OPTIONS,
            index=1,
            format_func=lambda m: f"{m}m",
            key="radius_choice",
        )

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
            return

        render_summary(places)
        st.divider()

        type_options = sorted({classify_lodging_type(p) for p in places})
        filter_col, sort_col = st.columns([2, 1])
        with filter_col:
            selected_types = st.multiselect(
                "숙소 유형 필터", options=type_options, default=type_options
            )
        with sort_col:
            sort_by = st.selectbox("정렬 기준", options=["거리순", "이름순"])

        filtered = [p for p in places if classify_lodging_type(p) in selected_types]
        if sort_by == "거리순":
            filtered.sort(key=lambda p: p.get("distance") or 0)
        else:
            filtered.sort(key=lambda p: p.get("place_name", ""))

        st.write(f"총 **{len(filtered)}개**의 숙박시설을 표시하고 있습니다.")

        if filtered:
            map_df = pd.DataFrame(
                [{"lat": float(p["y"]), "lon": float(p["x"])} for p in filtered]
                + [{"lat": center["y"], "lon": center["x"]}]
            )
            st.map(map_df, latitude="lat", longitude="lon", size=20)

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


if __name__ == "__main__":
    main()
