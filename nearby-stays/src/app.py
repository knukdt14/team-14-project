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


def _kakao_headers() -> dict:
    return {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}


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


def render_result_card(place: dict) -> None:
    name = place.get("place_name", "이름 없음")
    address = place.get("road_address_name") or place.get("address_name", "")
    distance_m = place.get("distance")
    phone = place.get("phone") or "전화번호 정보 없음"
    place_url = place.get("place_url")

    with st.container(border=True):
        st.markdown(f"**🏨 {name}**")
        st.caption(address)
        cols = st.columns(3)
        if distance_m:
            cols[0].metric("거리", f"{int(distance_m):,} m")
        cols[1].write(f"📞 {phone}")
        if place_url:
            cols[2].markdown(f"[카카오맵에서 보기]({place_url})")


def main() -> None:
    st.set_page_config(page_title="주변 숙소 리스트", page_icon="🏨", layout="wide")
    st.title("🏨 주변 숙소 리스트")
    st.caption("카카오 로컬 API로 여행지 주변 숙박시설을 검색합니다.")

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

    if not KAKAO_API_KEY:
        st.error(
            "**KAKAO_REST_API_KEY**가 설정되지 않았습니다.\n\n"
            "1. [Kakao Developers](https://developers.kakao.com/)에서 애플리케이션을 생성하고 "
            "**REST API 키**를 발급받으세요.\n"
            "2. `.env` 파일에 `KAKAO_REST_API_KEY=발급받은키` 형태로 추가하세요.\n"
            "3. (Docker로 실행 중이라면) `--env-file .env` 옵션으로 컨테이너를 재시작하세요."
        )
        st.stop()

    col_input, col_radius = st.columns([3, 1])
    with col_input:
        location_query = st.text_input(
            "여행지 주소 또는 지명", placeholder="예: 부산 해운대해수욕장, 서울 강남역"
        )
    with col_radius:
        radius_m = st.radio(
            "검색 반경", options=[500, 1000, 2000, 3000], index=1, format_func=lambda m: f"{m}m"
        )

    search_clicked = st.button("🔍 숙소 검색", type="primary")

    if search_clicked:
        if not location_query.strip():
            st.warning("위치를 입력해주세요.")
        else:
            with st.spinner("위치를 찾는 중..."):
                try:
                    center = geocode_location(location_query.strip(), KAKAO_API_KEY)
                except requests.RequestException as exc:
                    st.error(f"카카오 API 호출 중 오류가 발생했습니다: {exc}")
                    center = None

            if center is None:
                st.warning("입력하신 위치를 찾을 수 없습니다. 다른 표현으로 다시 시도해보세요.")
                st.session_state.pop("nearby_stays_result", None)
            else:
                with st.spinner("주변 숙소를 검색하는 중..."):
                    try:
                        places = search_nearby_lodging(
                            center["x"], center["y"], radius_m, KAKAO_API_KEY
                        )
                    except requests.RequestException as exc:
                        st.error(f"카카오 API 호출 중 오류가 발생했습니다: {exc}")
                        places = []

                st.session_state["nearby_stays_result"] = {
                    "center": center,
                    "radius_m": radius_m,
                    "places": places,
                }

    result = st.session_state.get("nearby_stays_result")
    if result:
        center = result["center"]
        places = result["places"]

        st.subheader(f"📍 {center['label']} 주변 {result['radius_m']}m 검색 결과")

        if not places:
            st.info("검색 반경 내에 숙박시설이 없습니다. 반경을 넓혀서 다시 검색해보세요.")
        else:
            st.write(f"총 **{len(places)}개**의 숙박시설을 찾았습니다.")

            map_df = pd.DataFrame(
                [{"lat": float(p["y"]), "lon": float(p["x"])} for p in places]
                + [{"lat": center["y"], "lon": center["x"]}]
            )
            st.map(map_df, latitude="lat", longitude="lon", size=20)

            for place in places:
                render_result_card(place)


if __name__ == "__main__":
    main()
