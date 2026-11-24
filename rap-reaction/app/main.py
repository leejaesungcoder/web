"""웹 서버: 정적 페이지 + 분석 API."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analyzer import AnalysisRefused, analyze, has_api_key
from .references import dataset_stats

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Rap Reaction")


class AnalyzeRequest(BaseModel):
    lyrics: str = Field(min_length=20, max_length=20000)
    title: Optional[str] = Field(default=None, max_length=200)
    artist_level: Optional[str] = Field(default=None, max_length=50)
    vibe: Optional[str] = Field(default=None, max_length=500)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"live": has_api_key(), "dataset": dataset_stats()}


@app.post("/api/analyze")
async def analyze_endpoint(req: AnalyzeRequest):
    try:
        return await run_in_threadpool(
            analyze, req.lyrics, req.title, req.artist_level, req.vibe
        )
    except AnalysisRefused as e:
        raise HTTPException(status_code=422, detail=str(e))
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=500, detail="Claude API 키가 올바르지 않습니다.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="요청이 많습니다. 잠시 후 다시 시도해 주세요.")
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Claude API 오류 ({e.status_code})")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Claude API에 연결하지 못했습니다.")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
