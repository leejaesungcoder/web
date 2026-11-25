"""운영자의 취향 기준(레퍼런스 곡·아티스트)과 레이블 목록을 불러와 프롬프트에 넣는다.

taste/references.json, taste/labels.json 을 고치면 다음 요청부터 반영된다.
"""

from __future__ import annotations

import json
from pathlib import Path

TASTE_DIR = Path(__file__).resolve().parent.parent / "taste"


def _load(name: str) -> dict:
    path = TASTE_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def references() -> dict:
    data = _load("references.json")
    return {
        "tracks": [t for t in data.get("tracks", []) if t.get("title")],
        "artists": [a for a in data.get("artists", []) if a],
        "notes": [n for n in data.get("notes", []) if n],
    }


def labels() -> list[dict]:
    return [l for l in _load("labels.json").get("labels", []) if l.get("name")]


def prompt_block() -> str:
    """사용자 메시지에 넣을 레퍼런스·레이블 블록. 아무것도 없으면 빈 문자열."""
    ref = references()
    labs = labels()
    parts = []
    if ref["tracks"] or ref["artists"]:
        lines = []
        for t in ref["tracks"]:
            lines.append(f"- 곡: {t['title']}" + (f" ({t['artist']})" if t.get("artist") else " (아티스트 미기재)"))
        if ref["artists"]:
            lines.append("- 아티스트: " + ", ".join(ref["artists"]))
        lines += [f"- 메모: {n}" for n in ref["notes"]]
        parts.append(
            "<운영자_레퍼런스>\n"
            "이 앱 운영자가 '좋은 국힙'의 기준으로 삼는 곡과 아티스트다. 네가 이 곡이나 아티스트를 확실히 알면 "
            "그 특징(플로우, 톤, 프로덕션, 컨셉)을 기준점으로 삼아 새 곡의 위치를 판단한다. 확실히 모르는 곡은 "
            "특징을 지어내지 말고 아는 것만 쓴다.\n"
            + "\n".join(lines)
            + "\n</운영자_레퍼런스>\n"
        )
    if labs:
        parts.append(
            "<레이블_목록>\n"
            "운영자가 관리하는 레이블 목록이다. 어울리는 레이블은 이 목록 안에서만 고른다.\n"
            + json.dumps(labs, ensure_ascii=False, indent=2)
            + "\n</레이블_목록>\n"
        )
    return "\n".join(parts)
