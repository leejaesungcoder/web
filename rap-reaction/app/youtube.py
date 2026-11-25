"""유튜브 링크로 영상 정보, 채널 정보, 실제 댓글을 가져온다 (YouTube Data API v3).

음원은 가져오지 않는다 (유튜브 약관상 다운로드 금지). 필요한 환경 변수: YOUTUBE_API_KEY
쿼터: 링크 하나에 약 5유닛 (하루 무료 10,000유닛).
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = os.environ.get("YOUTUBE_API_BASE", "https://www.googleapis.com/youtube/v3")
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")
CACHE_SECONDS = 3600
MAX_COMMENTS = 100

_cache: dict[str, tuple[float, dict]] = {}


class YouTubeError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류."""


def available() -> bool:
    return bool(_key())


def _key() -> str:
    return os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'")


def parse_video_id(value: str) -> str:
    value = (value or "").strip()
    m = VIDEO_ID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    raise YouTubeError("유튜브 영상 링크를 알아보지 못했습니다. youtube.com/watch?v=… 또는 youtu.be/… 형식인지 확인해 주세요.")


def _get(endpoint: str, params: dict) -> dict:
    url = f"{API}/{endpoint}?{urllib.parse.urlencode({**params, 'key': _key()})}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        reason = ""
        try:
            reason = json.loads(e.read().decode("utf-8", "replace"))["error"]["errors"][0]["reason"]
        except (KeyError, IndexError, ValueError):
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise YouTubeError("오늘의 유튜브 API 사용량을 다 썼습니다. 내일 다시 시도해 주세요.") from e
        if reason == "commentsDisabled":
            raise
        if e.code in (400, 403) and reason in ("keyInvalid", "badRequest", "forbidden", "accessNotConfigured", ""):
            raise YouTubeError("유튜브 API 키가 올바르지 않거나 YouTube Data API v3가 꺼져 있습니다.") from e
        raise YouTubeError(f"유튜브 API 오류 ({e.code} {reason})") from e
    except urllib.error.URLError as e:
        raise YouTubeError("유튜브 API에 연결하지 못했습니다.") from e


def _int(d: dict, k: str) -> int | None:
    v = d.get(k)
    return int(v) if v is not None else None


def _channel(channel_id: str, exclude_video: str) -> dict:
    data = _get("channels", {"part": "statistics,contentDetails", "id": channel_id})
    items = data.get("items", [])
    if not items:
        return {}
    ch = items[0]
    stats = ch.get("statistics", {})
    subs = None if stats.get("hiddenSubscriberCount") else _int(stats, "subscriberCount")
    uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    avg = None
    recent = 0
    if uploads:
        pl = _get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": 15})
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", []) if i["contentDetails"]["videoId"] != exclude_video]
        if ids:
            vids = _get("videos", {"part": "statistics", "id": ",".join(ids[:15])})
            views = [int(v["statistics"]["viewCount"]) for v in vids.get("items", []) if "viewCount" in v.get("statistics", {})]
            if views:
                avg = int(statistics.median(views))
                recent = len(views)
    return {"subscribers": subs, "channel_median_views": avg, "channel_recent_count": recent}


def _comments(video_id: str) -> list[dict]:
    try:
        data = _get("commentThreads", {
            "part": "snippet", "videoId": video_id, "maxResults": MAX_COMMENTS,
            "order": "relevance", "textFormat": "plainText",
        })
    except urllib.error.HTTPError:
        return []  # 댓글 사용 중지된 영상
    out = []
    for item in data.get("items", []):
        s = item["snippet"]["topLevelComment"]["snippet"]
        text = (s.get("textOriginal") or s.get("textDisplay") or "").strip()
        if text:
            out.append({"text": text[:500], "likes": int(s.get("likeCount", 0))})
    return out


def fetch(url_or_id: str) -> dict:
    """영상 정보 + 채널 정보 + 인기 댓글. 1시간 동안 캐시한다. 작성자 정보는 가져오지 않는다."""
    if not available():
        raise YouTubeError("서버에 유튜브 API 키(YOUTUBE_API_KEY)가 설정되지 않았습니다.")
    vid = parse_video_id(url_or_id)
    hit = _cache.get(vid)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    data = _get("videos", {"part": "snippet,statistics", "id": vid})
    if not data.get("items"):
        raise YouTubeError("영상을 찾을 수 없습니다. 비공개이거나 삭제된 영상일 수 있습니다.")
    v = data["items"][0]
    sn, st = v["snippet"], v.get("statistics", {})
    published = sn.get("publishedAt")
    days = None
    if published:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(published.replace("Z", "+00:00"))).days
    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")

    info = {
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "channel_id": sn.get("channelId", ""),
        "published_at": published,
        "days_since_upload": days,
        "description": (sn.get("description") or "")[:5000],
        "thumbnail": thumb,
        "views": _int(st, "viewCount"),
        "likes": _int(st, "likeCount"),
        "comment_count": _int(st, "commentCount"),
        **_channel(sn.get("channelId", ""), vid),
        "comments": _comments(vid),
    }
    _cache[vid] = (time.time(), info)
    if len(_cache) > 500:
        for k, (t, _) in list(_cache.items()):
            if time.time() - t > CACHE_SECONDS:
                del _cache[k]
    return info


def lyrics_from_description(description: str) -> str | None:
    """설명란에 가사가 있으면 가사 부분만 돌려준다 (짧은 줄이 많이 이어지는 구간)."""
    lines = [l.strip() for l in description.splitlines()]
    best, cur = [], []
    for l in lines + [""]:
        looks_lyric = 0 < len(l) <= 60 and not re.search(
            r"https?://|@|#|©|℗|instagram|youtube|soundcloud|credit|official|\bm/?v\b|produced|prod\.|mixed|master|"
            r"directed|composed|written|lyrics by|beat by|작사|작곡|편곡|믹스|마스터|감독|촬영|문의|contact",
            l, re.I)
        if looks_lyric:
            cur.append(l)
        elif l == "" and cur:
            cur.append("")
        else:
            if len([x for x in cur if x]) > len([x for x in best if x]):
                best = cur
            cur = []
    best = [x for x in best]
    while best and not best[-1]:
        best.pop()
    return "\n".join(best) if len([x for x in best if x]) >= 8 else None
