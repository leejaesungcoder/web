"""웹 서버: 정적 페이지 + 분석 API."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import guard, transcribe
from .analyzer import AnalysisRefused, analyze, has_api_key
from .audio_features import analyze_audio
from .references import dataset_stats

STATIC = Path(__file__).resolve().parent / "static"
MAX_AUDIO_BYTES = 40 * 1024 * 1024
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"}

app = FastAPI(title="Rap Reaction", docs_url=None, redoc_url=None, openapi_url=None)


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
        "transcription": transcribe.available(),
        "dataset": dataset_stats(),
    }


async def _save_upload(upload: UploadFile) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"지원하지 않는 파일 형식입니다. ({', '.join(sorted(AUDIO_EXTS))})")
    fd, path = tempfile.mkstemp(suffix=ext)
    size = 0
    with os.fdopen(fd, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                f.close()
                os.unlink(path)
                raise HTTPException(413, "음원 파일은 40MB까지 올릴 수 있습니다.")
            f.write(chunk)
    return path


def _clean(value: Optional[str], limit: int) -> Optional[str]:
    value = (value or "").strip()
    return value[:limit] or None


@app.post("/api/analyze")
async def analyze_endpoint(
    request: Request,
    lyrics: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    artist_level: Optional[str] = Form(None),
    vibe: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
):
    guard.check_access(request)
    lyrics = _clean(lyrics, 20000)
    has_audio = audio is not None and bool(audio.filename)
    if not has_audio and (not lyrics or len(lyrics) < 20):
        raise HTTPException(400, "가사(20자 이상)나 음원 중 하나는 넣어 주세요.")
    guard.check_rate(request)

    audio_info = None
    transcript = None
    if has_audio:
        path = await _save_upload(audio)
        try:
            try:
                async with guard.audio_slot:
                    audio_info = await run_in_threadpool(analyze_audio, path)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception:
                raise HTTPException(400, "음원을 읽지 못했습니다. 파일이 손상되지 않았는지 확인해 주세요.")
            if not lyrics and transcribe.available():
                try:
                    async with guard.audio_slot:
                        transcript = await run_in_threadpool(transcribe.transcribe, path)
                except Exception:
                    transcript = None  # 받아쓰기 실패해도 음원 구조만으로 분석을 계속한다
        finally:
            os.unlink(path)

    try:
        async with guard.claude_slot:
            return await run_in_threadpool(
                analyze,
                lyrics,
                _clean(title, 200),
                _clean(artist_level, 50),
                _clean(vibe, 500),
                audio_info,
                transcript,
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
