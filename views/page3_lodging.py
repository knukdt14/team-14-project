"""
P3. 주변 숙소  (담당: C 축 — 백엔드 / D 축 — 화면)

카카오 로컬 API로 숙소를 조회하고, P2 일정 동선을 기준으로 정렬한다.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from views._common import page_header, require_region, show_inherited, todo_panel  # noqa: E402

page_header(
    "주변 숙소",
    "짜 놓은 동선에서 가까운 순으로 숙소를 골라 보세요.",
)

region = require_region()
if region:
    show_inherited(region)
    st.write("")

    if not st.session_state.trip_context.get("plan"):
        st.info("일정이 아직 없습니다. 일정을 먼저 만들면 동선 기준 정렬을 쓸 수 있습니다.")

    todo_panel(
        owner="C 축 (백엔드) + D 축 (화면)",
        items=[
            "카카오 로컬 API 숙소 조회 (카테고리 AD5)",
            "필터 — 인원 · 날짜 · 숙소 유형 · 가격대 · 편의시설",
            "정렬 — 가격순 · 별점순 · 동선 거리순",
            "P2 일정 중심점(centroid) 계산 후 Haversine 거리 산출",
            "숙소 카드 — 이미지 · 주소 · 예상 총액",
            "숙소 마커와 일정 마커를 함께 띄우는 지도",
        ],
        context_note=(
            "가격과 평점은 시연용 시뮬레이션 데이터를 씁니다. "
            "고른 숙소는 trip_context['lodging'] 에 넣어 주세요."
        ),
    )

    with st.expander("물려받은 상태 전체 보기"):
        st.json(st.session_state.trip_context)
