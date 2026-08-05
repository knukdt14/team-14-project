"""
P4. 여행자 보험  (담당: B 축 — AI·RAG / C 축 — 보험료 계산)

확정된 여행 정보로 보험료를 산정하고, 약관 RAG 챗봇으로 보장 범위를 설명한다.
이 파일에서 B축(AI·RAG) 부분만 구현함. C축(보험료 계산) 자리는 todo_panel로 남겨둠.
"""
import re
import sys
import json
from pathlib import Path

import streamlit as st
import chromadb
from sentence_transformers import SentenceTransformer

sys.path.append(str(Path(__file__).resolve().parent.parent))
from views._common import page_header, require_region, show_inherited, todo_panel  # noqa: E402

from rag_chain import load_qwen_llm, build_chains, answer_question, parse_intent  # noqa: E402
from query_pipeline import INTENT_RIDER_MAP, NO_COVERAGE_INTENTS  # noqa: E402
from clause_matcher import is_coverage_article, is_exclusion_article, split_numbered_items  # noqa: E402

# ---------------------------------------------------------------------------
# 배포 시 .env 대신 Streamlit Secrets에서 키를 읽어 환경변수로 등록
# ---------------------------------------------------------------------------
import os  # noqa: E402
try:
    if "UPSTAGE_API_KEY" in st.secrets:
        os.environ["UPSTAGE_API_KEY"] = st.secrets["UPSTAGE_API_KEY"]
except Exception:
    pass

page_header(
    "여행자 보험",
    "이번 여행에 맞는 보험료를 계산하고, 약관에 근거해 답을 찾아 드립니다.",
)

region = require_region()
if not region:
    st.stop()

show_inherited(region)
st.write("")

# ---------------------------------------------------------------------------
# 회사 코드 <-> 실제 회사명 매핑 (PDF 표지 이미지로 실제 확인한 결과)
# ---------------------------------------------------------------------------
INSURER_DISPLAY_NAMES = {
    "meritz": "메리츠화재",
    "CM8150": "현대해상",
    "inTravel": "삼성화재",
    "프로미다이렉트": "DB손해보험",
}
DISPLAY_TO_CODE = {v: k for k, v in INSURER_DISPLAY_NAMES.items()}
INSURER_HOMEPAGES = {
    "meritz": "https://www.meritzfire.com/",
    "CM8150": "https://direct.hi.co.kr/",
    "inTravel": "https://direct.samsungfire.com/",
    "프로미다이렉트": "https://www.directdb.co.kr/",
}
INTENT_LABELS = {
    "휴대품손해": "가방·캐리어·휴대폰 등 물건 도난/파손",
    "배상책임": "실수로 남에게 피해를 준 경우",
    "실손의료비_상해질병": "여행 중 다치거나 아파서 병원 이용",
    "상해질병_사망후유장해": "사망·후유장해 관련",
    "항공기지연": "비행기 지연/결항",
    "여행취소": "여행 자체를 취소하게 된 경우",
    "여권분실": "여권 분실/도난",
}

# ---------------------------------------------------------------------------
# 이 페이지 전용 스타일 - app.py의 다크 네이비/골드 팔레트를 그대로 이어받음
#   (배경/사이드바는 app.py가 이미 전역으로 처리하므로 여기선 컴포넌트만 추가)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.rag-section-label {
    font-size: 13px; font-weight: 700; color: #E9B949; letter-spacing: 1px;
    text-transform: uppercase; margin: 16px 0 6px 2px;
}
.clause-ticket {
    position: relative;
    background: #101728; border: 1.5px dashed #E9B949; border-radius: 12px;
    padding: 16px 18px; font-family: 'Pretendard', monospace;
    font-size: 14.5px; line-height: 1.65; color: #EDEBE4; white-space: pre-wrap;
}
.clause-ticket::before {
    content: "✈ ARTICLE";
    position: absolute; top: -11px; left: 14px;
    background: #0B111F; padding: 0 8px;
    font-size: 11px; letter-spacing: 2px; color: #E9B949; font-weight: 700;
}
.stamp-wrap { display: flex; justify-content: flex-end; margin-top: -14px; margin-bottom: 10px; }
.stamp {
    display: inline-block; font-weight: 700; font-size: 14px; letter-spacing: 2px;
    padding: 5px 14px; border-radius: 6px; transform: rotate(-6deg);
    border: 2px solid currentColor;
}
.stamp-covered { color: #7FD8A6; background: rgba(127,216,166,0.10); }
.stamp-not-covered { color: #E98989; background: rgba(233,137,137,0.10); }
.stamp-unclear { color: #E9B949; background: rgba(233,185,73,0.10); }
.friendly-box {
    background: #101728; border-left: 4px solid #E9B949; border-radius: 6px;
    padding: 14px 16px; font-size: 15px; line-height: 1.7; color: #EDEBE4;
}
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="약관 데이터 불러오는 중...")
def load_rag_resources():
    with open("chunks_output.json", encoding="utf-8") as f:
        all_chunks = json.load(f)
    embedder = SentenceTransformer("BAAI/bge-m3")
    client = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_collection("travel_insurance")
    classify_llm, answer_llm = load_qwen_llm()
    intent_chain, rewrite_chain, answer_chain = build_chains(classify_llm, answer_llm)

    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    summary_prompt = ChatPromptTemplate.from_template(
        "다음은 여행자보험 약관 조항입니다. 전문용어 없이, 실제로 뭘 보장하는지만 "
        "일반인이 바로 이해할 수 있는 한 문장으로 쉽게 요약하세요. 조항 번호나 '피보험자' 같은 "
        "말은 쓰지 말고, 자연스러운 한국어 한 문장으로만 답하세요.\n\n[조항]\n{clause}\n\n[한 줄 요약]"
    )
    summary_chain = summary_prompt | classify_llm | StrOutputParser()

    return {
        "all_chunks": all_chunks, "embedder": embedder, "collection": collection,
        "intent_chain": intent_chain, "rewrite_chain": rewrite_chain, "answer_chain": answer_chain,
        "summary_chain": summary_chain,
    }


def parse_answer_for_ui(answer: str) -> dict:
    if "보상되지 않습니다" in answer:
        verdict = "not_covered"
    elif "보상됩니다" in answer:
        verdict = "covered"
    else:
        verdict = "unclear"
    raw_clause = re.sub(r'위\s*(조항)?[^\n]*따라.*', '', answer, flags=re.S)
    raw_clause = re.sub(r'보상(되지\s*않습니다|됩니다)\.?', '', raw_clause)
    raw_clause = re.sub(r'\n?\d\)\s*$', '', raw_clause)
    raw_clause = raw_clause.strip(" \n") or "(관련 조항을 찾지 못했습니다)"
    return {"raw_clause": raw_clause, "verdict": verdict}


def build_friendly_text(verdict: str) -> str:
    if verdict == "covered":
        return ("이 상황은 <b>보상 대상이에요.</b> 사고 경위와 증빙자료(사진, 진단서, 영수증 등)를 "
                "챙겨서 보험사에 청구하시면 됩니다.")
    if verdict == "not_covered":
        return ("이 상황은 <b>보상 대상이 아니에요.</b> 위 조항에 나온 면책 사유에 해당하기 때문이에요. "
                "다만 세부 상황에 따라 다를 수 있으니 보험사 상담을 함께 받아보시는 걸 권장해요.")
    return "약관만으로는 확실히 판단하기 어려운 상황이에요. 보험사 고객센터에 직접 문의해보시는 걸 권장드려요."


def render_answer_card(result: dict):
    parsed = parse_answer_for_ui(result["answer"])
    st.markdown('<div class="rag-section-label">📋 관련 약관 조항</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="clause-ticket">{parsed["raw_clause"]}</div>', unsafe_allow_html=True)

    stamp_class = {"covered": "stamp-covered", "not_covered": "stamp-not-covered", "unclear": "stamp-unclear"}[parsed["verdict"]]
    stamp_label = {"covered": "보상 O", "not_covered": "보상 X", "unclear": "확인 필요"}[parsed["verdict"]]
    st.markdown(f'<div class="stamp-wrap"><span class="stamp {stamp_class}">{stamp_label}</span></div>', unsafe_allow_html=True)

    st.markdown('<div class="rag-section-label">💬 쉬운 설명</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="friendly-box">{build_friendly_text(parsed["verdict"])}</div>', unsafe_allow_html=True)

    with st.expander("분류 정보 보기 (디버그용)"):
        st.write(f"분류된 문제 유형: {result['intent']}")
        st.write(f"처리 방식: {result['mode']}")


# ---------------------------------------------------------------------------
# 탭 분리: 보험료 계산(C축, 아직 미구현) / 약관 챗봇 상담(B축, 이번에 구현)
# ---------------------------------------------------------------------------
tab_premium, tab_chat = st.tabs(["💰 보험료 계산", "🤖 약관 상담 챗봇"])

with tab_premium:
    todo_panel(
        owner="C 축 (보험료 계산)",
        items=[
            "보험료 산정 — 기본요율 × 일수 × 연령대 × 인원",
            "플랜 비교 — 든든플랜 · 안심플랜",
        ],
    )

with tab_chat:
    if "insurance_stage" not in st.session_state:
        st.session_state.insurance_stage = "start"
    if "insurance_chat_history" not in st.session_state:
        st.session_state.insurance_chat_history = []

    if st.session_state.insurance_stage == "start":
        st.write("여행자보험에 이미 가입하셨나요?")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 가입했어요", use_container_width=True):
                st.session_state.insurance_stage = "select_insurer"
                st.rerun()
        with col2:
            if st.button("🧭 아직 안 가입했어요", use_container_width=True):
                st.session_state.insurance_stage = "no_insurance_chat"
                st.rerun()

    elif st.session_state.insurance_stage == "select_insurer":
        st.write("가입하신 보험사를 선택해주세요.")
        choice = st.selectbox("보험사", list(DISPLAY_TO_CODE.keys()))
        if st.button("선택 완료"):
            st.session_state.insurer_code = DISPLAY_TO_CODE[choice]
            st.session_state.insurer_display = choice
            st.session_state.insurance_stage = "insured_chat"
            st.rerun()

    elif st.session_state.insurance_stage == "insured_chat":
        st.caption(f"🎫 가입 보험사: {st.session_state.insurer_display}")

        for turn in st.session_state.insurance_chat_history:
            with st.chat_message("user"):
                st.write(turn["question"])
            with st.chat_message("assistant"):
                render_answer_card(turn["result"])

        # 채팅 입력창은 탭 "바깥"(페이지 최하단)에 두면 화면 전체에 항상 떠서
        # 다른 탭(보험료 계산)을 보고 있어도 같이 보이는 문제가 있었음
        # (Streamlit 탭은 안 보이는 탭도 코드가 그대로 실행되기 때문).
        # 그래서 다시 탭 "안", 대화 목록 바로 밑에 둔다 - 화면 맨 아래 고정(sticky)은
        # 아니지만, 최소한 해당 탭을 보고 있을 때만 나타난다.
        question = st.chat_input("여행 중 발생한 문제를 말씀해주세요 (예: 지갑을 잃어버렸어요)", key="insured_chat_input")
        if question:
            resources = load_rag_resources()
            with st.spinner("약관 확인 중..."):
                result = answer_question(
                    question, st.session_state.insurer_code,
                    resources["intent_chain"], resources["rewrite_chain"], resources["answer_chain"],
                    resources["all_chunks"], resources["collection"], resources["embedder"],
                )
            st.session_state.insurance_chat_history.append({"question": question, "result": result})
            st.session_state.trip_context["insurance"] = {
                "mode": "insured", "insurer": st.session_state.insurer_display,
            }
            st.rerun()

        if st.button("처음으로", key="ins_back1"):
            st.session_state.insurance_stage = "start"
            st.session_state.insurance_chat_history = []
            st.rerun()

    elif st.session_state.insurance_stage == "no_insurance_chat":
        st.write("지금 여행 계획하시면서 **가장 걱정되는 게** 뭐예요? 편하게 말씀해주세요.")
        st.caption("예: 캐리어 도난이 걱정돼요 / 여행 중 다치면 병원비가 걱정돼요 / 렌트카 사고가 걱정돼요")

        question_worry = st.chat_input("지금 가장 걱정되는 여행 문제", key="worry_chat_input")
        if question_worry:
            resources = load_rag_resources()
            st.session_state.worry_text = question_worry
            raw_intent = resources["intent_chain"].invoke({"question": question_worry})
            st.session_state.worry_intent = parse_intent(raw_intent)
            st.rerun()

        if "worry_intent" in st.session_state:
            worry = st.session_state.worry_text
            intent = st.session_state.worry_intent
            with st.chat_message("user"):
                st.write(worry)

            if intent == "기타" or intent not in INTENT_LABELS:
                st.warning("말씀하신 내용을 정확한 보장 카테고리로 특정하지 못했어요. 조금 더 구체적으로 말씀해주시겠어요? (예: '가방을 도둑맞았어요' 처럼요)")
            else:
                st.success(f"**'{INTENT_LABELS.get(intent, intent)}'** 관련 상담으로 이해했어요. 4개사를 비교해드릴게요.")
                if intent in NO_COVERAGE_INTENTS:
                    st.info(f"참고로 '{intent}'는 국내여행보험 상품 특성상 보장하지 않는 경우가 많아요. 그래도 참고용으로 비교는 보여드릴게요.")

                resources = load_rag_resources()
                all_chunks = resources["all_chunks"]
                summary_chain = resources["summary_chain"]

                exclusion_counts = {}
                st.markdown('<div class="rag-section-label">📊 4개사 비교</div>', unsafe_allow_html=True)
                for code, display_name in INSURER_DISPLAY_NAMES.items():
                    rider_names = INTENT_RIDER_MAP.get(intent, {}).get(code, [])
                    matched = [c for c in all_chunks if c["insurer"] == code and c.get("rider") in rider_names]
                    coverage_chunk = next((c for c in matched if is_coverage_article(c["article"])), None)
                    exclusion_chunk = next((c for c in matched if is_exclusion_article(c["article"])), None)

                    n_items = len(split_numbered_items(exclusion_chunk["text"])) if exclusion_chunk else None
                    exclusion_counts[display_name] = n_items

                    st.markdown(f"**🏢 {display_name}**" + (f"  ·  면책 항목 {n_items}개" if n_items is not None else ""))
                    if coverage_chunk:
                        st.markdown(f'<div class="clause-ticket">{coverage_chunk["text"]}</div>', unsafe_allow_html=True)
                        with st.spinner("쉬운 말로 바꾸는 중..."):
                            one_liner = summary_chain.invoke({"clause": coverage_chunk["text"]})
                        st.markdown(f'<div class="friendly-box">🗣️ {one_liner.strip()}</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="friendly-box">이 항목은 약관상 별도 특약이 확인되지 않아요. 가입 전 직접 문의해보세요.</div>', unsafe_allow_html=True)
                    st.write("")

                valid_counts = {k: v for k, v in exclusion_counts.items() if v is not None}
                st.markdown('<div class="rag-section-label">🔎 상담원 코멘트</div>', unsafe_allow_html=True)
                if valid_counts:
                    broadest = min(valid_counts, key=valid_counts.get)
                    comment = (
                        f"약관상 면책 항목 개수만 놓고 보면 <b>{broadest}</b>가 가장 적어(총 {valid_counts[broadest]}개) "
                        f"상대적으로 보장 범위가 넓은 편으로 보여요. 다만 이건 조항 개수 기준일 뿐, "
                        f"<b>실제 보험료와 가입 한도는 다를 수 있으니</b> 아래에서 본인 여행 일정 기준으로 "
                        f"직접 견적을 비교해보시는 걸 추천드려요."
                    )
                else:
                    comment = "이 항목은 4개사 모두 약관상 명확한 특약이 확인되지 않아, 가입 전 각 사에 직접 확인해보시는 걸 권장드려요."
                st.markdown(f'<div class="friendly-box">{comment}</div>', unsafe_allow_html=True)

                st.markdown('<div class="rag-section-label">🔗 다이렉트 가입 페이지</div>', unsafe_allow_html=True)
                link_cols = st.columns(4)
                for col, (code, display_name) in zip(link_cols, INSURER_DISPLAY_NAMES.items()):
                    with col:
                        st.link_button(display_name, INSURER_HOMEPAGES[code], use_container_width=True)

                st.session_state.trip_context["insurance"] = {"mode": "comparison", "intent": intent}

            if st.button("다른 걱정거리 물어보기"):
                del st.session_state.worry_intent
                del st.session_state.worry_text
                st.rerun()

        if st.button("처음으로", key="ins_back2"):
            for k in ("worry_intent", "worry_text"):
                st.session_state.pop(k, None)
            st.session_state.insurance_stage = "start"
            st.rerun()

st.caption(
    "안내 · 이 페이지가 제공하는 보험료와 답변은 참고용이며 법적 효력이 없습니다. "
    "정확한 보장 여부는 해당 보험사에 확인하세요."
)

with st.expander("물려받은 상태 전체 보기"):
    st.json(st.session_state.trip_context)
