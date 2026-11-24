"""웹 서버: 정적 페이지 + 분석 API.

음원은 브라우저에서 분석하고(static/audio-analyzer.js), 서버는 그 수치와 가사를 받아 Claude에 넘긴다.
그래서 음원 파일은 서버로 올라오지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import guard
from .analyzer import AnalysisRefused, analyze, has_api_key
from .references import dataset_stats

STATIC = Path(__file__).resolve().parent / "static"
TIME = r"^\d{1,2}:\d{2}$"

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
            )
    except AnalysisRefused as e:
        raise HTTPException(422, str(e))
    except anthropic.AuthenticationError:
        raise HTTPException(500, "Claude API 키가 올바르지 않습니다.")
    except anthropic.RateLimitError:
        raise HTTPException(429, "요청이 많습니다. 잠시 후 다시 시도해 주세요.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API 오류 ({e.status_code})")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Claude API에 연결하지 못했습니다.")
    except RuntimeError as e:
        raise HTTPException(500, str(e))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
