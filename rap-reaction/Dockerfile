FROM python:3.13-slim

# mp3/m4a 디코딩에 ffmpeg가 필요하다
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces는 uid 1000 사용자로 컨테이너를 실행한다
RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/app/.numba_cache

RUN mkdir -p data .numba_cache && chown -R user /app
USER user

# 음원 분석 코드를 미리 컴파일해 둔다 (첫 요청 지연 방지)
RUN python -m app.warmup

EXPOSE 7860
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
