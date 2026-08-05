"""P2 여행 플래너 — P1의 확정 목적지를 TourAPI-RAG-LLM 일정으로 연결한다."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, time, timedelta
from math import log2
from pathlib import Path
from uuid import uuid4

import numpy as np
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from pydantic import ValidationError

sys.path.append(str(Path(__file__).resolve().parent.parent))
from services.planner_rag import RELATIONSHIP_KEYWORDS, build_search_query, rank_candidates  # noqa: E402
from services.planner_schemas import TravelPlan, strict_response_format  # noqa: E402
from services.tour_api import TourApiError, fetch_place_candidates  # noqa: E402
from services.tmap_transit import TmapTransitError, fetch_air_travel_info  # noqa: E402
from views._common import page_header, require_region  # noqa: E402


RELATIONSHIPS = ["가족", "부부", "연인", "친구", "동료", "혼자"]
TRANSPORT = ["자동차", "대중교통", "항공", "도보 중심"]
STYLES = ["맛집", "관광", "자연", "문화·역사", "카페", "액티비티", "휴식", "아이 동반"]
CATEGORIES = ["관광지", "음식점", "문화시설", "축제", "숙소", "기타"]


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _init(region: dict) -> None:
    load_dotenv()
    origin = st.session_state.trip_context.get("origin", {})
    defaults = {
        "planner_title": f"{region['name']} 여행", "planner_start": date.today() + timedelta(days=7),
        "planner_end": date.today() + timedelta(days=9), "planner_count": 2, "planner_relationship": "친구",
        "planner_departure": origin.get("name", "출발지 미입력"), "planner_transport": "자동차",
        "planner_budget": 300000, "planner_styles": ["맛집", "관광"], "planner_preferences": "",
        "planner_candidates": [], "planner_query": "", "planner_mode": "", "planner_llm": None,
        "planner_itinerary": [], "planner_flight_info": None,
        "planner_use_tmap": _env_flag("ENABLE_TMAP_TRANSIT"), "planner_region_key": region["name"],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if st.session_state.planner_region_key != region["name"]:
        for key in ("planner_candidates", "planner_query", "planner_mode", "planner_llm", "planner_itinerary", "planner_flight_info"):
            st.session_state[key] = [] if key in ("planner_candidates", "planner_itinerary") else "" if key in ("planner_query", "planner_mode") else None
        st.session_state.planner_region_key = region["name"]
        st.session_state.planner_title = f"{region['name']} 여행"


def _developer_options() -> None:
    """Expose chargeable API controls only in an explicitly enabled dev mode."""
    if not _env_flag("DEVELOPER_MODE"):
        return
    with st.sidebar.expander("개발자 옵션"):
        st.session_state.planner_use_tmap = st.toggle(
            "TMAP 대중교통 API 사용",
            value=st.session_state.planner_use_tmap,
            help="항공 이동·운행 정보를 조회합니다. 호출 비용이 발생할 수 있습니다.",
        )


def _profile(region: dict) -> dict:
    days = _days(st.session_state.planner_start, st.session_state.planner_end)
    return {
        "destination": region["name"], "departure": st.session_state.planner_departure,
        "count": st.session_state.planner_count, "relationship": st.session_state.planner_relationship,
        "nights": len(days) - 1, "days": len(days), "transportation": st.session_state.planner_transport,
        "budget_per_person": st.session_state.planner_budget, "styles": st.session_state.planner_styles,
        "preferences": st.session_state.planner_preferences,
        "flight_operation": st.session_state.planner_flight_info,
    }


def _load_flight_info(region: dict) -> None:
    """Load only flight-route facts; the LLM must not invent flight details."""
    st.session_state.planner_flight_info = None
    if st.session_state.planner_transport != "항공":
        return
    if not st.session_state.planner_use_tmap:
        st.session_state.planner_flight_info = {
            "available": False,
            "disabled": True,
            "message": "개발자 설정에서 TMAP 대중교통 API 사용이 꺼져 있습니다.",
        }
        return
    origin = st.session_state.trip_context.get("origin", {})
    try:
        st.session_state.planner_flight_info = fetch_air_travel_info(
            float(origin["latitude"]), float(origin["longitude"]),
            float(region["latitude"]), float(region["longitude"]),
            st.session_state.planner_start,
            os.getenv("TMAP_APP_KEY", ""),
        )
    except (KeyError, TypeError, ValueError):
        st.session_state.planner_flight_info = {"available": False, "message": "출발지 좌표가 없어 항공 이동 경로를 조회하지 못했습니다."}
    except TmapTransitError as error:
        st.session_state.planner_flight_info = {"available": False, "message": str(error)}


@st.cache_data(ttl=21600, show_spinner=False)
def _tour_candidates(sido: str, sigungu: str, name: str, start: date, end: date, rows: int, _key: str) -> list[dict]:
    return fetch_place_candidates({"sido": sido, "sigungu": sigungu, "name": name}, start, end, _key, rows)


@st.cache_data(ttl=86400, show_spinner=False)
def _embedding(text: str, model: str, _token: str) -> np.ndarray:
    client = InferenceClient(provider="hf-inference", api_key=_token)
    return np.asarray(client.feature_extraction(text, model=model), dtype=np.float32)


def _retrieve(region: dict) -> None:
    tour_key = os.getenv("TOUR_API_KEY") or os.getenv("TOUR_API_SERVICE_KEY", "")
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not tour_key:
        raise ValueError(".env에 TOUR_API_KEY를 설정해 주세요.")
    if not hf_token:
        raise ValueError(".env에 HF_TOKEN을 설정해 주세요.")
    candidates = _tour_candidates(region["sido"], region.get("sigungu", ""), region["name"], st.session_state.planner_start, st.session_state.planner_end, int(os.getenv("TOUR_API_ROWS_PER_TYPE", "6")), tour_key)
    query = build_search_query(_profile(region))
    ranked, mode = rank_candidates(candidates, query, lambda text: _embedding(text, os.getenv("HF_EMBEDDING_MODEL", "intfloat/multilingual-e5-small"), hf_token), int(os.getenv("RAG_TOP_K", "12")))
    if not ranked:
        raise ValueError("이 지역에서 일정 후보 장소를 찾지 못했습니다.")
    st.session_state.planner_candidates, st.session_state.planner_query, st.session_state.planner_mode = ranked, query, mode


def _prompt(region: dict, candidates: list[dict]) -> str:
    dates = [day.isoformat() for day in _days(st.session_state.planner_start, st.session_state.planner_end)]
    allowed = [{key: item.get(key) for key in ("content_id", "name", "category", "address", "latitude", "longitude", "overview", "event_start", "event_end")} for item in candidates]
    profile = _profile(region)
    flight_rule = "항공편 정보가 없으면 항공편·공항·시간을 지어내지 말 것. 항공편 정보가 있으면 도착 시간 이후부터 첫날 일정을 시작할 것."
    return f"""당신은 국내 여행 동선 전문가입니다. 반드시 아래 TourAPI 후보만 사용해 JSON 스키마에 맞는 일정을 만드세요.
여행 조건: {json.dumps(profile, ensure_ascii=False)}
여행 날짜: {json.dumps(dates, ensure_ascii=False)}
관계별 선호 키워드: {json.dumps(RELATIONSHIP_KEYWORDS.get(profile['relationship'], []), ensure_ascii=False)}
TourAPI 후보: {json.dumps(allowed, ensure_ascii=False)}
항공 이동 규칙: {flight_rule}
규칙: 후보 밖의 장소를 지어내지 말 것. itinerary는 날짜 목록을 같은 순서로 정확히 한 번씩 모두 포함할 것. 가까운 장소를 묶고, 이동·식사·휴식과 1인 기준 추정비용을 반영할 것. 축제는 여행 기간과 겹칠 때만 사용한다."""


def _validate(plan: TravelPlan, dates: list[str], allowed_ids: set[str]) -> None:
    actual = [day.date for day in plan.itinerary]
    if actual != dates:
        raise ValueError(f"LLM 일정이 여행 날짜 전체를 포함하지 않았습니다. 기대: {', '.join(dates)} / 응답: {', '.join(actual)}")
    invalid = {item.content_id for day in plan.itinerary for item in day.items if item.content_id not in allowed_ids}
    if invalid:
        raise ValueError(f"TourAPI 후보에 없는 장소가 포함되었습니다: {', '.join(sorted(invalid))}")


def _parse_llm_plan(content: str) -> TravelPlan:
    """Decode JSON output defensively when a provider emits an invalid escape."""
    payload = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        # JSON allows only these escaped characters. Keep valid escapes intact and
        # turn every other backslash into a literal backslash.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', payload)
        data = json.loads(repaired)
    return TravelPlan.model_validate(data)


def _generate(region: dict) -> None:
    _load_flight_info(region)
    if not st.session_state.planner_candidates:
        _retrieve(region)
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not token:
        raise ValueError(".env에 HF_TOKEN을 설정해 주세요.")
    candidates = st.session_state.planner_candidates
    dates = [day.isoformat() for day in _days(st.session_state.planner_start, st.session_state.planner_end)]
    messages = [{"role": "system", "content": "TourAPI 근거만 사용하고 JSON schema만 출력하세요."}, {"role": "user", "content": _prompt(region, candidates)}]
    client = InferenceClient(provider=os.getenv("HF_LLM_PROVIDER", "nscale"), api_key=token)
    last_error: Exception | None = None
    for attempt in range(2):
        result = client.chat_completion(model=os.getenv("HF_MODEL", "Qwen/Qwen3-32B"), messages=messages, response_format=strict_response_format([item["content_id"] for item in candidates], dates), max_tokens=min(8000, max(3500, len(dates) * 1000)), temperature=0.3 if attempt == 0 else 0.1)
        text = result.choices[0].message.content or ""
        try:
            plan = _parse_llm_plan(text)
            _validate(plan, dates, {item["content_id"] for item in candidates})
            st.session_state.planner_llm = plan.model_dump(mode="json")
            return
        except (ValidationError, ValueError) as error:
            last_error = error
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": f"검증 실패: {error}. 누락 없이 모든 날짜를 순서대로 포함한 전체 JSON만 다시 출력하세요."}]
    raise ValueError(f"재생성 뒤에도 일정 검증에 실패했습니다: {last_error}")


def _sync_context(region: dict) -> None:
    items = st.session_state.planner_itinerary
    points = [(item["latitude"], item["longitude"]) for item in items if item.get("latitude") is not None and item.get("longitude") is not None]
    center = {"latitude": sum(point[0] for point in points) / len(points), "longitude": sum(point[1] for point in points) / len(points)} if points else {"latitude": region["latitude"], "longitude": region["longitude"]}
    st.session_state.trip_context["plan"] = {
        "title": st.session_state.planner_title, "destination": region["name"],
        "start_date": st.session_state.planner_start.isoformat(), "end_date": st.session_state.planner_end.isoformat(),
        "summary": (st.session_state.planner_llm or {}).get("summary", "직접 구성한 일정"),
        "itinerary": items, "center": center,
        "transportation": st.session_state.planner_transport,
        "flight_operation": st.session_state.planner_flight_info,
        "route_points": _route_points(items),
    }


def _route_points(items: list[dict]) -> list[dict]:
    """Build map-only route points, including a car trip's departure and return."""
    points = [
        {
            "name": item["name"], "latitude": item["latitude"], "longitude": item["longitude"],
            "date": item["date"], "time": item["time"], "kind": "schedule",
        }
        for item in sorted(items, key=lambda value: (value["date"], value["time"]))
        if item.get("latitude") is not None and item.get("longitude") is not None
    ]
    if not points or st.session_state.planner_transport != "자동차":
        return points
    origin = st.session_state.trip_context.get("origin", {})
    if origin.get("latitude") is None or origin.get("longitude") is None:
        return points
    departure = {"name": f"출발지 · {origin.get('name', '출발지')}", "latitude": origin["latitude"], "longitude": origin["longitude"], "date": points[0]["date"], "time": "출발", "kind": "departure"}
    return_point = {"name": f"복귀 · {origin.get('name', '출발지')}", "latitude": origin["latitude"], "longitude": origin["longitude"], "date": points[-1]["date"], "time": "복귀", "kind": "return"}
    return [departure, *points, return_point]


def _show_itinerary_map() -> None:
    all_points = _route_points(st.session_state.planner_itinerary)
    schedule_points = [point for point in all_points if point["kind"] == "schedule"]
    if not schedule_points:
        st.info("좌표가 있는 일정 장소가 생기면 동선 지도가 표시됩니다.")
        return
    st.subheader("일정 지도")
    dates = list(dict.fromkeys(point["date"] for point in schedule_points))
    labels = {"전체 일정": "전체 일정"}
    labels.update({value: f"Day {index + 1} · {date.fromisoformat(value):%m/%d}" for index, value in enumerate(dates)})
    selected_date = st.radio("표시할 동선", list(labels), format_func=labels.get, horizontal=True, key="planner_map_date")
    if selected_date == "전체 일정":
        points = all_points
    else:
        points = [point for point in schedule_points if point["date"] == selected_date]
        if st.session_state.planner_transport == "자동차":
            if selected_date == dates[0] and all_points[0]["kind"] == "departure":
                points.insert(0, all_points[0])
            if selected_date == dates[-1] and all_points[-1]["kind"] == "return":
                points.append(all_points[-1])
    map_points = [{"name": point["name"], "date": point["date"], "time": point["time"], "position": [point["longitude"], point["latitude"]], "color": [242, 183, 5] if point["kind"] != "schedule" else [52, 120, 246]} for point in points]
    path = [{"path": [point["position"] for point in map_points]}]
    latitude = sum(point["latitude"] for point in points) / len(points)
    longitude = sum(point["longitude"] for point in points) / len(points)
    span = max(max(point["latitude"] for point in points) - min(point["latitude"] for point in points), max(point["longitude"] for point in points) - min(point["longitude"] for point in points), 0.01)
    zoom = max(5, min(13, 8.5 - log2(span)))
    deck = pdk.Deck(
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=pdk.ViewState(latitude=latitude, longitude=longitude, zoom=zoom, pitch=0),
        layers=[
            pdk.Layer("PathLayer", path, get_path="path", get_color=[41, 98, 255], width_scale=12, width_min_pixels=4, pickable=False),
            pdk.Layer("ScatterplotLayer", map_points, get_position="position", get_fill_color="color", get_radius=180, radius_min_pixels=6, pickable=True),
        ],
        tooltip={"text": "{time} · {name}\n{date}"},
    )
    st.pydeck_chart(deck, use_container_width=True, height=460)
    if st.session_state.planner_transport == "자동차":
        st.caption("자동차 동선에는 출발지 → 첫 일정과 마지막 일정 → 출발지(복귀) 구간이 포함됩니다.")


def _apply_plan(region: dict) -> None:
    st.session_state.planner_itinerary = [{"id": str(uuid4()), "date": day["date"], **item} for day in st.session_state.planner_llm["itinerary"] for item in day["items"]]
    _sync_context(region)


def _profile_form() -> bool:
    with st.form("planner_profile"):
        one, two, three = st.columns(3)
        with one:
            title = st.text_input("여행 이름", st.session_state.planner_title)
            departure = st.text_input("출발지", st.session_state.planner_departure)
            count = st.number_input("여행 인원", 1, 20, st.session_state.planner_count)
        with two:
            start = st.date_input("출발일", st.session_state.planner_start)
            end = st.date_input("마지막 날", st.session_state.planner_end)
            relationship = st.selectbox("관계", RELATIONSHIPS, index=RELATIONSHIPS.index(st.session_state.planner_relationship))
        with three:
            transport = st.selectbox("주요 이동수단", TRANSPORT, index=TRANSPORT.index(st.session_state.planner_transport))
            budget = st.number_input("1인 예산(원)", 50000, 5000000, st.session_state.planner_budget, step=50000)
            styles = st.multiselect("여행 스타일", STYLES, default=st.session_state.planner_styles)
        preferences = st.text_area("추가 요청", st.session_state.planner_preferences, placeholder="예: 아이와 함께라 무리 없는 동선, 해산물 제외")
        submitted = st.form_submit_button("여행 조건 반영 · 일정 생성 · 지도 보기", type="primary", use_container_width=True)
    if submitted:
        if end < start or (end - start).days > 13:
            st.error("여행 날짜는 시작일 이후이며 최대 14일이어야 합니다.")
            return False
        st.session_state.update({"planner_title": title or "국내 여행", "planner_start": start, "planner_end": end, "planner_departure": departure or "출발지 미입력", "planner_count": count, "planner_relationship": relationship, "planner_transport": transport, "planner_budget": budget, "planner_styles": styles, "planner_preferences": preferences, "planner_candidates": [], "planner_llm": None})
        st.success("여행 조건을 적용했습니다.")
        return True
    return False


def _show_plan(region: dict) -> None:
    plan = st.session_state.planner_llm
    if not plan:
        return
    st.success(plan["summary"])
    st.metric("예상 총비용 (1인)", f"{plan['total_estimated_cost']:,}원")
    for day in plan["itinerary"]:
        with st.expander(f"{day['date']} · 예상 {day['daily_budget']:,}원", expanded=True):
            for index, item in enumerate(day["items"]):
                if index:
                    minutes = item.get("travel_minutes_from_previous", 0)
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:8px;margin:8px 0 8px 14px;'>"
                        f"<span style='width:2px;height:24px;background:#4f8bf9;display:inline-block'></span>"
                        f"<span style='font-size:1.25rem;color:#4f8bf9'>↓</span>"
                        f"<span style='padding:3px 10px;border-radius:16px;background:#17345d;color:#d9e8ff;font-weight:600'>이동 {minutes}분</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with st.container(border=True):
                    st.markdown(f"### {item['time']} · {item['name']}")
                    category, cost = st.columns(2)
                    category.caption("카테고리")
                    category.markdown(f"**{item['category']}**")
                    cost.caption("예상 비용")
                    cost.markdown(f"**{item['estimated_cost']:,}원**")
                    if item.get("address"):
                        st.caption(f"📍 {item['address']}")
                    if item.get("memo"):
                        st.write(item["memo"])
    flight_info = st.session_state.planner_flight_info
    if flight_info and st.session_state.planner_transport == "항공":
        if flight_info.get("available"):
            flight = flight_info["flights"][0]
            operating = "운행 정보 확인" if flight.get("operating") else "운행 종료 또는 미확인"
            st.info(f"TMAP 항공 이동 반영: {flight['departure']} → {flight['arrival']} · 약 {flight.get('minutes') or '-'}분 · {operating}")
        elif flight_info.get("disabled"):
            st.caption(f"TMAP 항공 이동 정보: {flight_info['message']}")
        else:
            st.warning(f"TMAP 항공 이동 정보: {flight_info.get('message')}")


def _manual(region: dict, days: list[date]) -> None:
    with st.expander("일정 직접 추가"):
        with st.form("manual_schedule"):
            one, two = st.columns(2)
            with one:
                selected = st.selectbox("날짜", days, format_func=lambda value: value.strftime("%m/%d"))
                selected_time = st.time_input("시간", time(10, 0))
            with two:
                name = st.text_input("장소 또는 일정 이름")
                category = st.selectbox("유형", CATEGORIES)
            memo = st.text_area("메모")
            submitted = st.form_submit_button("일정 추가", use_container_width=True)
        if submitted and name.strip():
            st.session_state.planner_itinerary.append({"id": str(uuid4()), "date": selected.isoformat(), "time": selected_time.strftime("%H:%M"), "content_id": "manual", "name": name.strip(), "category": category, "address": "", "latitude": None, "longitude": None, "duration_minutes": 60, "travel_minutes_from_previous": 0, "estimated_cost": 0, "memo": memo})
            _sync_context(region)
            st.rerun()


def _saved(region: dict, days: list[date]) -> None:
    if not st.session_state.planner_itinerary:
        return
    _show_itinerary_map()
    st.subheader("내 일정")
    tabs = st.tabs([f"Day {i + 1} · {day.strftime('%m/%d')}" for i, day in enumerate(days)])
    for day, tab in zip(days, tabs):
        with tab:
            items = sorted((item for item in st.session_state.planner_itinerary if item["date"] == day.isoformat()), key=lambda item: item["time"])
            if not items:
                st.caption("등록된 일정이 없습니다.")
            for item in items:
                left, right = st.columns([8, 1])
                left.markdown(f"**{item['time']} · {item['name']}**  \n{item['category']} · {item.get('memo') or '메모 없음'}")
                if right.button("삭제", key=f"delete_{item['id']}"):
                    st.session_state.planner_itinerary = [saved for saved in st.session_state.planner_itinerary if saved["id"] != item["id"]]
                    _sync_context(region)
                    st.rerun()
    if st.button("주변 숙소로 이동", type="primary", use_container_width=True):
        _sync_context(region)
        st.switch_page("views/page3_lodging.py")


page_header("여행 플래너", "P1에서 확정한 여행지의 실제 TourAPI 장소만 골라 AI가 날짜별 일정을 구성합니다.")
region = require_region()
if region:
    _init(region)
    _developer_options()
    st.info(f"선택 여행지: **{region['name']}** · 출발지에서 약 {region['distance_km']}km")
    st.caption(f"목적지 연동: 랜덤 목적지 선정(P1) · **{region['name']}**")
    generate_requested = _profile_form()
    days = _days(st.session_state.planner_start, st.session_state.planner_end)
    st.info(f"{st.session_state.planner_departure} 출발 · {len(days)-1}박 {len(days)}일 · {st.session_state.planner_count}명 {st.session_state.planner_relationship} 여행")
    if generate_requested:
        try:
            with st.spinner("TourAPI 조회 → 장소 설명 임베딩 검색 → LLM 일정 생성 중입니다..."):
                _generate(region)
            _apply_plan(region)
            st.success("AI 일정을 확정하고 지도에 동선을 표시했습니다.")
        except (TourApiError, ValidationError, ValueError) as error:
            st.error(f"일정 생성에 실패했습니다: {error}")
        except Exception as error:
            st.error(f"예상하지 못한 API 오류가 발생했습니다: {error}")
    _show_plan(region)
    _manual(region, days)
    _saved(region, days)
    # P3는 실제 일정이 만들어진 뒤에만 plan 컨텍스트를 받는다.
    if st.session_state.planner_itinerary:
        _sync_context(region)
