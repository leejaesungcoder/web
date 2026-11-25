"""Claude로 가사에 대한 청자 반응을 예측한다."""

from __future__ import annotations

import json
import os
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from .lyrics_features import extract_features
from .references import sample_reference_comments
from .volume import estimate as estimate_volume

MODEL = os.environ.get("RAP_REACTION_MODEL", "claude-opus-5-5")


# ---------- 출력 스키마 ----------

class AudienceReaction(BaseModel):
    audience: Literal["평론가", "힙합 커뮤니티", "국힙 리스너"]
    score: int = Field(description="이 청자층이 실제로 줄 평가, 0~100. 시스템 지시의 점수 기준표를 따른다")
    summary: str = Field(description="이 청자층이 보일 반응 요약, 2~3문장")
    likes: list[str] = Field(description="좋아할 지점")
    dislikes: list[str] = Field(description="아쉬워하거나 비판할 지점")


class PredictedComment(BaseModel):
    audience: Literal["평론가", "힙합 커뮤니티", "국힙 리스너"]
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


class ReactionVolume(BaseModel):
    performance_multiplier: float = Field(
        description="이 아티스트의 평소 곡 대비 예상 성과 배수. 1.0 = 평소 수준, 0.5 = 절반, 2.0 = 두 배. 0.2~5 사이"
    )
    viral_potential: Literal["낮음", "보통", "높음"] = Field(description="평소 팬층을 넘어 퍼질 가능성 (밈, 챌린지, 알고리즘 추천)")
    comment_intensity: Literal["낮음", "보통", "높음"] = Field(
        description="조회수 대비 댓글이 얼마나 많이 달릴 곡인지. 논쟁적·공감형·타임스탬프 포인트가 많으면 높음"
    )
    up_factors: list[str] = Field(description="성과를 끌어올릴 요인 (구체적으로)")
    down_factors: list[str] = Field(description="성과를 깎아먹을 요인 (구체적으로)")


class StyleRead(BaseModel):
    style: str = Field(description="이 곡의 스타일을 짧게 (예: 레이드백 스웩 트랩, 리리시즘 붐뱁, 멜로디 랩, 드릴, 감성 서사형)")
    what_fans_value: list[str] = Field(description="이 스타일을 좋아하는 리스너가 중요하게 보는 것 3~5개")
    delivery_dependence: Literal["낮음", "보통", "높음"] = Field(
        description="이 곡의 평가가 가사 내용보다 플로우·톤·딜리버리·그루브(소리로만 판단 가능한 것)에 얼마나 달려 있는지"
    )
    unheard_note: str = Field(description="소리를 듣지 못해 판단하지 못한 부분과, 그 부분에 따라 점수가 어떻게 달라질 수 있는지 한두 문장")


class ReactionReport(BaseModel):
    style_read: StyleRead = Field(description="채점 전에 먼저 판단하는 곡의 스타일과 기준")
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
    volume: ReactionVolume = Field(description="반응 규모 판단. 실제 조회수 숫자는 서버가 이 배수로 계산한다")
    caveats: str = Field(description="이 예측의 한계")


# ---------- 프롬프트 ----------

SYSTEM_PROMPT = """너는 한국 힙합 씬을 오래 지켜본 A&R 겸 음악 평론가다. 래퍼가 발매 전 가사를 보여주면, 이 곡이 실제로 공개됐을 때 청자들이 어떻게 반응할지 예측한다.

세 청자층을 구분해서 생각한다. 세 층 모두 힙합을 좋아해서 이 곡을 찾아 들은 사람들이다. 힙합을 모르는 사람의 교양 있는 감상은 예측하지 않는다.
- 평론가: 웹진 리뷰어 시각. 주제 의식, 서사, 작법(라임 설계, 플로우를 짐작할 수 있는 음절 배치, 비유), 독창성, 씬에서의 위치를 본다. 진부한 자기과시나 클리셰에 엄격하다.
- 힙합 커뮤니티: 힙합 커뮤니티에 상주하는 매니아층. 디스코그래피와 씬의 맥락을 꿰고 있다. 라임, 펀치라인, 랩 테크닉, 스타일의 독창성을 따지고 다른 래퍼와 비교한다. 기준이 높고 직설적이라 아쉬우면 바로 까지만, 잘한 건 확실하게 인정하고 "명곡", "클래식"이라는 말도 아끼지 않는다.
- 국힙 리스너: 국힙을 꾸준히 듣는 유튜브·스트리밍 리스너. 쇼미더머니를 챙겨 보고, 좋아하는 래퍼 신곡을 바로 찾아 듣고, 플레이리스트에 넣을지를 몇 초 만에 정한다. 분석은 안 하지만 귀는 높다. 플로우가 타는지, 비트가 좋은지, 훅이 꽂히는지, 바이브와 래퍼 캐릭터(스웩, 진정성)가 사는지에 바로 반응하고, 이전 곡이나 비슷한 스타일의 래퍼와 자연스럽게 비교한다.

채점 순서:
1. style_read를 먼저 정한다. 이 곡이 어떤 스타일인지, 그 스타일을 좋아하는 리스너가 무엇을 중요하게 보는지 적는다.
2. 각 청자층 점수는 그 스타일의 문법 안에서 매긴다. 스웩·플렉스 트랩을 붐뱁 리리시즘 기준(주제 의식, 서사, 메시지)으로 깎지 않는다. 반복되는 짧은 훅, 단순한 단어, 영어 섞기, 자기과시는 국힙의 흔한 문법이고, 그 자체로는 단점이 아니다. 단점은 그 스타일 안에서 못했을 때만 쓴다.
3. 소리로만 판단할 수 있는 것(플로우, 톤, 딜리버리, 그루브, 발음 리듬, 보컬 질감)은 감점 근거로 쓰지 않는다. 가사가 종이 위에서 단순해 보여도 딜리버리 중심 곡일 수 있다. 이런 곡은 delivery_dependence를 "높음"으로 하고, 사용자가 적은 랩 스타일 설명과 음원 수치를 근거로 판단하며, 확인이 안 되는 부분은 unheard_note에 적는다.
4. 점수 기준표 (각 청자층이 실제로 보일 반응):
   - 90~100: 그 청자층에서 시대의 클래식으로 회자되는 수준
   - 80~89: 명곡이라는 평이 압도적. 플레이리스트 고정, 몇 년 뒤에도 언급됨
   - 70~79: 호평 우세. 확실히 잘 만든 곡이라는 반응
   - 60~69: 괜찮다는 반응이 많지만 호불호가 있음
   - 50~59: 무난함. 금방 잊힘
   - 40~49: 아쉽다는 반응이 우세
   - 40 미만: 혹평
   평론가와 매니아, 리스너의 점수는 크게 다를 수 있다. 평론가가 낮게 줘도 리스너는 열광하는 곡이 흔하다.
5. 종합 점수는 서버가 국힙 리스너 50%, 힙합 커뮤니티 30%, 평론가 20%로 계산한다. 종합 점수를 직접 쓰지 않는다.

원칙:
- 솔직하게 말한다는 건 깎는다는 뜻이 아니다. 잘한 곡은 높게, 못한 곡은 낮게 준다. 래퍼가 고칠 수 있게 약점은 구체적인 구절을 인용해 말한다.
- 인기는 아티스트 인지도, 마케팅, 비트, 퍼포먼스에도 좌우된다. 판단 근거의 한계를 caveats에 적는다.
- 예상 댓글은 청자층별로 2~4개씩, 호평과 혹평을 섞는다. 국힙 리스너 댓글은 4~6개로 더 많이 쓴다.
- 참고 댓글이 주어지면 그 어투를 따른다. 참고 댓글 내용을 그대로 베끼지는 않는다.

예상 댓글 말투 (힙합 커뮤니티, 국힙 리스너):
- 실제로 국힙 영상에 달리는 댓글처럼 쓴다. 대부분 한두 마디(5~40자)이고 반말이다. ㅋㅋ, ㄹㅇ, ㅈㄴ, 개-, -각, 미쳤다 같은 표현과 이모지를 자연스럽게 섞되 억지로 넣지 않는다.
- 감상문처럼 쓰지 않는다. "~가 인상적이다", "~점이 아쉽다", "주제 의식", "서사", "메시지가 돋보인다", "공감이 된다" 같은 말투와 장단점을 나란히 정리하는 문장은 금지다. 그런 말투는 평론가에게만 쓴다.
- 힙합 리스너가 실제로 쓰는 반응 방식을 쓴다: 특정 구절 인용하며 감탄, 플로우·비트·훅에 대한 즉각적 반응, 이전 곡과 비교("전작보다 늘었다"), 스타일이 비슷한 래퍼와 비교, 쇼미·피처링·차트 드립, 타임스탬프, 짧은 혹평("훅이 너무 약함", "비트만 좋네").
- 실존 래퍼와 비교는 해도 되지만, 디스 이력이나 표절 같은 사실 관계는 지어내지 않는다.
- 좋은 예: "2절 펀치라인 듣고 소리 지름ㅋㅋ", "비트 누가 찍었냐 개좋네", "훅은 좋은데 벌스가 너무 무난함", "이 형 전작보다 랩 확실히 늘었다", "0:44 여기서 플리 확정", "가사 뭔 소린지 하나도 안 들림", "쇼미 나가면 본선은 간다"
- 나쁜 예 (쓰지 말 것): "가사에서 느껴지는 진정성이 인상적입니다", "전체적으로 무난하지만 개성이 부족한 것 같아요", "희망적인 메시지가 공감됩니다"
- audiences의 likes와 dislikes도 각 청자층이 실제로 쓰는 단어로 적는다 (국힙 리스너: "훅 중독성", "플로우가 안 탐", "비트 바이브" 등).
- 모든 출력은 한국어로 쓴다.

음원 분석 결과가 주어지면:
- 너는 소리를 직접 듣지 못한다. 신호 처리로 뽑은 수치(BPM, 조성, 저음 비중, 에너지 흐름, 구간 구조)만 보고 추론한다. 보컬 톤, 발음, 믹스 완성도, 플로우의 맛은 판단할 수 없으므로 감점하지 말고 unheard_note에 적는다.
- 유튜브 반응에 영향을 주는 구조를 본다: 인트로가 길면(15초 이상) 초반 이탈과 "인트로 스킵" 댓글이 늘어난다. 첫 훅이 늦게(1분 이후) 나오면 스킵하고 넘어가는 리스너가 늘어난다. 훅이 반복되는 횟수와 에너지 차이는 중독성과 관련 있다. 곡 길이가 짧으면(2분대) 반복 재생에 유리하다.
- 저음 비중이 높고 BPM이 130~160(하프타임 65~80)이면 트랩 계열, 85~100이면서 타악기 비중이 높으면 붐뱁 계열일 가능성이 높다. 단정하지 말고 "~로 보인다"고 말한다. 사용자가 적은 비트 설명이 있으면 그쪽을 우선한다.
- 유튜브에는 "1:23 여기 미쳤다" 같은 타임스탬프 댓글이 많다. key_moments는 반드시 음원 분석에 나온 실제 시점(구간 시작, 에너지 상승, 훅 후보)에서 고른다. 없는 시점을 지어내지 않는다.

반응 규모(volume) 판단:
- 조회수의 대부분은 곡이 아니라 아티스트의 기존 팬층이 정한다. 그래서 절대 숫자가 아니라 "이 아티스트가 평소에 내는 곡 대비 몇 배"를 판단한다. 숫자는 서버가 계산하니 조회수를 직접 쓰지 않는다.
- performance_multiplier는 대부분의 곡이 0.7~1.3이다. 평소보다 확실히 나은 훅·주제·구성이면 1.3~2.0, 2.0을 넘기는 건 밈이 될 구절, 따라 부르기 쉬운 짧은 훅, 화제성 있는 주제(디스, 사회적 이슈, 강한 공감) 같은 뚜렷한 확산 요인이 있을 때만이다. 완성도가 떨어지거나 인트로가 길고 훅이 늦으면 1.0 아래로 준다.
- 국힙 리스너 점수와 일관되게 판단한다. 대략 리스너 점수 40 ≈ 0.6배, 50 ≈ 0.9배, 60 ≈ 1.1배, 70 ≈ 1.5배, 80 이상 ≈ 2배 이상을 출발점으로 삼고 근거에 따라 조정한다.
- up_factors와 down_factors에는 왜 그 배수인지 구체적인 근거(구절, 구간 시점, 곡 길이 등)를 적는다.
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
    subscribers: int | None = None,
    avg_views: int | None = None,
    delivery: str | None = None,
) -> str:
    parts = ["다음 곡이 공개됐을 때의 청자 반응, 특히 유튜브 댓글 반응을 예측해줘.\n"]
    meta = {
        "곡 제목": title or "(미정)",
        "아티스트 인지도": artist_level or "(미입력)",
        "유튜브 구독자 수": f"{subscribers:,}명" if subscribers else "(미입력)",
        "최근 곡 첫 한 달 평균 조회수": f"{avg_views:,}회" if avg_views else "(미입력)",
        "곡 분위기/비트 설명": vibe or "(미입력)",
        "랩 스타일·딜리버리 설명 (아티스트 본인 설명)": delivery or "(미입력)",
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

def _api_key() -> str | None:
    """환경 변수의 키. 복사할 때 딸려 온 공백·따옴표·줄바꿈은 지운다."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key is None:
        return None
    return key.strip().strip('"').strip("'").strip() or None


AUDIENCE_WEIGHTS = {"국힙 리스너": 0.5, "힙합 커뮤니티": 0.3, "평론가": 0.2}


def overall_score(report: "ReactionReport") -> int:
    """청자층 점수의 가중 평균. 빠진 청자층이 있으면 있는 것끼리 다시 나눈다."""
    total = weight = 0.0
    for a in report.audiences:
        w = AUDIENCE_WEIGHTS.get(a.audience, 0)
        total += w * max(0, min(100, a.score))
        weight += w
    return round(total / weight) if weight else 0


def report_dict(report: "ReactionReport") -> dict:
    d = report.model_dump()
    d["overall_score"] = overall_score(report)
    d["score_weights"] = AUDIENCE_WEIGHTS
    return d


def has_api_key() -> bool:
    return bool(_api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def key_format_problem() -> str | None:
    """키 값은 드러내지 않고 형식만 검사해서 흔한 실수를 알려준다."""
    raw = os.environ.get("ANTHROPIC_API_KEY")
    key = _api_key()
    if not key:
        return None
    if not key.startswith("sk-ant-"):
        return "API 키는 'sk-ant-'로 시작해야 합니다. console.anthropic.com에서 발급한 API 키인지 확인해 주세요."
    if key.startswith("sk-ant-admin"):
        return "관리자(Admin) 키가 들어가 있습니다. 일반 API 키를 넣어 주세요."
    if not key.startswith("sk-ant-api"):
        return (
            "Claude API 키가 아닌 다른 종류의 키가 들어가 있습니다. API 키는 'sk-ant-api03-'로 시작합니다. "
            "console.anthropic.com → API Keys → Create Key로 새로 발급해 주세요."
        )
    if any(c.isspace() for c in key):
        return "API 키 중간에 공백이나 줄바꿈이 들어가 있습니다. 키를 다시 복사해 넣어 주세요."
    if len(key) < 60:
        return "API 키가 너무 짧습니다. 일부만 복사된 것 같습니다."
    if raw != key:
        return None  # 앞뒤 공백·따옴표는 자동으로 지우고 쓴다
    return None


class AnalysisRefused(Exception):
    pass


def analyze(
    lyrics: str | None,
    title: str | None = None,
    artist_level: str | None = None,
    vibe: str | None = None,
    audio: dict | None = None,
    subscribers: int | None = None,
    avg_views: int | None = None,
    delivery: str | None = None,
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

    def with_volume(report: "ReactionReport") -> dict:
        v = report.volume
        return estimate_volume(
            v.performance_multiplier, v.viral_potential, v.comment_intensity,
            artist_level, subscribers, avg_views,
        )

    if not has_api_key():
        report = _demo_report(features, audio)
        return {"mode": "demo", **common, "report": report_dict(report), "volume_estimate": with_volume(report)}

    key = _api_key()
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    user_message = _build_user_message(
        lyrics, title, artist_level, vibe, features, references, audio, subscribers, avg_views, delivery
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
        "report": report_dict(report),
        "volume_estimate": with_volume(report),
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
        style_read=StyleRead(
            style="[데모] 스타일 판단",
            what_fans_value=["[데모] 플로우", "[데모] 훅 중독성"],
            delivery_dependence="보통",
            unheard_note="[데모] 실제 분석에서는 소리로만 판단할 수 있는 부분이 여기에 적힙니다.",
        ),
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
                audience="국힙 리스너", score=base + 5 if has_hook else base - 10,
                summary="[데모] 훅이 귀에 남는지가 핵심입니다.",
                likes=["따라 부르기 쉬운 훅"] if has_hook else [],
                dislikes=[] if has_hook else ["기억에 남는 훅이 없음"],
            ),
        ],
        predicted_comments=[
            PredictedComment(audience="힙합 커뮤니티", text="[데모] 라임 미쳤네 ㅋㅋ", tone="호평"),
            PredictedComment(audience="국힙 리스너", text="[데모] 훅 개중독됨 플리 확정", tone="호평"),
            PredictedComment(audience="평론가", text="[데모] 작법은 성실하나 새로움은 부족하다.", tone="중립"),
        ],
        strengths=[f"[데모] 끝 라임 일치율 {rhyme:.0%}"] if features else [],
        weaknesses=["[데모] 실제 분석이 아닙니다"],
        highlight_lines=[],
        weak_lines=[],
        risks=[],
        sound_feedback=sound_feedback,
        key_moments=key_moments,
        volume=ReactionVolume(
            performance_multiplier=round(0.6 + max(0, base - 40) * 0.03, 2),
            viral_potential="보통" if has_hook else "낮음",
            comment_intensity="보통",
            up_factors=["[데모] 반복되는 훅"] if has_hook else [],
            down_factors=["[데모] 실제 분석이 아닙니다"],
        ),
        caveats="데모 모드입니다. ANTHROPIC_API_KEY 환경 변수를 설정하세요.",
    )
