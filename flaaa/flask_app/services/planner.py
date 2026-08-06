"""P2 여행 플래너의 TourAPI → RAG(임베딩 검색) → LLM 일정 생성 파이프라인.

원래 views/page2_planner.py(Streamlit)의 비공개(_-prefixed) 함수들과 동일한
로직이며, st.session_state/st.cache_data 의존을 없애고 인자로 모든 걸
주고받게 바꿨다. Flask 라우트(flask_app/routes/planner.py)에서 호출한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

import numpy as np
import requests
from huggingface_hub import InferenceClient
from json_repair import repair_json
from pydantic import ValidationError

from flask_app.services.cache import cache
from services.planner_rag import RELATIONSHIP_KEYWORDS, build_search_query, rank_candidates
from services.planner_schemas import TravelPlan
from services.tmap_transit import TmapTransitError, fetch_air_travel_info
from services.tour_api import fetch_place_candidates

logger = logging.getLogger(__name__)


class BudgetExceededError(ValueError):
    pass
TOUR_SIDO_COMPATIBILITY = {"전라남도": "전라북도"}


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def build_profile(region: dict, form: dict) -> dict:
    days = days_between(form["start"], form["end"])
    return {
        "destination": region["name"],
        "departure": form["departure"],
        "count": form["count"],
        "relationship": form["relationship"],
        "nights": len(days) - 1,
        "days": len(days),
        "transportation": form["transport"],
        "budget_per_person": form["budget"],
        "styles": form["styles"],
        "preferences": form["preferences"],
        "flight_operation": form.get("flight_operation"),
    }


def load_flight_info(region: dict, origin: dict, start: date, transport: str, enabled: bool) -> dict | None:
    if transport != "항공": return None
    if not enabled: return {"available": False, "disabled": True, "message": "TMAP transit lookup is disabled."}
    try: return fetch_air_travel_info(float(origin["latitude"]), float(origin["longitude"]), float(region["latitude"]), float(region["longitude"]), start, os.getenv("TMAP_APP_KEY", ""))
    except (KeyError, TypeError, ValueError): return {"available": False, "message": "Departure coordinates are unavailable."}
    except TmapTransitError as error: return {"available": False, "message": str(error)}


@cache.memoize(timeout=21600)
def tour_candidates(sido: str, sigungu: str, name: str, latitude: float | None, longitude: float | None, start: date, end: date, rows: int, api_key: str) -> list[dict]:
    return fetch_place_candidates({"sido": sido, "sigungu": sigungu, "name": name, "lat": latitude, "lng": longitude}, start, end, api_key, rows)


@cache.memoize(timeout=86400)
def _embed_cached(text: str, model: str, token: str) -> list[float]:
    client = InferenceClient(provider="hf-inference", api_key=token)
    vector = client.feature_extraction(text, model=model)
    return np.asarray(vector, dtype=np.float32).tolist()


def retrieve_candidates(region: dict, profile: dict, start: date, end: date) -> tuple[list[dict], str, str]:
    tour_key = os.getenv("TOUR_API_KEY") or os.getenv("TOUR_API_SERVICE_KEY", "")
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not tour_key:
        raise ValueError(".env에 TOUR_API_KEY를 설정해 주세요.")
    if not hf_token:
        raise ValueError(".env에 HF_TOKEN을 설정해 주세요.")

    tour_sido = TOUR_SIDO_COMPATIBILITY.get(region["sido"], region["sido"])
    tour_sigungu = "" if tour_sido != region["sido"] else region.get("sigungu", "")
    tour_name = "" if tour_sido != region["sido"] else region["name"]
    candidates = tour_candidates(
        tour_sido, tour_sigungu, tour_name, region.get("latitude"), region.get("longitude"), start, end,
        int(os.getenv("TOUR_API_ROWS_PER_TYPE", "6")), tour_key,
    )
    logger.warning("[Planner TourAPI] selected=%s/%s candidates=%d", region["sido"], region.get("sigungu", ""), len(candidates))
    query = build_search_query(profile)
    model = os.getenv("HF_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

    def embed(text: str) -> np.ndarray:
        return np.asarray(_embed_cached(text, model, hf_token), dtype=np.float32)

    required_unique_places = max(12, ((end - start).days + 1) * 6)
    ranked, mode = rank_candidates(candidates, query, embed, min(len(candidates), max(required_unique_places, int(os.getenv("RAG_TOP_K", "12")))))
    logger.warning("[Planner RAG] input=%d ranked=%d mode=%s", len(candidates), len(ranked), mode)
    if not ranked:
        raise ValueError(f"이 지역에서 일정 후보 장소를 찾지 못했습니다. (TourAPI 후보 {len(candidates)}개, RAG 후보 {len(ranked)}개)")
    return ranked, query, mode


def build_prompt(profile: dict, candidates: list[dict], dates: list[str]) -> str:
    allowed = [
        {
            **{key: item.get(key) for key in ("content_id", "name", "category", "address", "latitude", "longitude", "event_start", "event_end")},
            # Full TourAPI descriptions can be several thousand characters
            # each. A short summary is enough for planning and prevents the
            # reasoning model from exhausting its output budget.
            "overview": (item.get("overview") or "")[:240],
        }
        for item in candidates
    ]
    return f"""당신은 국내 여행 동선 전문가입니다. 반드시 아래 TourAPI 후보만 사용해 JSON 스키마에 맞는 일정을 만드세요.
여행 조건: {json.dumps(profile, ensure_ascii=False)}
여행 날짜: {json.dumps(dates, ensure_ascii=False)}
관계별 선호 키워드: {json.dumps(RELATIONSHIP_KEYWORDS.get(profile['relationship'], []), ensure_ascii=False)}
TourAPI 후보: {json.dumps(allowed, ensure_ascii=False)}
비용 기준: 모든 비용은 1인 기준이며 total_estimated_cost는 budget_per_person를 초과하지 않는다.
규칙: 후보 밖의 장소를 지어내지 말 것. itinerary는 날짜 목록을 같은 순서로 정확히 한 번씩 모두 포함할 것. 가까운 장소를 묶고, 이동·식사·휴식과 1인 기준 추정비용을 반영할 것. 축제는 여행 기간과 겹칠 때만 사용한다."""


def validate_plan(plan: TravelPlan, dates: list[str], allowed_ids: set[str], budget_per_person: int) -> None:
    actual = [day.date for day in plan.itinerary]
    if actual != dates:
        raise ValueError(f"LLM 일정이 여행 날짜 전체를 포함하지 않았습니다. 기대: {', '.join(dates)} / 응답: {', '.join(actual)}")
    invalid = {item.content_id for day in plan.itinerary for item in day.items if item.content_id not in allowed_ids}
    if invalid:
        raise ValueError(f"TourAPI 후보에 없는 장소가 포함되었습니다: {', '.join(sorted(invalid))}")
    content_ids = [item.content_id for day in plan.itinerary for item in day.items]
    duplicates = {content_id for content_id in content_ids if content_ids.count(content_id) > 1}
    if duplicates:
        raise ValueError(f"같은 장소가 여행 전체에 중복되었습니다: {', '.join(sorted(duplicates))}")
    if plan.total_estimated_cost > budget_per_person:
        raise BudgetExceededError(f"1인 예상 총비용 {plan.total_estimated_cost:,}원이 1인 예산을 초과했습니다.")


def _repair_json(payload: str) -> str:
    """Repair common LLM JSON slips without changing field values."""
    payload = payload.replace("“", '"').replace("”", '"').replace("’", "'")
    payload = re.sub(r",\s*([}\]])", r"\1", payload)  # trailing commas
    output, in_string, escaped = [], False, False
    for index, char in enumerate(payload):
        if in_string and char in "\r\n":
            output.append("\\n")
            continue
        if char == '"' and not escaped:
            if not in_string:
                # A quoted key directly after a completed value is the common
                # "missing comma" error emitted by chat models.
                previous = next((value for value in reversed(output) if not value.isspace()), "")
                end_quote = payload.find('"', index + 1)
                tail = payload[end_quote + 1:] if end_quote >= 0 else ""
                if previous in {'"', '}', ']'} or previous.isdigit():
                    if re.match(r"\s*:", tail):
                        output.append(",")
            in_string = not in_string
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
        output.append(char)
    return "".join(output)


def parse_llm_plan(content: str) -> TravelPlan:
    """Decode and repair common JSON formatting mistakes in LLM output."""
    payload = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1)
    start, end = payload.find("{"), payload.rfind("}")
    if start >= 0 and end > start:
        payload = payload[start:end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        repaired = _repair_json(re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", payload))
        # The model occasionally omits a separator between completed JSON
        # values. json.JSONDecodeError gives the exact insertion point.
        for _ in range(3):
            try:
                data = json.loads(repaired)
                break
            except json.JSONDecodeError as error:
                if "Expecting ',' delimiter" not in error.msg or error.pos >= len(repaired):
                    data = repair_json(repaired, return_objects=True)
                    break
                repaired = repaired[:error.pos] + "," + repaired[error.pos:]
        else:
            data = repair_json(repaired, return_objects=True)
    # A model can occasionally emit one extra stop despite the prompt.  This
    # is a presentation-limit violation, not a reason to discard an otherwise
    # complete itinerary.  Keep the first six chronological entries; costs are
    # recalculated immediately after parsing by ``normalize_plan_costs``.
    if isinstance(data, dict) and isinstance(data.get("itinerary"), list):
        for day in data["itinerary"]:
            if isinstance(day, dict) and isinstance(day.get("items"), list):
                day["items"] = day["items"][:6]
    return TravelPlan.model_validate(data)


def normalize_plan_costs(plan: TravelPlan) -> TravelPlan:
    """Make item, daily, and trip-level per-person estimates agree exactly."""
    data = plan.model_dump()
    total = 0
    for day in data["itinerary"]:
        daily = sum(item["estimated_cost"] for item in day["items"])
        day["daily_budget"] = daily
        total += daily
    data["total_estimated_cost"] = total
    return TravelPlan.model_validate(data)


def replace_duplicate_places(plan: TravelPlan, candidates: list[dict]) -> TravelPlan:
    """Replace repeated LLM choices with unused TourAPI candidates when possible."""
    data = plan.model_dump()
    seen: set[str] = set()
    unused = list(candidates)
    for day in data["itinerary"]:
        for item in day["items"]:
            content_id = str(item["content_id"])
            if content_id not in seen:
                seen.add(content_id)
                unused = [candidate for candidate in unused if str(candidate["content_id"]) != content_id]
                continue
            replacement = next((candidate for candidate in unused if candidate.get("category") == item["category"]), None)
            replacement = replacement or (unused[0] if unused else None)
            if not replacement:
                continue
            item.update({key: replacement.get(key) for key in ("content_id", "name", "category", "address", "latitude", "longitude")})
            item["memo"] = "중복 없는 동선을 위해 추천 후보로 교체한 일정입니다."
            seen.add(str(replacement["content_id"]))
            unused.remove(replacement)
    return TravelPlan.model_validate(data)


def _tourapi_fallback_plan(candidates: list[dict], dates: list[str]) -> dict:
    """Return a valid, fast TourAPI itinerary when the LLM returns no content."""
    if not candidates:
        raise ValueError("일정을 만들 TourAPI 후보가 없습니다.")
    ordered = sorted(candidates, key=lambda item: (item.get("latitude") is None, item.get("latitude") or 0, item.get("longitude") or 0))
    itinerary = []
    for day_index, day in enumerate(dates):
        items = []
        for item_index in range(2):
            candidate = ordered[(day_index * 2 + item_index) % len(ordered)]
            items.append({
                "time": "10:00" if item_index == 0 else "13:00",
                "content_id": candidate["content_id"], "name": candidate["name"], "category": candidate["category"],
                "address": candidate.get("address", ""), "latitude": candidate.get("latitude"), "longitude": candidate.get("longitude"),
                "duration_minutes": 120, "travel_minutes_from_previous": 0 if item_index == 0 else 30,
                "estimated_cost": 0, "memo": "TourAPI 후보를 좌표 순서로 배치한 빠른 일정 초안입니다.",
            })
        itinerary.append({"date": day, "daily_budget": 0, "items": items})
    return {"summary": "TourAPI 기반 빠른 일정 초안입니다. 다시 생성하면 AI 세부 추천을 시도합니다.", "total_estimated_cost": 0, "itinerary": itinerary}


def _upstage_completion(messages: list[dict], max_tokens: int, temperature: float) -> str:
    api_key = os.getenv("UPSTAGE_API_KEY", "").strip()
    if not api_key: raise ValueError("UPSTAGE_API_KEY를 .env에 설정해 주세요.")
    # P2 needs a structured itinerary quickly.  Keep this separate from P4's
    # chat model: solar-pro3 returns the same JSON shape without pro4's long
    # hidden-reasoning delay. Override with PLANNER_UPSTAGE_MODEL if needed.
    payload = {"model": os.getenv("PLANNER_UPSTAGE_MODEL", "solar-pro3"), "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    try:
        response = requests.post(os.getenv("UPSTAGE_BASE_URL", "https://api.upstage.ai/v1").rstrip("/") + "/chat/completions", json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
        response.raise_for_status(); choice = response.json()["choices"][0]; content = choice["message"].get("content")
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError(f"Upstage Solar API 호출에 실패했습니다: {response.text[:300] if 'response' in locals() else error}") from error
    if not isinstance(content, str) or not content.strip():
        # JSON mode can occasionally finish without a visible content field.
        # Retry once as a normal completion; the system prompt still requires
        # a JSON object, while Pydantic validation protects the result.
        logger.warning("[Planner Upstage] empty JSON-mode content model=%s finish_reason=%s", payload["model"], choice.get("finish_reason"))
        fallback_payload = {**payload, "messages": [*messages, {"role": "system", "content": "Return a complete JSON object now. Do not return an empty response."}]}
        try:
            fallback_response = requests.post(
                os.getenv("UPSTAGE_BASE_URL", "https://api.upstage.ai/v1").rstrip("/") + "/chat/completions",
                json=fallback_payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=120,
            )
            fallback_response.raise_for_status()
            fallback_choice = fallback_response.json()["choices"][0]
            fallback_content = fallback_choice["message"].get("content")
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("Upstage Solar API가 JSON 모드 대체 요청에도 실패했습니다.") from error
        if isinstance(fallback_content, str) and fallback_content.strip():
            return fallback_content
        logger.warning("[Planner Upstage] empty fallback content model=%s finish_reason=%s", payload["model"], fallback_choice.get("finish_reason"))
        raise ValueError("Upstage Solar API가 빈 응답을 반환했습니다.")
    return content


def generate_plan(region: dict, profile: dict, start: date, end: date) -> dict:
    """전체 파이프라인: TourAPI 후보 조회 → LLM 호출(최대 2회, 검증 실패 시 재시도).

    성공하면 {"plan": dict, "candidates": [...], "query": str, "mode": str}를 돌려주고,
    실패하면 ValueError/ValidationError를 그대로 올린다 (호출부인 Flask 라우트가 처리).
    """
    candidates, query, mode = retrieve_candidates(region, profile, start, end)
    dates = [d.isoformat() for d in days_between(start, end)]
    # Never ask the model for more unique places per day than the retrieved
    # candidate pool can support across the whole trip.
    candidate_limit_per_day = max(3, len(candidates) // max(len(dates), 1))
    max_items_per_day = max(3, min(6, candidate_limit_per_day, int(profile["budget_per_person"]) // max(len(dates), 1) // 30_000))
    min_items_per_day = 3
    messages = [
        {"role": "system", "content": "Use candidate latitude/longitude to make each day a geographically efficient route. Keep the item array in real visit order, group nearby places, avoid backtracking, and give realistic chronological times and travel_minutes_from_previous. Prioritize the traveller's styles, companion preferences, and free-text request. If restaurant candidates exist, include a meal stop at 11:30-13:30 or 17:30-20:30."},
        {"role": "system", "content": "Return JSON only. Required root fields: summary, total_estimated_cost, itinerary. Every itinerary entry has date, daily_budget, items. Every item has time, content_id, name, category, address, latitude, longitude, duration_minutes, travel_minutes_from_previous, estimated_cost, memo. Use only supplied content_id values and include every requested date once."},
        {"role": "system", "content": f"Budget is a hard per-person limit of {profile['budget_per_person']} KRW. Choose only the best affordable places. Schedule {min_items_per_day} to {max_items_per_day} items per day, never reuse the same content_id anywhere in the trip, and make total_estimated_cost no greater than the budget."},
        {"role": "user", "content": build_prompt(profile, candidates, dates)},
    ]
    allowed_ids = {item["content_id"] for item in candidates}

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            text = _upstage_completion(messages, min(8_000, max(4_000, len(dates) * 1_200)), 0.3 if attempt == 0 else 0.1)
        except ValueError as error:
            last_error = error
            if attempt == 0:
                messages.append({"role": "user", "content": "JSON 본문이 비어 있었습니다. JSON만 다시 출력하세요."}); continue
            raise
        try:
            plan = normalize_plan_costs(replace_duplicate_places(parse_llm_plan(text), candidates))
            validate_plan(plan, dates, allowed_ids, int(profile["budget_per_person"]))
            return {"plan": plan.model_dump(mode="json"), "candidates": candidates, "query": query, "mode": mode}
        except BudgetExceededError as error:
            last_error = error
            messages.append({"role": "user", "content": f"The prior plan exceeded the {profile['budget_per_person']} KRW per-person limit. Make a new, smaller itinerary with fewer affordable places. Return JSON only."})
        except (ValidationError, ValueError) as error:
            last_error = error
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"검증 실패: {error}. 누락 없이 모든 날짜를 순서대로 포함한 전체 JSON만 다시 출력하세요."},
            ]
    raise ValueError(f"재생성 뒤에도 일정 검증에 실패했습니다: {last_error}")
