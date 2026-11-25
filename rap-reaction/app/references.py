"""수집해 둔 유튜브 댓글(data/*.jsonl)을 불러와 '실제 댓글 말투' 참고 자료로 쓴다.

1단계에서는 유사 곡 검색(RAG) 없이, 좋아요가 많은 댓글을 무작위로 골라
Claude가 예상 댓글을 생성할 때 실제 한국 힙합 청자의 어투를 따르도록 돕는다.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_comments() -> list[dict]:
    comments: list[dict] = []
    # videos.jsonl(영상 통계)은 댓글이 아니므로 제외한다
    for path in sorted(DATA_DIR.glob("comments*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    comments.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return comments


def sample_reference_comments(k: int = 40, min_len: int = 8, max_len: int = 200) -> list[dict]:
    """좋아요 상위 30% 댓글 위주로 k개를 뽑는다."""
    pool = [
        c for c in load_comments()
        if min_len <= len(c.get("text", "")) <= max_len
    ]
    if not pool:
        return []
    pool.sort(key=lambda c: c.get("like_count", 0), reverse=True)
    top = pool[: max(k, int(len(pool) * 0.3))]
    picked = random.sample(top, min(k, len(top)))
    return [
        {
            "video": c.get("video_title", ""),
            "text": c.get("text", ""),
            "likes": c.get("like_count", 0),
        }
        for c in picked
    ]


def dataset_stats() -> dict:
    comments = load_comments()
    return {
        "comment_count": len(comments),
        "video_count": len({c.get("video_id") for c in comments}),
    }
