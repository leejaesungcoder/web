"""공개 배포용 보호 장치: 접속 코드, 요청 횟수 제한, 동시 실행 제한.

주소가 공개되면 누구나 분석을 돌릴 수 있고, 그 비용은 서버의 Claude API 키로 나간다.
환경 변수로 조절한다:
    APP_ACCESS_CODE       설정하면 이 코드를 아는 사람만 분석 가능 (베타 테스트용)
    RATE_LIMIT_PER_HOUR   IP 하나당 시간당 분석 횟수 (기본 10)
    DAILY_LIMIT           서버 전체 하루 분석 횟수 (기본 200)
"""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

ACCESS_CODE = os.environ.get("APP_ACCESS_CODE", "").strip()
PER_HOUR = int(os.environ.get("RATE_LIMIT_PER_HOUR", "10"))
DAILY = int(os.environ.get("DAILY_LIMIT", "200"))

# 음원 분석은 메모리를 많이 써서 한 번에 하나씩만 돌린다.
audio_slot = asyncio.Semaphore(1)
# Claude 호출은 동시에 몇 개까지만.
claude_slot = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENT_ANALYSES", "4")))

_hits: dict[str, deque[float]] = defaultdict(deque)
_day = {"date": "", "count": 0}


def client_ip(request: Request) -> str:
    # 호스팅 프록시 뒤에 있을 때는 X-Forwarded-For의 첫 번째 값이 실제 접속자다.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_access(request: Request) -> None:
    if not ACCESS_CODE:
        return
    given = request.headers.get("x-access-code", "")
    if not hmac.compare_digest(given.encode(), ACCESS_CODE.encode()):
        raise HTTPException(401, "접속 코드가 올바르지 않습니다.")


def check_rate(request: Request) -> None:
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _day["date"] != today:
        _day["date"], _day["count"] = today, 0
    if _day["count"] >= DAILY:
        raise HTTPException(429, "오늘 분석 가능한 횟수를 모두 사용했습니다. 내일 다시 시도해 주세요.")

    q = _hits[client_ip(request)]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= PER_HOUR:
        wait = int(3600 - (now - q[0])) // 60 + 1
        raise HTTPException(429, f"한 시간에 {PER_HOUR}번까지 분석할 수 있습니다. {wait}분 뒤에 다시 시도해 주세요.")
    q.append(now)
    _day["count"] += 1

    if len(_hits) > 10000:  # 오래된 IP 기록 정리
        for ip in [ip for ip, d in _hits.items() if not d or now - d[-1] > 3600]:
            del _hits[ip]
