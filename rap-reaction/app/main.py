"""웹 서버: 정적 페이지 + 분석 API.

음원은 브라우저에서 분석하고(static/audio-analyzer.js), 서버는 그 수치와 가사를 받아 Claude에 넘긴다.
그래서 음원 파일은 서버로 올라오지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import logging

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import guard
from .analyzer import AnalysisRefused, analyze, has_api_key, key_format_problem
from .references import dataset_stats

STATIC = Path(__file__).resolve().parent / "static"
TIME = r"^\d{1,2}:\d{2}$"

log = logging.getLogger("rap_reaction")

app = FastAPI(title="Rap Reaction", docs_url=None, redoc_url=None, openapi_url=None)


# ---------- 브라우저가 보내는 음원 분석 결과 (신뢰하지 않고 형식을 검증한다) ----------

class Section(BaseModel):
    start: str = Field(pattern=TIME)
    end: str = Field(pattern=TIME)
    start_seconds: float = Field(ge=0, le=3600)
    end_seconds: float = Field(ge=0, le=3600)
    label: str = Field(pattern=r"^[A-Z?]$")
    energy: float = Field(ge=0, le=1.5)


class EnergyRise(BaseModel):
    seconds: float = Field(ge=0, le=3600)
    time: str = Field(pattern=TIME)
    energy_jump: float = Field(ge=-2, le=2)


class HookCandidate(BaseModel):
    label: str = Field(pattern=r"^[A-Z?]$")
    occurrences: int = Field(ge=0, le=100)
    first_at: str = Field(pattern=TIME)
    first_at_seconds: float = Field(ge=0, le=3600)


class AudioFeatures(BaseModel):
    duration: str = Field(pattern=TIME)
    duration_seconds: float = Field(gt=0, le=3600)
    bpm: float = Field(ge=0, le=400)
    bpm_note: Optional[str] = None  # 서버에서 다시 만든다
    key: str = Field(pattern=r"^[A-G]#? (major|minor)$")
    loudness_db: Optional[float] = Field(default=None, ge=-200, le=50)
    dynamic_range_db: Optional[float] = Field(default=None, ge=0, le=200)
    bass_ratio: float = Field(ge=0, le=1)
    high_freq_ratio: float = Field(ge=0, le=1)
    brightness_hz: float = Field(ge=0, le=20000)
    percussive_ratio: float = Field(ge=0, le=1)
    onsets_per_second: float = Field(ge=0, le=100)
    intro_seconds: float = Field(ge=0, le=3600)
    energy_rises: list[EnergyRise] = Field(max_length=6)
    sections: list[Section] = Field(max_length=40)
    hook_candidate: Optional[HookCandidate] = None
    energy_curve: list[float] = Field(max_length=400)
    energy_curve_step_seconds: int = Field(ge=1, le=10)

    @field_validator("energy_curve")
    @classmethod
    def _curve_range(cls, v: list[float]) -> list[float]:
        if any(not (0 <= x <= 1.5) for x in v):
            raise ValueError("energy_curve 값은 0~1.5 사이여야 합니다")
        return v

    def to_dict(self) -> dict:
        d = self.model_dump()
        bpm = d["bpm"]
        d["bpm_note"] = (
            f"하프타임으로 느끼면 약 {bpm / 2:.0f} BPM" if bpm >= 120
            else f"더블타임으로 느끼면 약 {bpm * 2:.0f} BPM" if bpm <= 85
            else None
        )
        return d


class AnalyzeRequest(BaseModel):
    lyrics: Optional[str] = Field(default=None, max_length=20000)
    title: Optional[str] = Field(default=None, max_length=200)
    artist_level: Optional[str] = Field(default=None, max_length=50)
    vibe: Optional[str] = Field(default=None, max_length=500)
    subscribers: Optional[int] = Field(default=None, ge=0, le=1_000_000_000)
    avg_views: Optional[int] = Field(default=None, ge=0, le=10_000_000_000)
    audio: Optional[AudioFeatures] = None


# ---------- 라우트 ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    return {
        "live": has_api_key(),
        "key_problem": key_format_problem(),
        "access_required": bool(guard.ACCESS_CODE),
        "dataset": dataset_stats(),
    }


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


@app.post("/api/analyze")
async def analyze_endpoint(req: AnalyzeRequest, request: Request):
    guard.check_access(request)
    lyrics = _clean(req.lyrics)
    if not req.audio and (not lyrics or len(lyrics) < 20):
        raise HTTPException(400, "가사(20자 이상)나 음원 중 하나는 넣어 주세요.")
    guard.check_rate(request)

    try:
        async with guard.claude_slot:
            return await run_in_threadpool(
                analyze,
                lyrics,
                _clean(req.title),
                _clean(req.artist_level),
                _clean(req.vibe),
                req.audio.to_dict() if req.audio else None,
                req.subscribers or None,
                req.avg_views or None,
            )
    except AnalysisRefused as e:
        raise HTTPException(422, str(e))
    except anthropic.APIStatusError as e:
        raise HTTPException(*_explain_api_error(e))
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Claude API에 연결하지 못했습니다.")
    except RuntimeError as e:
        raise HTTPException(500, str(e))


def _explain_api_error(e: anthropic.APIStatusError) -> tuple[int, str]:
    """Claude API 오류를 사용자가 고칠 수 있는 설명으로 바꾼다. 키 값은 절대 넣지 않는다."""
    msg = (getattr(e, "message", "") or "").lower()
    log.warning("Claude API error %s (request_id=%s): %s", e.status_code, getattr(e, "request_id", None), msg[:300])
    if isinstance(e, anthropic.AuthenticationError):
        hint = key_format_problem()
        return 500, (
            "Claude API가 이 키를 거부했습니다 (401). "
            + (hint + " " if hint else "")
            + "console.anthropic.com → API Keys에서 키가 살아 있는지 확인하고, 새로 발급한 키를 "
            "Render의 Environment → ANTHROPIC_API_KEY에 따옴표·공백 없이 다시 넣어 주세요."
        )
    if isinstance(e, anthropic.PermissionDeniedError):
        return 500, "이 API 키로는 요청 권한이 없습니다 (403). 키가 속한 워크스페이스와 조직 상태를 console.anthropic.com에서 확인해 주세요."
    if isinstance(e, anthropic.NotFoundError):
        return 500, "이 계정에서 쓸 수 없는 모델입니다 (404). Render의 Environment에 RAP_REACTION_MODEL을 추가해 다른 모델 이름을 넣어 보세요."
    if isinstance(e, anthropic.RateLimitError):
        return 429, "요청이 많습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(e, anthropic.BadRequestError) and ("credit" in msg or "billing" in msg or "balance" in msg):
        return 402, "Claude API 크레딧이 부족합니다. console.anthropic.com → Billing에서 크레딧을 충전해 주세요."
    if e.status_code == 529 or e.status_code >= 500:
        return 503, "Claude API 서버가 일시적으로 바쁩니다. 잠시 후 다시 시도해 주세요."
    return 502, f"Claude API 오류 ({e.status_code})"


app.mount("/static", StaticFiles(directory=STATIC), name="static")
