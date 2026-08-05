"""
rider 전체 조항(제1조~제9조) 중에서, "보상하지 않는 손해"(면책) 조항의
번호(1호,2호...) 단위까지 코드로 미리 좁혀서 LLM에 넘긴다.

왜 이게 필요한가:
- 3B 모델한테 "9개 조항 전체를 읽고 → 딱 맞는 호를 스스로 찾아서 → 토씨 안 틀리고 베끼기"를
  한 번에 시키면 실패율이 높았음 (엉뚱한 조/제목 인용, 도입부만 인용하고 세부 항목은 놓침).
- 그래서 "찾기"는 파이썬 키워드 매칭(결정적, 항상 정확)으로 하고,
  "자연스럽게 답변 문장 만들기"만 LLM에 맡긴다.
"""
from __future__ import annotations
import re

# 일상어 -> 면책조항에서 실제 쓰이는 키워드 매핑 (여러 개 적어도 됨, 하나라도 겹치면 매칭)
EXCLUSION_KEYWORD_HINTS: dict[str, list[str]] = {
    "분실": ["분실", "잃어버", "잊어버", "놓고 왔", "놓고왔"],
    "방치": ["방치", "놓고 왔", "놓고왔", "두고 왔", "두고왔"],
    "도난": ["도난", "훔쳐", "도둑", "절취", "강취"],
    "고의_중과실": ["일부러", "고의로", "제가 실수로 심하게"],
    "자연소모": ["곰팡이", "녹슬", "녹이", "변색", "변질", "벌레", "쥐가"],
    "외관손상": ["긁힘", "스크래치", "찍힘", "흠집", "기스", "외관상", "외관"],
    "액체유출": ["액체", "새어", "샜어", "흘러나"],
    "천재지변": ["지진", "태풍", "해일", "화산"],
    "전쟁_소요": ["전쟁", "폭동", "테러"],
    "방사능": ["방사능", "방사선", "원전"],
    # 진짜 파손(기능 손상)은 "단순 외관상 손해(기능 지장 없음)"인 6호와는 다른 카테고리로 분리.
    # 힌트가 "외관손상" 쪽 단어(긁힘/스크래치 등)와 안 겹치게 해서, 두 카테고리가 서로 다른 걸 가리키게 함.
    "파손": ["파손", "부서", "고장", "깨졌", "깨짐", "망가"],
}

# "파손"은 면책 목록에 "단순 외관상(기능 지장 없음)"만 있고 "기능 손상되는 진짜 파손"은 없음
# -> 파손 카테고리 힌트가 면책 목록 어디에도 안 걸리면 "보상 대상"이라고 확신 가능

ARTICLE_ITEM_PATTERN = re.compile(r'(?:^|\n)\s*(\d{1,2})\.\s*(.+?)(?=(?:\n\s*\d{1,2}\.\s)|\Z)', re.S)


def is_exclusion_article(article_title: str) -> bool:
    return bool(re.search(r'(보상하지 않는|지급하지 않는|보상지 않는)', article_title or ''))


def is_coverage_article(article_title: str) -> bool:
    return bool(re.search(r'(보상하는 손해|지급사유|목적의 범위|보장종목)', article_title or ''))


def split_numbered_items(text: str) -> list[tuple[str, str]]:
    """'1. ~~~\n2. ~~~' 형태의 조항 본문을 (번호, 본문) 리스트로 쪼갠다."""
    items = []
    for m in ARTICLE_ITEM_PATTERN.finditer(text):
        num, body = m.group(1), m.group(2).strip()
        items.append((num, body))
    return items


def match_keywords(question: str) -> set[str]:
    """질문 안에 어떤 면책사유 카테고리 힌트가 있는지 찾는다."""
    matched = set()
    for category, hints in EXCLUSION_KEYWORD_HINTS.items():
        if any(h in question for h in hints):
            matched.add(category)
    return matched


def find_relevant_clauses(rider_chunks: list[dict], question: str, top_n: int = 2) -> str:
    """
    (기존과 동일 - LLM에 넘길 전체 컨텍스트 문자열을 만든다. 하위 호환용으로 유지)
    """
    result = find_relevant_clauses_structured(rider_chunks, question, top_n=top_n)
    return result["context_text"]


CONFIDENT_COVERED_IF_NO_EXCLUSION = {"도난", "파손"}  # 면책 목록에 명시적으로 없으면 "보장 대상"이라고 확신할 수 있는 카테고리
                                              # (도난은 분실과, 파손은 "단순 외관상(기능 지장 없음)" 6호와
                                              #  법적/문언적으로 구분되는 개념이라, 해당 안 하면 코드가 직접 판단해도 안전함)


def find_relevant_clauses_structured(rider_chunks: list[dict], question: str, top_n: int = 2) -> dict:
    """
    find_relevant_clauses와 동일한 매칭을 하되, LLM에 넘길 텍스트뿐 아니라
    "정확히 몇 조 몇 호가 매칭됐는지"를 구조화된 데이터로도 함께 반환한다.
    -> 매칭이 명확한 경우, 이 데이터를 이용해 코드가 인용 부분을 직접 조립하고
       LLM에게는 인용을 맡기지 않을 수 있다 (3B 모델의 인용 오류를 원천 차단).

    반환값:
      {
        "context_text": str,           # LLM에 넘길 전체 컨텍스트 (기존과 동일)
        "matched_article": str | None, # 명확히 매칭된 면책조항의 [제N조(제목)]
        "matched_items": [(번호, 본문 원문), ...],  # 명확히 매칭된 경우만 채워짐, 애매하면 빈 리스트
        "verdict": "excluded" | "covered_no_exclusion_found" | None,
        # covered_no_exclusion_found: 카테고리(예: 도난)는 특정됐는데 면책 목록 어디에도
        # 해당 사유가 없음 -> "면책 대상이 아니다 = 보상 대상이다"로 코드가 판단 (LLM이
        # "도난"과 "분실"을 혼동해 잘못된 면책 조항을 갖다 붙이는 문제를 원천 차단)
      }
    """
    coverage_chunks = [c for c in rider_chunks if is_coverage_article(c['article'])]
    exclusion_chunks = [c for c in rider_chunks if is_exclusion_article(c['article'])]

    parts = []
    for c in coverage_chunks:
        parts.append(f"[{c['article']}]\n{c['text']}")

    matched_categories = match_keywords(question)
    matched_article = None
    matched_items: list[tuple[str, str]] = []
    category_hit = {cat: False for cat in matched_categories}

    for c in exclusion_chunks:
        items = split_numbered_items(c['text'])
        if not items:
            parts.append(f"[{c['article']}]\n{c['text']}")
            continue

        picked = []
        for category in matched_categories:
            hints = EXCLUSION_KEYWORD_HINTS[category]
            for num, body in items:
                if any(h in body for h in hints) or category.split('_')[0] in body:
                    picked.append((num, body))
                    category_hit[category] = True

        if not picked:
            header = f"[{c['article']}] (아래 사유 중 질문 상황과 일치하는 것이 있는지 전부 확인)"
            body_all = "\n".join(f"{n}. {b}" for n, b in items)
            parts.append(f"{header}\n{body_all}")
        else:
            seen = set()
            lines = []
            for n, b in picked[:top_n]:
                if n in seen:
                    continue
                seen.add(n)
                lines.append(f"{n}. {b}")
                matched_items.append((n, b))
            matched_article = c['article']
            parts.append(f"[{c['article']}] (질문과 관련 있는 면책사유만 발췌)\n" + "\n".join(lines))

    confident_categories = matched_categories & CONFIDENT_COVERED_IF_NO_EXCLUSION
    verdict = None
    if matched_items:
        verdict = "excluded"
    elif confident_categories and all(not category_hit[cat] for cat in confident_categories):
        # 도난처럼 "면책 목록에 없으면 보상 대상"이라고 확신 가능한 카테고리인데,
        # 실제로 면책 목록 전체를 다 뒤져봐도 관련 항목이 하나도 없었던 경우.
        verdict = "covered_no_exclusion_found"

    return {
        "context_text": "\n\n".join(parts),
        "matched_article": matched_article if len(matched_items) <= top_n and matched_items else None,
        "matched_items": matched_items,
        "verdict": verdict,
    }


if __name__ == "__main__":
    import json
    with open("chunks_output.json", encoding="utf-8") as f:
        all_chunks = json.load(f)

    rider_chunks = [
        c for c in all_chunks
        if c["insurer"] == "meritz" and c.get("rider") == "휴대품손해(분실제외) 특별약관"
    ]
    ctx = find_relevant_clauses(rider_chunks, "지갑을 잃어버렸어요")
    print(ctx)
