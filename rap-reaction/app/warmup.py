"""Docker 이미지를 만들 때 음원 분석을 한 번 돌려 numba 컴파일 결과를 캐시에 남긴다.

이렇게 하면 배포 후 첫 요청이 20초 넘게 걸리는 일을 막을 수 있다.
    python -m app.warmup
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from .audio_features import analyze_audio


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        # 10초짜리 테스트 음 (사인파 + 잡음)
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=110:duration=10",
                "-f", "lavfi", "-i", "anoisesrc=duration=10:amplitude=0.05",
                "-filter_complex", "amix=inputs=2", path,
            ],
            check=True,
        )
        result = analyze_audio(path)
        print(f"warmup ok: {result['duration']}, {result['bpm']} BPM")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
