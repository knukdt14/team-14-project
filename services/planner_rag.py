"""Candidate ranking helpers for the travel planner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np


RELATIONSHIP_KEYWORDS = {
    "가족": ["아이", "가족", "체험", "휴식"],
    "부부": ["산책", "문화", "맛집", "여유"],
    "연인": ["카페", "야경", "사진", "데이트"],
    "친구": ["맛집", "체험", "쇼핑", "액티비티"],
    "동료": ["식사", "문화", "산책", "휴식"],
    "혼자": ["전시", "카페", "산책", "힐링"],
}


def build_search_query(profile: dict) -> str:
    relationship = profile.get("relationship", "")
    keywords = RELATIONSHIP_KEYWORDS.get(relationship, [])
    return " ".join(
        str(value) for value in (
            profile.get("destination", ""), relationship, profile.get("transportation", ""),
            " ".join(profile.get("styles", [])), " ".join(keywords), profile.get("preferences", ""),
        ) if value
    )


def _candidate_text(item: dict) -> str:
    return " ".join(str(item.get(key, "")) for key in ("name", "category", "address", "overview"))


def rank_candidates(candidates: list[dict], query: str, embed: Callable[[str], np.ndarray], top_k: int) -> tuple[list[dict], str]:
    if not candidates:
        return [], "keyword"
    try:
        query_embedding = np.asarray(embed(query), dtype=np.float32).reshape(-1)
        query_norm = np.linalg.norm(query_embedding)
        if not query_norm:
            raise ValueError("empty query embedding")
        def score_item(item: dict) -> dict:
            vector = np.asarray(embed(_candidate_text(item)), dtype=np.float32).reshape(-1)
            denominator = query_norm * np.linalg.norm(vector)
            score = float(np.dot(query_embedding, vector) / denominator) if denominator else 0.0
            return {**item, "retrieval_score": round(score, 4)}
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
            scored = list(executor.map(score_item, candidates))
        return sorted(scored, key=lambda item: item["retrieval_score"], reverse=True)[:top_k], "embedding"
    except Exception:
        query_words = {word.lower() for word in query.split() if word}
        scored = []
        for item in candidates:
            text = _candidate_text(item).lower()
            score = sum(word in text for word in query_words)
            scored.append({**item, "retrieval_score": score})
        return sorted(scored, key=lambda item: item["retrieval_score"], reverse=True)[:top_k], "keyword"
