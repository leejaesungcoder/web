"""음원에서 가사를 받아쓴다 (선택 기능).

faster-whisper가 설치돼 있을 때만 동작한다:
    pip install faster-whisper
처음 실행할 때 Hugging Face에서 모델을 내려받는다 (small 약 500MB).
랩은 빠르고 비트가 깔려 있어 받아쓰기 정확도가 낮다. 가사를 직접 넣는 쪽이 훨씬 정확하다.
"""

from __future__ import annotations

import os
from functools import lru_cache

from .audio_features import mmss

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel(MODEL_SIZE, device="auto", compute_type="int8")


def transcribe(path: str) -> list[dict]:
    """[{"time": "0:12", "seconds": 12.3, "text": "..."}] 형태로 돌려준다."""
    segments, _info = _model().transcribe(path, language="ko", vad_filter=True)
    return [
        {"time": mmss(s.start), "seconds": round(s.start, 1), "text": s.text.strip()}
        for s in segments
        if s.text.strip()
    ]
