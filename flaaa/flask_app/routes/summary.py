"""P5. 여행 요약 — 1~4단계(여행지·일정·숙소·보험) 결과를 한 페이지에 모아 보여주고,
날짜순으로 일정과 숙박을 엮어 전체 이동 동선을 정리한다.
"""

from datetime import date, timedelta

from flask import Blueprint, render_template, session

from flask_app.services import insurance as insurance_service
from flask_app.state import get_trip_context, region_required

summary_bp = Blueprint("summary", __name__, url_prefix="/summary")


def _days_between(start_iso: str, end_iso: str) -> list[date]:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _build_day_groups(plan: dict | None, lodging_map: dict, days: list[date]) -> list[dict]:
    """하루 단위로 일정 항목과 그날 밤 확정된 숙소를 묶는다.

    각 그룹은 { index, date, stops } 형태다 — stops는 그날의 일정들 다음에
    그날 밤 숙소가 오는 순서로, 실제로 여행자가 움직이는 순서 그대로다.
    summary.html에서 하루씩 접고 펼칠 수 있게 이 단위로 렌더링한다.
    """
    groups: list[dict] = []
    if not plan:
        return groups

    itinerary = plan.get("itinerary") or []
    for idx, day in enumerate(days):
        day_items = sorted(
            (item for item in itinerary if item.get("date") == day.isoformat()),
            key=lambda item: item.get("time", ""),
        )
        stops = [{**item, "kind": "schedule"} for item in day_items]

        if idx < len(days) - 1:
            night = lodging_map.get(f"night_{idx}")
            if night:
                stops.append({**night, "kind": "lodging"})

        groups.append({"index": idx, "date": day, "stops": stops})

    return groups


@summary_bp.route("/")
@region_required
def index():
    ctx = get_trip_context()
    region = ctx["region"]
    plan = ctx.get("plan")
    lodging_map = ctx.get("lodging", {})
    insurance_info = ctx.get("insurance")
    premium_selection = ctx.get("premium_selection")

    days = _days_between(plan["start_date"], plan["end_date"]) if plan else []
    day_groups = _build_day_groups(plan, lodging_map, days)

    nights_total = max(len(days) - 1, 0)
    matched_night_keys = {f"night_{i}" for i in range(nights_total)}
    extra_lodging = [
        {"night_key": key, **value} for key, value in lodging_map.items() if key not in matched_night_keys
    ]

    total_cost = sum((item.get("estimated_cost") or 0) for item in (plan or {}).get("itinerary", []))
    profile = session.get("planner_profile") or {}

    return render_template(
        "summary.html",
        active_step="summary",
        region=region,
        plan=plan,
        profile=profile,
        days=days,
        day_groups=day_groups,
        extra_lodging=extra_lodging,
        insurance_info=insurance_info,
        premium_selection=premium_selection,
        intent_labels=insurance_service.INTENT_LABELS,
        total_cost=total_cost,
        nights_booked=len(lodging_map),
        nights_total=nights_total,
    )
