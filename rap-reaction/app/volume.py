"""예상 반응 규모(첫 한 달 조회수·좋아요·댓글 수)를 계산한다.

    예상 조회수 = 기준 조회수 × 성과 배수

- 기준 조회수: 래퍼가 입력한 최근 곡 평균 조회수 > 구독자 수 기반 추정 > 인지도 단계 기본값
- 성과 배수: Claude가 곡(가사·음원 구조)을 보고 판단한 "평소 대비" 배수
- 좋아요·댓글: 조회수 × 비율. 수집한 유튜브 데이터(data/videos.jsonl)가 있으면 실제 국힙 영상의
  중앙값 비율을, 없으면 기본 가정치를 쓴다.

숫자는 Claude가 지어내지 않고 여기서 공식대로 계산한다. 모든 값은 대략적인 범위다.
"""

from __future__ import annotations

import json
import statistics
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 인지도 단계별 "평소 곡 첫 한 달 조회수" 가정치 (데이터로 보정하기 전 출발점)
TIER_BASELINE = {
    "신인": 500,
    "언더그라운드": 5_000,
    "중견": 50_000,
    "메인스트림": 500_000,
}
SUBSCRIBER_TO_VIEWS = 0.10  # 구독자의 약 10%가 첫 한 달 안에 본다고 가정
DEFAULT_LIKE_RATE = 0.03    # 조회수 대비 좋아요 3%
DEFAULT_COMMENT_RATE = 0.002  # 조회수 대비 댓글 0.2%
COMMENT_INTENSITY = {"낮음": 0.6, "보통": 1.0, "높음": 1.8}
VIRAL_UPSIDE = {"낮음": 1.5, "보통": 3.0, "높음": 8.0}

MULT_MIN, MULT_MAX = 0.2, 5.0


def _tier_of(artist_level: str | None) -> str | None:
    if not artist_level:
        return None
    for tier in TIER_BASELINE:
        if artist_level.startswith(tier):
            return tier
    return None


@lru_cache(maxsize=1)
def _data_rates_cached(mtime: float) -> dict | None:
    path = DATA_DIR / "videos.jsonl"
    latest: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                v = json.loads(line)
                latest[v["video_id"]] = v  # 같은 영상이 여러 번 있으면 마지막 값
            except (ValueError, KeyError):
                continue
    vids = [v for v in latest.values() if v.get("view_count", 0) >= 1000]
    if len(vids) < 10:
        return None
    like = [v["like_count"] / v["view_count"] for v in vids if v.get("like_count")]
    comment = [v["comment_count"] / v["view_count"] for v in vids if v.get("comment_count")]
    if len(like) < 10 or len(comment) < 10:
        return None
    return {
        "like_rate": statistics.median(like),
        "comment_rate": statistics.median(comment),
        "video_count": len(vids),
    }


def data_rates() -> dict | None:
    path = DATA_DIR / "videos.jsonl"
    if not path.exists():
        return None
    return _data_rates_cached(path.stat().st_mtime)


def _nice(n: float) -> int:
    """보기 좋은 숫자로 반올림 (유효숫자 2자리)."""
    if n < 10:
        return max(0, round(n))
    digits = len(str(int(n))) - 2
    return int(round(n, -digits))


def estimate(
    multiplier: float,
    viral_potential: str,
    comment_intensity: str,
    artist_level: str | None,
    subscribers: int | None,
    avg_views: int | None,
) -> dict:
    mult = max(MULT_MIN, min(MULT_MAX, float(multiplier)))

    if avg_views:
        baseline, source = avg_views, f"입력한 최근 곡 평균 조회수 {avg_views:,}회"
    elif subscribers:
        baseline = subscribers * SUBSCRIBER_TO_VIEWS
        source = f"구독자 {subscribers:,}명 × {SUBSCRIBER_TO_VIEWS:.0%} (구독자 중 첫 달에 보는 비율 가정)"
    elif (tier := _tier_of(artist_level)):
        baseline = TIER_BASELINE[tier]
        source = f"'{tier}' 단계 기본값 {baseline:,}회 (가정치)"
    else:
        return {
            "available": False,
            "multiplier": round(mult, 2),
            "message": "아티스트 인지도, 구독자 수, 최근 곡 평균 조회수 중 하나를 넣으면 예상 조회수를 숫자로 보여드립니다.",
        }

    rates = data_rates()
    if rates:
        like_rate, comment_rate = rates["like_rate"], rates["comment_rate"]
        rate_source = f"수집한 국힙 영상 {rates['video_count']}개의 중앙값"
    else:
        like_rate, comment_rate = DEFAULT_LIKE_RATE, DEFAULT_COMMENT_RATE
        rate_source = "기본 가정치 (유튜브 데이터를 수집하면 실제 비율로 바뀝니다)"
    comment_rate *= COMMENT_INTENSITY.get(comment_intensity, 1.0)

    mid = baseline * mult
    views = {"low": mid * 0.5, "mid": mid, "high": mid * 2.0}
    viral = mid * VIRAL_UPSIDE.get(viral_potential, 3.0)

    def scale(rate: float) -> dict:
        return {k: _nice(v * rate) for k, v in views.items()}

    return {
        "available": True,
        "multiplier": round(mult, 2),
        "baseline": _nice(baseline),
        "baseline_source": source,
        "views": {k: _nice(v) for k, v in views.items()},
        "likes": scale(like_rate),
        "comments": scale(comment_rate),
        "viral_views": _nice(viral),
        "like_rate": round(like_rate, 4),
        "comment_rate": round(comment_rate, 5),
        "rate_source": rate_source,
    }
