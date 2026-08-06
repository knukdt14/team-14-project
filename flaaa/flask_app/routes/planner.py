"""P2 여행 플래너 — P1의 확정 목적지를 TourAPI-RAG-LLM 일정으로 연결한다.

원래 Streamlit 버전은 st.session_state에 프로필/후보/생성결과/직접추가일정을
따로따로 들고 있었다. 여기서도 같은 키 구성을 세션에 그대로 유지해서
services/planner.py의 파이프라인만 갈아끼웠다.
"""

import json
import os
from datetime import date, timedelta
from uuid import uuid4

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from pydantic import ValidationError

from flask_app.state import get_trip_context, region_required, set_trip_context_value
from flask_app.services import planner as planner_service
from services.tour_api import TourApiError

planner_bp = Blueprint("planner", __name__, url_prefix="/planner")

RELATIONSHIPS = ["가족", "부부", "연인", "친구", "동료", "혼자"]
TRANSPORT = ["자동차", "대중교통", "항공", "도보 중심"]
STYLE_OPTIONS = ["맛집", "관광", "자연", "문화·역사", "카페", "액티비티", "휴식", "아이 동반"]
CATEGORIES = ["관광지", "음식점", "문화시설", "축제", "숙소", "기타"]


def _default_profile(region: dict) -> dict:
    origin = get_trip_context().get("origin", {})
    today = date.today()
    return {
        "title": f"{region['name']} 여행",
        "start": (today + timedelta(days=7)).isoformat(),
        "end": (today + timedelta(days=9)).isoformat(),
        "count": 2,
        "relationship": "친구",
        "departure": origin.get("name", "출발지 미입력"),
        "transport": "자동차",
        "budget": 300000,
        "styles": ["맛집", "관광"],
        "preferences": "",
    }


def _ensure_profile(region: dict) -> dict:
    if session.get("planner_region_key") != region["name"]:
        session["planner_profile"] = _default_profile(region)
        session["planner_candidates"] = []
        session["planner_query"] = ""
        session["planner_mode"] = ""
        session["planner_llm"] = None
        session["planner_itinerary"] = []
        session["planner_flight_info"] = None
        session["planner_region_key"] = region["name"]
        session.modified = True
    return session.setdefault("planner_profile", _default_profile(region))


def _sync_context(region: dict) -> None:
    """P3는 실제 일정이 생긴 뒤에만 plan 컨텍스트를 받는다.

    일정을 전부 지워서 items가 다시 비면, trip_context["plan"]도 같이 지워야
    한다 — 안 그러면 P3가 이미 삭제된 옛 일정을 기준으로 계속 동선을 그린다.
    """
    items = session.get("planner_itinerary", [])
    if not items:
        ctx = get_trip_context()
        if ctx.pop("plan", None) is not None:
            session["trip_context"] = ctx
            session.modified = True
        return
    points = [
        (i["latitude"], i["longitude"])
        for i in items
        if i.get("latitude") is not None and i.get("longitude") is not None
    ]
    if points:
        center = {"latitude": sum(p[0] for p in points) / len(points), "longitude": sum(p[1] for p in points) / len(points)}
    else:
        center = {"latitude": region["latitude"], "longitude": region["longitude"]}
    profile = session.get("planner_profile", {})
    plan = session.get("planner_llm")
    set_trip_context_value(
        "plan",
        {
            "title": profile.get("title", f"{region['name']} 여행"),
            "destination": region["name"],
            "start_date": profile.get("start"),
            "end_date": profile.get("end"),
            "summary": (plan or {}).get("summary", "직접 구성한 일정"),
            "itinerary": items,
            "center": center,
            "transportation": profile.get("transport"),
            "flight_operation": session.get("planner_flight_info"),
            "route_points": _route_points(items, profile.get("transport")),
            "retrieval_query": session.get("planner_query", ""),
        },
    )


def _route_points(items: list[dict], transport: str | None) -> list[dict]:
    """Return only scheduled places for the planner route map."""
    return [
        {key: item[key] for key in ("name", "latitude", "longitude", "date", "time")}
        | {"kind": "schedule"}
        for item in sorted(items, key=lambda value: (value["date"], value["time"]))
        if item.get("latitude") is not None and item.get("longitude") is not None
    ]


def _profile_from_form(form, region: dict) -> tuple[dict | None, str | None]:
    """Validate the planner form so one submit can save and generate."""
    try:
        start = date.fromisoformat(form["start"])
        end = date.fromisoformat(form["end"])
        count = min(20, max(1, int(form.get("count") or 2)))
        budget = min(5_000_000, max(50_000, int(form.get("budget") or 300000)))
    except (KeyError, ValueError):
        return None, "여행 날짜, 인원, 예산을 올바르게 입력해 주세요."
    if end < start or (end - start).days > 13:
        return None, "여행 날짜는 시작일 이후이며 최대 14일이어야 합니다."
    transport = form.get("transport") if form.get("transport") in TRANSPORT else "자동차"
    return {
        "title": form.get("title") or "국내 여행",
        "start": start.isoformat(), "end": end.isoformat(),
        "count": count, "relationship": form.get("relationship") or "친구",
        "departure": form.get("departure") or "출발지 미입력",
        "transport": transport, "budget": budget,
        "styles": form.getlist("styles"), "preferences": form.get("preferences", ""),
    }, None


@planner_bp.route("/")
@region_required
def index():
    region = get_trip_context()["region"]
    profile = _ensure_profile(region)
    start = date.fromisoformat(profile["start"])
    end = date.fromisoformat(profile["end"])
    days = planner_service.days_between(start, end)

    itinerary = session.get("planner_itinerary", [])
    itinerary_by_day = {}
    for day in days:
        day_items = sorted(
            (item for item in itinerary if item["date"] == day.isoformat()),
            key=lambda item: item["time"],
        )
        itinerary_by_day[day.isoformat()] = day_items

    return render_template(
        "planner.html",
        active_step="planner",
        region=region,
        profile=profile,
        days=days,
        relationships=RELATIONSHIPS,
        transports=TRANSPORT,
        style_options=STYLE_OPTIONS,
        categories=CATEGORIES,
        candidates=session.get("planner_candidates", []),
        query=session.get("planner_query", ""),
        mode=session.get("planner_mode", ""),
        plan=session.get("planner_llm"),
        itinerary=itinerary,
        itinerary_by_day=itinerary_by_day,
        flight_info=session.get("planner_flight_info"),
        route_points=_route_points(itinerary, profile.get("transport")),
        developer_mode=os.getenv("DEVELOPER_MODE", "").lower() in {"1", "true", "yes", "on"},
        tmap_enabled=session.get("planner_use_tmap", os.getenv("ENABLE_TMAP_TRANSIT", "").lower() in {"1", "true", "yes", "on"}),
    )


@planner_bp.route("/profile", methods=["POST"])
@region_required
def set_profile():
    region = get_trip_context()["region"]
    form = request.form
    try:
        start = date.fromisoformat(form["start"])
        end = date.fromisoformat(form["end"])
    except (KeyError, ValueError):
        flash("날짜 형식이 올바르지 않습니다.", "error")
        return redirect(url_for("planner.index"))

    if end < start or (end - start).days > 13:
        flash("여행 날짜는 시작일 이후이며 최대 14일이어야 합니다.", "error")
        return redirect(url_for("planner.index"))

    try:
        count = min(20, max(1, int(form.get("count") or 2)))
        budget = min(5_000_000, max(50_000, int(form.get("budget") or 300000)))
    except ValueError:
        flash("인원과 예산은 숫자로 입력해 주세요.", "error")
        return redirect(url_for("planner.index"))

    transport = form.get("transport") if form.get("transport") in TRANSPORT else "자동차"
    session["planner_profile"] = {
        "title": form.get("title") or "국내 여행",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "count": count,
        "relationship": form.get("relationship") or "친구",
        "departure": form.get("departure") or "출발지 미입력",
        "transport": transport,
        "budget": budget,
        "styles": request.form.getlist("styles"),
        "preferences": form.get("preferences", ""),
    }
    session["planner_region_key"] = region["name"]
    session["planner_candidates"] = []
    session["planner_llm"] = None
    session["planner_flight_info"] = None
    session["planner_use_tmap"] = form.get("use_tmap") == "on"
    session["planner_itinerary"] = []
    _sync_context(region)
    session.modified = True
    flash("여행 조건을 적용했습니다.", "success")
    return redirect(url_for("planner.index"))


@planner_bp.route("/generate", methods=["POST"])
@region_required
def generate():
    region = get_trip_context()["region"]
    profile, error = _profile_from_form(request.form, region)
    if error:
        flash(error, "error")
        return redirect(url_for("planner.index"))
    session["planner_profile"] = profile
    session["planner_region_key"] = region["name"]
    session["planner_candidates"] = []
    session["planner_llm"] = None
    session["planner_flight_info"] = None
    session["planner_itinerary"] = []
    session["planner_use_tmap"] = request.form.get("use_tmap") == "on"
    if not profile:
        flash("먼저 여행 조건을 적용해 주세요.", "warning")
        return redirect(url_for("planner.index"))

    start = date.fromisoformat(profile["start"])
    end = date.fromisoformat(profile["end"])
    flight_info = planner_service.load_flight_info(
        region, get_trip_context().get("origin", {}), start, profile["transport"],
        session.get("planner_use_tmap", False),
    )
    full_profile = planner_service.build_profile(
        region,
        {
            "start": start, "end": end, "departure": profile["departure"], "count": profile["count"],
            "relationship": profile["relationship"], "transport": profile["transport"],
            "budget": profile["budget"], "styles": profile["styles"], "preferences": profile["preferences"],
            "flight_operation": flight_info,
        },
    )
    try:
        result = planner_service.generate_plan(region, full_profile, start, end)
    except (TourApiError, ValidationError, ValueError) as error:
        flash(f"일정 생성에 실패했습니다: {error}", "error")
        return redirect(url_for("planner.index"))
    except Exception as error:  # noqa: BLE001 — 외부 API 호출이라 방어적으로 넓게 잡는다
        flash(f"예상하지 못한 API 오류가 발생했습니다: {error}", "error")
        return redirect(url_for("planner.index"))

    session["planner_llm"] = result["plan"]
    session["planner_candidates"] = result["candidates"]
    session["planner_query"] = result["query"]
    session["planner_mode"] = result["mode"]
    session["planner_flight_info"] = flight_info
    session["planner_itinerary"] = [
        {"id": str(uuid4()), "date": day["date"], **item}
        for day in result["plan"]["itinerary"]
        for item in day["items"]
    ]
    session.modified = True
    _sync_context(region)
    flash("TourAPI 근거 기반 AI 일정을 생성했습니다.", "success")
    return redirect(url_for("planner.index"))


@planner_bp.route("/apply", methods=["POST"])
@region_required
def apply_plan():
    region = get_trip_context()["region"]
    plan = session.get("planner_llm")
    if not plan:
        flash("적용할 AI 일정이 없습니다.", "warning")
        return redirect(url_for("planner.index"))

    itinerary = [
        {"id": str(uuid4()), "date": day["date"], **item}
        for day in plan["itinerary"]
        for item in day["items"]
    ]
    session["planner_itinerary"] = itinerary
    session.modified = True
    _sync_context(region)
    flash("AI 일정을 내 플래너에 적용했습니다.", "success")
    return redirect(url_for("planner.index"))


@planner_bp.route("/manual", methods=["POST"])
@region_required
def add_manual():
    region = get_trip_context()["region"]
    form = request.form
    name = (form.get("name") or "").strip()
    if not name:
        flash("일정 이름을 입력해 주세요.", "warning")
        return redirect(url_for("planner.index"))

    itinerary = session.get("planner_itinerary", [])
    itinerary.append(
        {
            "id": str(uuid4()),
            "date": form.get("date"),
            "time": form.get("time") or "10:00",
            "content_id": "manual",
            "name": name,
            "category": form.get("category") or "기타",
            "address": "",
            "latitude": None,
            "longitude": None,
            "duration_minutes": 60,
            "travel_minutes_from_previous": 0,
            "estimated_cost": 0,
            "memo": form.get("memo", ""),
        }
    )
    session["planner_itinerary"] = itinerary
    session.modified = True
    _sync_context(region)
    return redirect(url_for("planner.index"))


@planner_bp.route("/delete/<item_id>", methods=["POST"])
@region_required
def delete_item(item_id: str):
    region = get_trip_context()["region"]
    itinerary = [item for item in session.get("planner_itinerary", []) if item["id"] != item_id]
    session["planner_itinerary"] = itinerary
    session.modified = True
    _sync_context(region)
    return redirect(url_for("planner.index"))


@planner_bp.route("/next", methods=["POST"])
@region_required
def next_step():
    """Keep the P2 → P3 handoff explicit and only navigate with an itinerary."""
    region = get_trip_context()["region"]
    if not session.get("planner_itinerary"):
        flash("일정을 생성하거나 직접 추가한 뒤 숙소 선택으로 이동할 수 있습니다.", "warning")
        return redirect(url_for("planner.index"))
    _sync_context(region)
    return redirect(url_for("lodging.index"))


@planner_bp.route("/export.json")
@region_required
def export_json():
    region = get_trip_context()["region"]
    profile = session.get("planner_profile", {})
    export = {
        "title": profile.get("title"),
        "destination": region["name"],
        "start_date": profile.get("start"),
        "end_date": profile.get("end"),
        "itinerary": session.get("planner_itinerary", []),
        "retrieval_query": session.get("planner_query", ""),
    }
    return Response(
        json.dumps(export, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={region['name']}_travel_plan.json"},
    )
