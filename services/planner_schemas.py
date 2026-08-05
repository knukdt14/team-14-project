"""Strict response schema for the planner LLM."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    content_id: str
    name: str
    category: Literal["관광지", "음식점", "문화시설", "축제"]
    address: str
    latitude: float | None
    longitude: float | None
    duration_minutes: int = Field(ge=20, le=480)
    travel_minutes_from_previous: int = Field(ge=0, le=300)
    estimated_cost: int = Field(ge=0)
    memo: str


class DayPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    daily_budget: int = Field(ge=0)
    items: list[ScheduleItem] = Field(min_length=2, max_length=8)


class TravelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    total_estimated_cost: int = Field(ge=0)
    itinerary: list[DayPlan] = Field(min_length=1, max_length=14)


def strict_response_format(content_ids: list[str], travel_dates: list[str]) -> dict:
    schema = TravelPlan.model_json_schema()
    schema["$defs"]["ScheduleItem"]["properties"]["content_id"]["enum"] = content_ids
    schema["$defs"]["DayPlan"]["properties"]["date"]["enum"] = travel_dates
    schema["properties"]["itinerary"]["minItems"] = len(travel_dates)
    schema["properties"]["itinerary"]["maxItems"] = len(travel_dates)
    return {
        "type": "json_schema",
        "json_schema": {"name": "DomesticTravelPlan", "schema": schema, "strict": True},
    }
