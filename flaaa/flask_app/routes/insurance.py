"""P4. 여행자 보험 — views/page4_insurance.py(Streamlit)의 두 탭을 그대로 옮긴다.

- 보험료 계산 탭: 아직 미구현(todo_panel), 원본과 동일하게 자리만 표시.
- 약관 상담 챗봇 탭: 가입 여부 -> (가입자면 보험사 선택 -> 채팅) / (미가입이면 걱정거리
  하나 입력 -> 4개사 비교) 순서의 작은 상태 머신. Streamlit은 st.session_state로
  이 상태를 들고 있었는데, 여기서는 그 키들을 Flask session에 그대로 옮겨 담았다.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from flask_app.services import insurance as insurance_service
from flask_app.state import get_trip_context, region_required, set_trip_context_value

insurance_bp = Blueprint("insurance", __name__, url_prefix="/insurance")

TODO_ITEMS = [
    "보험료 산정 — 기본요율 × 일수 × 연령대 × 인원",
    "플랜 비교 — 든든플랜 · 안심플랜",
]


def _reset_chat_state() -> None:
    session["insurance_stage"] = "start"
    session["insurance_chat_history"] = []
    session.pop("insurer_code", None)
    session.pop("insurer_display", None)
    session.pop("worry_text", None)
    session.pop("worry_intent", None)


@insurance_bp.route("/")
@region_required
def index():
    region = get_trip_context()["region"]
    stage = session.get("insurance_stage", "start")
    tab = request.args.get("tab", "premium")

    plan_info = get_trip_context().get("plan")
    days, days_from_planner = insurance_service.days_from_plan(plan_info)
    premium_rows = None
    premium_age_counts = session.get("premium_age_counts")
    if premium_age_counts and session.get("premium_plan"):
        premium_rows = insurance_service.calculate_premiums(
            days, premium_age_counts, session["premium_plan"]
        )

    comparison_rows = None
    comparison_comment = None
    resources_error = None
    if stage == "no_insurance_chat" and session.get("worry_intent"):
        intent = session["worry_intent"]
        if intent != "기타" and intent in insurance_service.INTENT_LABELS:
            try:
                resources = insurance_service.load_resources()
                comparison_rows = insurance_service.compare_insurers(intent, resources)
                comparison_comment = insurance_service.broadest_coverage_comment(comparison_rows)
                set_trip_context_value("insurance", {"mode": "comparison", "intent": intent})
            except insurance_service.ResourcesUnavailable as error:
                resources_error = str(error)

    return render_template(
        "insurance.html",
        active_step="insurance",
        region=region,
        todo_items=TODO_ITEMS,
        tab=tab,
        stage=stage,
        insurer_options=insurance_service.DISPLAY_TO_CODE,
        insurer_display=session.get("insurer_display"),
        chat_history=session.get("insurance_chat_history", []),
        worry_text=session.get("worry_text"),
        worry_intent=session.get("worry_intent"),
        intent_labels=insurance_service.INTENT_LABELS,
        no_coverage_intents=insurance_service.NO_COVERAGE_INTENTS,
        comparison_rows=comparison_rows,
        comparison_comment=comparison_comment,
        resources_error=resources_error,
        parse_answer=insurance_service.parse_answer_for_ui,
        friendly_text=insurance_service.build_friendly_text,
        days=days,
        days_from_planner=days_from_planner,
        plan_start=(plan_info or {}).get("start_date"),
        plan_end=(plan_info or {}).get("end_date"),
        age_groups=insurance_service.PREMIUM_AGE_GROUPS,
        selected_age_counts=premium_age_counts or {},
        selected_plan=session.get("premium_plan"),
        premium_rows=premium_rows,
    )


@insurance_bp.route("/premium", methods=["POST"])
@region_required
def premium():
    plan = request.form.get("plan")
    if plan not in ("기본형", "고급형"):
        flash("플랜을 선택해 주세요.", "warning")
        return redirect(url_for("insurance.index", tab="premium"))

    age_counts = {}
    for age_group in insurance_service.PREMIUM_AGE_GROUPS:
        idx = insurance_service.PREMIUM_AGE_GROUPS.index(age_group)
        raw = request.form.get(f"age_count_{idx}", "0")
        try:
            age_counts[age_group] = max(int(raw), 0)
        except ValueError:
            age_counts[age_group] = 0

    if sum(age_counts.values()) == 0:
        flash("최소 1명 이상 인원을 입력해 주세요.", "warning")
        return redirect(url_for("insurance.index", tab="premium"))

    session["premium_age_counts"] = age_counts
    session["premium_plan"] = plan
    set_trip_context_value("premium_estimated", True)
    return redirect(url_for("insurance.index", tab="premium"))


@insurance_bp.route("/start", methods=["POST"])
@region_required
def start():
    choice = request.form.get("choice")
    session["insurance_stage"] = "select_insurer" if choice == "insured" else "no_insurance_chat"
    return redirect(url_for("insurance.index", tab="chat"))


@insurance_bp.route("/insurer", methods=["POST"])
@region_required
def choose_insurer():
    display = request.form.get("insurer")
    if display not in insurance_service.DISPLAY_TO_CODE:
        flash("보험사를 선택해 주세요.", "warning")
        return redirect(url_for("insurance.index", tab="chat"))
    session["insurer_code"] = insurance_service.DISPLAY_TO_CODE[display]
    session["insurer_display"] = display
    session["insurance_stage"] = "insured_chat"
    return redirect(url_for("insurance.index", tab="chat"))


@insurance_bp.route("/chat", methods=["POST"])
@region_required
def chat():
    question = (request.form.get("question") or "").strip()
    if not question:
        return redirect(url_for("insurance.index", tab="chat"))

    insurer_code = session.get("insurer_code")
    if not insurer_code:
        flash("먼저 보험사를 선택해 주세요.", "warning")
        return redirect(url_for("insurance.index", tab="chat"))

    try:
        resources = insurance_service.load_resources()
    except insurance_service.ResourcesUnavailable as error:
        flash(f"약관 데이터가 아직 준비되지 않았습니다: {error}", "error")
        return redirect(url_for("insurance.index", tab="chat"))

    result = insurance_service.answer_question(
        question,
        insurer_code,
        resources["intent_chain"], resources["rewrite_chain"], resources["answer_chain"],
        resources["all_chunks"], resources["collection"], resources["embedder"],
    )
    history = session.get("insurance_chat_history", [])
    history.append({"question": question, "result": result})
    session["insurance_chat_history"] = history
    set_trip_context_value("insurance", {"mode": "insured", "insurer": session.get("insurer_display")})
    return redirect(url_for("insurance.index", tab="chat"))


@insurance_bp.route("/worry", methods=["POST"])
@region_required
def worry():
    question_worry = (request.form.get("worry") or "").strip()
    if not question_worry:
        return redirect(url_for("insurance.index", tab="chat"))

    try:
        resources = insurance_service.load_resources()
    except insurance_service.ResourcesUnavailable as error:
        flash(f"약관 데이터가 아직 준비되지 않았습니다: {error}", "error")
        return redirect(url_for("insurance.index", tab="chat"))

    raw_intent = resources["intent_chain"].invoke({"question": question_worry})
    session["worry_text"] = question_worry
    session["worry_intent"] = insurance_service.parse_intent(raw_intent)
    return redirect(url_for("insurance.index", tab="chat"))


@insurance_bp.route("/worry/reset", methods=["POST"])
@region_required
def worry_reset():
    session.pop("worry_text", None)
    session.pop("worry_intent", None)
    return redirect(url_for("insurance.index", tab="chat"))


@insurance_bp.route("/restart", methods=["POST"])
@region_required
def restart():
    _reset_chat_state()
    return redirect(url_for("insurance.index", tab="chat"))
