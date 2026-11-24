"""YouTube Data API v3로 국내 힙합 음원 영상의 댓글을 수집한다.

사용 예:
    export YOUTUBE_API_KEY=...
    python -m collector.youtube_comments --query "국힙 신곡 MV" --max-videos 20
    python -m collector.youtube_comments --video https://youtu.be/VIDEO_ID
    python -m collector.youtube_comments --queries-file collector/seed_queries.txt

결과:
    data/comments.jsonl  댓글 1개당 1줄 (작성자 정보는 저장하지 않음)
    data/videos.jsonl    영상별 조회수/좋아요/댓글 수 (4단계 점수 모델의 정답 라벨로 씀)

쿼터: 하루 기본 10,000 유닛. 검색 1회 = 100유닛, 댓글 페이지(100개) 1회 = 1유닛.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://www.googleapis.com/youtube/v3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMMENTS_FILE = DATA_DIR / "comments.jsonl"
VIDEOS_FILE = DATA_DIR / "videos.jsonl"

VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


class QuotaExceeded(Exception):
    pass


def _get(endpoint: str, params: dict, api_key: str, retries: int = 3) -> dict:
    params = {**params, "key": api_key}
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            reason = ""
            try:
                reason = json.loads(body)["error"]["errors"][0]["reason"]
            except (KeyError, IndexError, ValueError):
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise QuotaExceeded(body) from e
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{endpoint} {e.code} {reason or body[:200]}") from e
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def parse_video_id(value: str) -> str:
    m = VIDEO_ID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    raise ValueError(f"영상 ID를 찾을 수 없습니다: {value}")


def search_videos(query: str, max_videos: int, api_key: str, order: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while len(ids) < max_videos:
        params = {
            "part": "id",
            "q": query,
            "type": "video",
            "maxResults": min(50, max_videos - len(ids)),
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "videoCategoryId": "10",  # Music
            "order": order,
        }
        if page_token:
            params["pageToken"] = page_token
        data = _get("search", params, api_key)
        ids += [item["id"]["videoId"] for item in data.get("items", [])]
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids[:max_videos]


def fetch_video_meta(video_ids: list[str], api_key: str) -> list[dict]:
    out = []
    for i in range(0, len(video_ids), 50):
        data = _get(
            "videos",
            {"part": "snippet,statistics", "id": ",".join(video_ids[i : i + 50])},
            api_key,
        )
        for item in data.get("items", []):
            s, st = item["snippet"], item.get("statistics", {})
            out.append({
                "video_id": item["id"],
                "title": s.get("title", ""),
                "channel": s.get("channelTitle", ""),
                "published_at": s.get("publishedAt"),
                "view_count": int(st.get("viewCount", 0)),
                "like_count": int(st.get("likeCount", 0)),
                "comment_count": int(st.get("commentCount", 0)),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
    return out


def fetch_comments(video: dict, max_comments: int, api_key: str, order: str) -> list[dict]:
    comments: list[dict] = []
    page_token = None
    while len(comments) < max_comments:
        params = {
            "part": "snippet",
            "videoId": video["video_id"],
            "maxResults": 100,
            "order": order,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            data = _get("commentThreads", params, api_key)
        except RuntimeError as e:
            if "commentsDisabled" in str(e) or "403" in str(e):
                print(f"  - 댓글 비활성화/접근 불가: {video['title'][:40]}", file=sys.stderr)
                return comments
            raise
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]
            s = top["snippet"]
            comments.append({
                "comment_id": top["id"],
                "video_id": video["video_id"],
                "video_title": video["title"],
                "channel": video["channel"],
                "text": s.get("textOriginal") or s.get("textDisplay", ""),
                "like_count": int(s.get("likeCount", 0)),
                "reply_count": int(item["snippet"].get("totalReplyCount", 0)),
                "published_at": s.get("publishedAt"),
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return comments[:max_comments]


def _existing_ids(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line)[key])
            except (ValueError, KeyError):
                continue
    return ids


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="유튜브 힙합 음원 댓글 수집기")
    p.add_argument("--query", action="append", default=[], help="검색어 (여러 번 지정 가능)")
    p.add_argument("--queries-file", help="한 줄에 검색어 하나씩 적힌 파일")
    p.add_argument("--video", action="append", default=[], help="영상 URL 또는 ID (여러 번 지정 가능)")
    p.add_argument("--max-videos", type=int, default=10, help="검색어당 영상 수 (기본 10)")
    p.add_argument("--max-comments", type=int, default=300, help="영상당 댓글 수 (기본 300)")
    p.add_argument("--search-order", default="viewCount", choices=["viewCount", "relevance", "date"])
    p.add_argument("--comment-order", default="relevance", choices=["relevance", "time"])
    args = p.parse_args(argv)

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY 환경 변수를 설정하세요.", file=sys.stderr)
        return 2

    queries = list(args.query)
    if args.queries_file:
        queries += [
            l.strip() for l in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")
        ]

    try:
        video_ids = [parse_video_id(v) for v in args.video]
        for q in queries:
            found = search_videos(q, args.max_videos, api_key, args.search_order)
            print(f"검색 '{q}': 영상 {len(found)}개")
            video_ids += found
        video_ids = list(dict.fromkeys(video_ids))  # 순서 유지 중복 제거
        if not video_ids:
            print("수집할 영상이 없습니다. --query 또는 --video를 지정하세요.", file=sys.stderr)
            return 2

        videos = fetch_video_meta(video_ids, api_key)
        _append(VIDEOS_FILE, videos)

        seen = _existing_ids(COMMENTS_FILE, "comment_id")
        total = 0
        for v in videos:
            rows = [
                c for c in fetch_comments(v, args.max_comments, api_key, args.comment_order)
                if c["comment_id"] not in seen
            ]
            seen.update(c["comment_id"] for c in rows)
            _append(COMMENTS_FILE, rows)
            total += len(rows)
            print(f"  + {len(rows):4d}개  {v['title'][:50]}  (조회수 {v['view_count']:,})")
    except QuotaExceeded:
        print("오늘의 YouTube API 쿼터를 다 썼습니다. 지금까지 수집한 데이터는 저장됐습니다.", file=sys.stderr)
        return 1

    print(f"완료: 새 댓글 {total}개 -> {COMMENTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
