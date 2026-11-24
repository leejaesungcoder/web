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


class KeyMoment(BaseModel):
    time: str = Field(description="m:ss 형식 타임스탬프. 음원 분석의 구간/에너지 상승 시점에서 고른다")
    what: str = Field(description="그 순간 무슨 일이 일어나는지 (훅 진입, 비트 드롭, 특정 가사 등)")
    expected_comment: str = Field(description="이 시점을 언급하는 예상 유튜브 댓글. 타임스탬프로 시작 (예: '0:44 여기서 소름')")


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
    sound_feedback: list[str] = Field(description="음원 구성(인트로 길이, 훅 타이밍, 비트, 곡 길이)이 유튜브 반응에 미칠 영향. 음원이 없으면 빈 배열")
    key_moments: list[KeyMoment] = Field(description="유튜브 타임스탬프 댓글이 몰릴 순간 2~4개. 음원이 없으면 빈 배열")
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
- 예상 댓글은 청자층별로 2~4개씩, 호평과 혹평을 섞는다. 일반 유튜브 청자 댓글은 4~6개로 더 많이 쓴다.
- 모든 출력은 한국어로 쓴다.

음원 분석 결과가 주어지면:
- 너는 소리를 직접 듣지 못한다. 신호 처리로 뽑은 수치(BPM, 조성, 저음 비중, 에너지 흐름, 구간 구조)만 보고 추론한다. 보컬 톤, 발음, 믹스 완성도, 플로우의 맛은 판단할 수 없다고 caveats에 적는다.
- 유튜브 반응에 영향을 주는 구조를 본다: 인트로가 길면(15초 이상) 초반 이탈과 "인트로 스킵" 댓글이 늘어난다. 첫 훅이 늦게(1분 이후) 나오면 일반 청자를 잃는다. 훅이 반복되는 횟수와 에너지 차이는 중독성과 관련 있다. 곡 길이가 짧으면(2분대) 반복 재생에 유리하다.
- 저음 비중이 높고 BPM이 130~160(하프타임 65~80)이면 트랩 계열, 85~100이면서 타악기 비중이 높으면 붐뱁 계열일 가능성이 높다. 단정하지 말고 "~로 보인다"고 말한다. 사용자가 적은 비트 설명이 있으면 그쪽을 우선한다.
- 유튜브에는 "1:23 여기 미쳤다" 같은 타임스탬프 댓글이 많다. key_moments는 반드시 음원 분석에 나온 실제 시점(구간 시작, 에너지 상승, 훅 후보)에서 고른다. 없는 시점을 지어내지 않는다.
"""


def _audio_summary(audio: dict) -> dict:
    """Claude에게 넘길 음원 정보. 화면용 에너지 곡선은 2초 단위 평균으로 줄여서 넘긴다."""
    curve = audio["energy_curve"]
    step = audio["energy_curve_step_seconds"]
    # 10초 단위로 줄인 에너지 흐름 (0~1)
    group = max(1, 10 // step)
    coarse = [
        {"t": f"{(i * step) // 60}:{(i * step) % 60:02d}", "energy": round(sum(curve[i:i + group]) / len(curve[i:i + group]), 2)}
        for i in range(0, len(curve), group)
    ]
    keys = [
        "duration", "bpm", "bpm_note", "key", "loudness_db", "dynamic_range_db",
        "bass_ratio", "high_freq_ratio", "brightness_hz", "percussive_ratio",
        "onsets_per_second", "intro_seconds", "energy_rises", "hook_candidate",
    ]
    out = {k: audio[k] for k in keys}
    out["sections"] = [
        {k: s[k] for k in ("start", "end", "label", "energy")} for s in audio["sections"]
    ]
    out["energy_flow_10s"] = coarse
    return out


AUDIO_GUIDE = (
    "신호 처리로 자동 추정한 값이다. "
    "bass_ratio는 150Hz 미만 에너지 비중(0.4 이상이면 저음이 강한 편), "
    "high_freq_ratio는 5kHz 이상 비중(하이햇·치찰음), percussive_ratio는 타악기 성분 비중, "
    "intro_seconds는 비트가 본격적으로 들어오는 시점(초), "
    "energy_rises는 에너지가 크게 올라가는 순간(드롭·훅 진입 후보), "
    "sections의 label이 같으면 비슷한 소리가 반복되는 구간이고, "
    "hook_candidate는 가장 많이 반복되며 에너지가 높은 구간이다. "
    "energy는 곡 안에서의 상대값(0~1)이다."
)


def _build_user_message(
    lyrics: str | None,
    title: str | None,
    artist_level: str | None,
    vibe: str | None,
    features: dict | None,
    references: list[dict],
    audio: dict | None = None,
) -> str:
    parts = ["다음 곡이 공개됐을 때의 청자 반응, 특히 유튜브 댓글 반응을 예측해줘.\n"]
    meta = {
        "곡 제목": title or "(미정)",
        "아티스트 인지도": artist_level or "(미입력)",
        "곡 분위기/비트 설명": vibe or "(미입력)",
    }
    parts.append("<곡_정보>\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n</곡_정보>\n")
    if audio:
        parts.append(
            "<음원_분석>\n" + AUDIO_GUIDE + "\n"
            + json.dumps(_audio_summary(audio), ensure_ascii=False, indent=2)
            + "\n</음원_분석>\n"
        )
    if features:
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
    if lyrics:
        parts.append("<가사>\n" + lyrics.strip() + "\n</가사>")
    else:
        parts.append(
            "<가사>\n(가사 없음. 음원 구조만으로 판단하고, 가사 관련 항목은 비워두거나 "
            "'가사를 함께 넣으면 분석할 수 있음'이라고 적는다.)\n</가사>"
        )
    return "\n".join(parts)


# ---------- 실행 ----------

def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class AnalysisRefused(Exception):
    pass


def analyze(
    lyrics: str | None,
    title: str | None = None,
    artist_level: str | None = None,
    vibe: str | None = None,
    audio: dict | None = None,
) -> dict:
    if not lyrics and not audio:
        raise ValueError("가사나 음원 중 하나는 있어야 합니다.")

    features = extract_features(lyrics) if lyrics else None
    references = sample_reference_comments()
    common = {
        "features": features,
        "audio": audio,
        "reference_count": len(references),
    }

    if not has_api_key():
        return {"mode": "demo", **common, "report": _demo_report(features, audio).model_dump()}

    client = anthropic.Anthropic()
    user_message = _build_user_message(
        lyrics, title, artist_level, vibe, features, references, audio
    )

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
        **common,
        "report": report.model_dump(),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
    }


def _demo_report(features: dict | None, audio: dict | None = None) -> ReactionReport:
    """API 키 없이 화면을 확인하기 위한 고정 예시. 정량 지표와 음원 구조만 실제 값을 반영한다."""
    rhyme = features["end_rhyme_ratio"] if features else 0.0
    has_hook = bool(features and features["repeated_lines"]) or bool(audio and audio["hook_candidate"])
    base = 45 + int(rhyme * 30) + (5 if has_hook else 0)
    base = max(0, min(100, base))

    sound_feedback: list[str] = []
    key_moments: list[KeyMoment] = []
    if audio:
        sound_feedback.append(f"[데모] 비트가 {audio['intro_seconds']:.0f}초에 들어옵니다.")
        hook = audio["hook_candidate"]
        if hook:
            sound_feedback.append(f"[데모] 훅 후보가 {hook['first_at']}에 처음 나오고 {hook['occurrences']}번 반복됩니다.")
        for r in audio["energy_rises"][:3]:
            key_moments.append(KeyMoment(
                time=r["time"], what="[데모] 에너지가 크게 올라가는 구간",
                expected_comment=f"{r['time']} [데모] 여기서부터 분위기 바뀜",
            ))

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
                audience="일반 유튜브 청자", score=base + 5 if has_hook else base - 10,
                summary="[데모] 훅이 귀에 남는지가 핵심입니다.",
                likes=["따라 부르기 쉬운 훅"] if has_hook else [],
                dislikes=[] if has_hook else ["기억에 남는 훅이 없음"],
            ),
        ],
        predicted_comments=[
            PredictedComment(audience="힙합 커뮤니티", text="[데모] 라임 미쳤네 ㅋㅋ", tone="호평"),
            PredictedComment(audience="일반 유튜브 청자", text="[데모] 훅 중독성 있다", tone="호평"),
            PredictedComment(audience="평론가", text="[데모] 작법은 성실하나 새로움은 부족하다.", tone="중립"),
        ],
        strengths=[f"[데모] 끝 라임 일치율 {rhyme:.0%}"] if features else [],
        weaknesses=["[데모] 실제 분석이 아닙니다"],
        highlight_lines=[],
        weak_lines=[],
        risks=[],
        sound_feedback=sound_feedback,
        key_moments=key_moments,
        caveats="데모 모드입니다. ANTHROPIC_API_KEY 환경 변수를 설정하세요.",
    )
