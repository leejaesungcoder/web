"""이미 반응이 알려진 발매곡으로 점수가 맞는지 확인한다.

    export ANTHROPIC_API_KEY=sk-ant-api03-...
    python -m tools.calibrate                 # calibration/cases.json 전체
    python -m tools.calibrate --only wu       # 제목에 'wu'가 들어간 곡만

calibration/cases.json 에 곡 정보와 기대 점수 범위를 적고,
가사는 calibration/lyrics/ 에 텍스트 파일로 둔다 (저작권 때문에 git에는 올라가지 않는다).
곡 하나당 Claude API를 한 번 호출하므로 비용이 든다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.analyzer import analyze, has_api_key

ROOT = Path(__file__).resolve().parent.parent / "calibration"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="발매곡으로 점수 보정 확인")
    p.add_argument("--only", help="제목에 이 글자가 들어간 곡만")
    args = p.parse_args(argv)

    if not has_api_key():
        print("ANTHROPIC_API_KEY 환경 변수를 설정하세요.", file=sys.stderr)
        return 2

    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if args.only.lower() in c["title"].lower()]

    misses = 0
    rows = []
    for c in cases:
        lyrics_path = ROOT / c["lyrics_file"]
        if not lyrics_path.exists():
            print(f"- 건너뜀: {c['title']} (가사 파일 없음: {lyrics_path.relative_to(ROOT.parent)})")
            continue
        print(f"- 분석 중: {c['artist']} - {c['title']} ...", flush=True)
        r = analyze(
            lyrics_path.read_text(encoding="utf-8"),
            title=c["title"],
            artist_level=c.get("artist_level"),
            vibe=c.get("vibe"),
            delivery=c.get("delivery"),
        )
        rep = r["report"]
        scores = {a["audience"]: a["score"] for a in rep["audiences"]}
        scores["종합"] = rep["overall_score"]
        checks = []
        for who, (lo, hi) in c.get("expected", {}).items():
            got = scores.get(who)
            ok = got is not None and lo <= got <= hi
            misses += 0 if ok else 1
            checks.append(f"{who} {got} (기대 {lo}~{hi}) {'OK' if ok else '<< 벗어남'}")
        rows.append((c, rep, scores, checks))

    print()
    for c, rep, scores, checks in rows:
        sr = rep["style_read"]
        print(f"■ {c['artist']} - {c['title']}")
        print(f"  스타일: {sr['style']} / 딜리버리 의존도 {sr['delivery_dependence']}")
        print("  점수: " + ", ".join(f"{k} {v}" for k, v in scores.items()))
        for line in checks:
            print("  " + line)
        print(f"  총평: {rep['verdict']}")
        print()
    print(f"기대 범위를 벗어난 항목: {misses}개")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
