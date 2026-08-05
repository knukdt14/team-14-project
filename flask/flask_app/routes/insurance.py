"""P4. 여행자 보험 — Streamlit 버전(views/page4_insurance.py)과 동일하게 아직은
플레이스홀더다. 실제 보험료 계산/약관 RAG 챗봇은 범위 밖."""

from flask import Blueprint, render_template

from flask_app.state import get_trip_context, region_required

insurance_bp = Blueprint("insurance", __name__, url_prefix="/insurance")

TODO_ITEMS = [
    "보험료 산정 — 기본요율 × 일수 × 연령대 × 인원",
    "플랜 비교 — 든든플랜 · 안심플랜",
    "약관 PDF 파싱 → 청킹(보험사 메타데이터) → bge-m3 → Chroma",
    "가입자 모드 — 선택한 보험사 청크만 필터 검색",
    "비교 모드 — 4개사 균등 검색 후 비교표 생성",
    "근거 조항 표시 · 면책 문구 상시 노출",
]


@insurance_bp.route("/")
@region_required
def index():
    region = get_trip_context()["region"]
    return render_template(
        "insurance.html",
        active_step="insurance",
        region=region,
        todo_items=TODO_ITEMS,
    )
