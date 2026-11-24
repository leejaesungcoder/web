"""Claude로 가사에 대한 청자 반응을 예측한다."""

from __future__ import annotations

import json
import os
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from .lyrics_features import extract_features
from .references import sample_reference_comments

MODEL = os.environ.get("RAP_REACTION_MODEL", "claude-opus-5-5")


# ---------- 출력 스키마 ----------

class AudienceReaction(BaseModel):
    audience: Literal["평론가", "힙합 커뮤니티", "일반 유튜브 청자"]
    score: int = Field(description="이 청자층의 예상 호감도, 0~100")
    summary: str = Field(description="이 청자층이 보일 반응 요약, 2~3문장")
    likes: list[str] = Field(description="좋아할 지점")
    dislikes: list[str] = Field(description="아쉬워하거나 비판할 지점")


class PredictedComment(BaseModel):
    audience: Literal["평론가", "힙합 커뮤니티", "일반 유튜브 청자"]
    text: str = Field(description="실제로 달릴 법한 댓글/한줄평. 해당 청자층의 말투 그대로")
    tone: Literal["호평", "혹평", "중립", "밈"]


class LineFeedback(BaseModel):
    line: str = Field(description="해당 가사 구절 (원문 인용)")
    comment: str = Field(description="왜 좋거나 아쉬운지")
    suggestion: Optional[str] = Field(default=None, description="개선 제안, 좋은 구절이면 null")


class ReactionReport(BaseModel):
    overall_score: int = Field(description="종합 반응 잠재력, 0~100")
    reach: Literal["니치", "힙합 코어 팬층", "대중 확산 가능"]
    verdict: str = Field(description="한 줄 총평")
    audiences: list[AudienceReaction]
    predicted_comments: list[PredictedComment]
    strengths: list[str]
    weaknesses: list[str]
    highlight_lines: list[LineFeedback] = Field(description="화제가 될 만한 구절")
    weak_lines: list[LineFeedback] = Field(description="고치면 좋을 구절")
    risks: list[str] = Field(description="논란·오해 소지 (디스, 혐오 표현, 표절 의심 등). 없으면 빈 배열")
    caveats: str = Field(description="이 예측의 한계")


# ---------- 프롬프트 ----------

SYSTEM_PROMPT = """너는 한국 힙합 씬을 오래 지켜본 A&R 겸 음악 평론가다. 래퍼가 발매 전 가사를 보여주면, 이 곡이 실제로 공개됐을 때 청자들이 어떻게 반응할지 예측한다.

세 청자층을 구분해서 생각한다.
- 평론가: 웹진 리뷰어 시각. 주제 의식, 서사, 작법(라임 설계, 플로우를 짐작할 수 있는 음절 배치, 비유), 독창성, 씬에서의 위치를 본다. 진부한 자기과시나 클리셰에 엄격하다.
- 힙합 커뮤니티: 힙합 커뮤니티 이용자 시각. 펀치라인, 라임 개수, 디스·맥락, 래퍼 간 비교, "가사 미쳤다"/"이건 좀..." 식의 직설적 반응.
- 일반 유튜브 청자: 힙합을 가볍게 듣는 사람. 훅이 귀에 남는지, 따라 부를 수 있는지, 공감되는지, 밈이 될 구절이 있는지.

원칙:
- 듣기 좋은 말만 하지 않는다. 래퍼가 발매 전에 고칠 수 있게 솔직하게, 구체적인 구절을 인용하며 말한다.
- 점수는 실제 반응 분포를 생각하고 매긴다. 평범한 곡은 40~60점대다. 80점 이상은 씬에서 화제가 될 수준일 때만 준다.
- 인기는 아티스트 인지도, 마케팅, 비트, 퍼포먼스에 크게 좌우된다. 가사만 보고 판단한다는 한계를 caveats에 적는다.
- 예상 댓글은 실제 유튜브/커뮤니티 말투로 쓴다 (반말, 줄임말, ㅋㅋ, 이모지 등 자연스럽게). 참고 댓글이 주어지면 그 어투를 따른다. 참고 댓글 내용을 그대로 베끼지는 않는다.
- 예상 댓글은 청자층별로 2~4개씩, 호평과 혹평을 섞는다.
- 모든 출력은 한국어로 쓴다."""


def _build_user_message(
    lyrics: str,
    title: str | None,
    artist_level: str | None,
    vibe: str | None,
    features: dict,
    references: list[dict],
) -> str:
    parts = ["다음 곡이 공개됐을 때의 청자 반응을 예측해줘.\n"]
    meta = {
        "곡 제목": title or "(미정)",
        "아티스트 인지도": artist_level or "(미입력)",
        "곡 분위기/비트 설명": vibe or "(미입력)",
    }
    parts.append("<곡_정보>\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n</곡_정보>\n")
    parts.append(
        "<가사_정량지표>\n"
        "자동 계산된 대략적인 수치다. end_rhyme_ratio는 인접 줄 끝 음절/모음이 맞는 비율, "
        "english_ratio는 영어 글자 비율, repeated_lines는 3번 이상 반복된 줄(훅 후보)이다.\n"
        + json.dumps(features, ensure_ascii=False, indent=2)
        + "\n</가사_정량지표>\n"
    )
    if references:
        parts.append(
            "<참고_실제댓글>\n"
            "국내 힙합 음원 유튜브 영상에 실제로 달린 인기 댓글 샘플이다. 말투 참고용으로만 쓴다.\n"
            + "\n".join(f"- {r['text']}" for r in references)
            + "\n</참고_실제댓글>\n"
        )
    parts.append("<가사>\n" + lyrics.strip() + "\n</가사>")
    return "\n".join(parts)


# ---------- 실행 ----------

def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class AnalysisRefused(Exception):
    pass


def analyze(
    lyrics: str,
    title: str | None = None,
    artist_level: str | None = None,
    vibe: str | None = None,
) -> dict:
    features = extract_features(lyrics)
    references = sample_reference_comments()

    if not has_api_key():
        return {
            "mode": "demo",
            "features": features,
            "reference_count": len(references),
            "report": _demo_report(features).model_dump(),
        }

    client = anthropic.Anthropic()
    user_message = _build_user_message(lyrics, title, artist_level, vibe, features, references)

    # 스트리밍으로 받아 타임아웃을 피하고, 완성된 메시지만 사용한다.
    # fallbacks="default": 안전 분류기가 요청을 거절하면 서버가 다른 모델로 자동 재시도한다.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=ReactionReport,
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise AnalysisRefused("모델이 이 가사의 분석을 거절했습니다.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("응답이 너무 길어 중간에 잘렸습니다. 가사를 나눠서 다시 시도해 주세요.")

    report = message.parsed_output
    if report is None:
        raise RuntimeError("분석 결과를 해석하지 못했습니다.")

    return {
        "mode": "live",
        "model": message.model,
        "features": features,
        "reference_count": len(references),
        "report": report.model_dump(),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
    }


def _demo_report(features: dict) -> ReactionReport:
    """API 키 없이 화면을 확인하기 위한 고정 예시. 정량 지표만 실제 값을 반영한다."""
    rhyme = features["end_rhyme_ratio"]
    base = 45 + int(rhyme * 30) + (5 if features["repeated_lines"] else 0)
    base = max(0, min(100, base))
    return ReactionReport(
        overall_score=base,
        reach="힙합 코어 팬층",
        verdict="[데모] API 키를 설정하면 실제 분석 결과가 여기에 표시됩니다.",
        audiences=[
            AudienceReaction(
                audience="평론가", score=base - 5,
                summary="[데모] 작법과 주제 의식을 중심으로 평가합니다.",
                likes=["라임 설계"], dislikes=["주제의 진부함"],
            ),
            AudienceReaction(
                audience="힙합 커뮤니티", score=base,
                summary="[데모] 펀치라인과 라임 밀도에 반응합니다.",
                likes=["펀치라인"], dislikes=["클리셰 자기과시"],
            ),
            AudienceReaction(
                audience="일반 유튜브 청자", score=base + 5 if features["repeated_lines"] else base - 10,
                summary="[데모] 훅이 귀에 남는지가 핵심입니다.",
                likes=["따라 부르기 쉬운 훅"] if features["repeated_lines"] else [],
                dislikes=[] if features["repeated_lines"] else ["기억에 남는 훅이 없음"],
            ),
        ],
        predicted_comments=[
            PredictedComment(audience="힙합 커뮤니티", text="[데모] 라임 미쳤네 ㅋㅋ", tone="호평"),
            PredictedComment(audience="일반 유튜브 청자", text="[데모] 훅 중독성 있다", tone="호평"),
            PredictedComment(audience="평론가", text="[데모] 작법은 성실하나 새로움은 부족하다.", tone="중립"),
        ],
        strengths=[f"[데모] 끝 라임 일치율 {rhyme:.0%}"],
        weaknesses=["[데모] 실제 분석이 아닙니다"],
        highlight_lines=[],
        weak_lines=[],
        risks=[],
        caveats="데모 모드입니다. ANTHROPIC_API_KEY 환경 변수를 설정하세요.",
    )
