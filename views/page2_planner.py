"""
P2. 여행 플래너  (담당: B 축 — AI·RAG / D 축 — 화면)

P1에서 확정된 지역을 기준으로 인원·날짜·관계·출발지 등을 입력받아
TourAPI 검색 → 임베딩 검색 → LLM 일정 생성 순서로 일정을 만든다.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from views._common import page_header, require_region, show_inherited, todo_panel  # noqa: E402

page_header(
    "여행 플래너",
    "정해진 여행지에 조건을 더하면 AI가 날짜별 일정을 짜 줍니다.",
)

region = require_region()
if region:
    show_inherited(region)
    st.write("")

    todo_panel(
        owner="B 축 (AI·RAG) + D 축 (화면)",
        items=[
            "여행자 정보 입력 폼 — 인원 · 관계 · 날짜 · 예산 · 이동수단 · 여행 스타일",
            "TourAPI 조회 — 관광지 · 음식점 · 문화시설 · 축제",
            "관계별 키워드 확장 후 임베딩 검색 (상위 12개 후보)",
            "LLM 일정 생성 — strict JSON Schema + Pydantic 검증",
            "검색 근거 보기 — 검색어 · 후보 · 유사도 점수",
            "날짜별 탭 · 지도 · JSON 다운로드",
        ],
        context_note=(
            "입력으로 쓸 값은 st.session_state.trip_context['region'] 에 들어 있습니다. "
            "결과 일정은 trip_context['plan'] 에 넣어 주세요. P3 숙소 정렬이 이 값을 씁니다."
        ),
    )

    with st.expander("물려받은 상태 전체 보기"):
        st.json(st.session_state.trip_context)
