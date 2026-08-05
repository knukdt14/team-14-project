"""
P4. 여행자 보험  (담당: B 축 — AI·RAG / C 축 — 보험료 계산)

확정된 여행 정보로 보험료를 산정하고, 약관 RAG 챗봇으로 보장 범위를 설명한다.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from views._common import page_header, require_region, show_inherited, todo_panel  # noqa: E402

page_header(
    "여행자 보험",
    "이번 여행에 맞는 보험료를 계산하고, 약관에 근거해 답을 찾아 드립니다.",
)

region = require_region()
if region:
    show_inherited(region)
    st.write("")

    todo_panel(
        owner="B 축 (AI·RAG) + C 축 (보험료 계산)",
        items=[
            "보험료 산정 — 기본요율 × 일수 × 연령대 × 인원",
            "플랜 비교 — 든든플랜 · 안심플랜",
            "약관 PDF 파싱 → 청킹(보험사 메타데이터) → bge-m3 → Chroma",
            "가입자 모드 — 선택한 보험사 청크만 필터 검색",
            "비교 모드 — 4개사 균등 검색 후 비교표 생성",
            "근거 조항 표시 · 면책 문구 상시 노출",
        ],
        context_note=(
            "보험료는 공개 요율표 기반 시뮬레이션입니다. "
            "챗봇은 보장 여부를 단정하지 않고 보험사 확인을 권고하도록 프롬프트를 잡아 주세요."
        ),
    )

    st.caption(
        "안내 · 이 페이지가 제공하는 보험료와 답변은 참고용이며 법적 효력이 없습니다. "
        "정확한 보장 여부는 해당 보험사에 확인하세요."
    )

    with st.expander("물려받은 상태 전체 보기"):
        st.json(st.session_state.trip_context)
