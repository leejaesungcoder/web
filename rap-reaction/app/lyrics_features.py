"""가사에서 간단한 정량 지표를 뽑는다.

Claude에게 근거 자료로 함께 넘기고, 화면에도 그대로 보여준다.
형태소 분석기 없이 동작하도록 공백/음절 단위의 가벼운 휴리스틱만 쓴다.
"""

from __future__ import annotations

import re
from collections import Counter

HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[가-힣A-Za-z0-9']+")
SECTION_TAG = re.compile(r"^\s*[\[(][^\])]{1,30}[\])]\s*$")  # [Verse 1], (Hook) 등


def _last_syllables(line: str, n: int = 2) -> str:
    """줄 끝 n음절 (라임 비교용). 한글이 없으면 영어 끝 3글자."""
    hangul = HANGUL.findall(line)
    if hangul:
        return "".join(hangul[-n:])
    latin = LATIN.findall(line)
    return "".join(latin[-3:]).lower()


def _vowel_of(syllable: str) -> int | None:
    """한글 음절의 중성(모음) 인덱스. 모음 라임 비교에 쓴다."""
    code = ord(syllable) - 0xAC00
    if 0 <= code < 11172:
        return (code % 588) // 28
    return None


def _rhymes(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a[-1] == b[-1]:
        return True
    # 마지막 음절의 모음이 같으면 느슨한 라임으로 본다 (예: 사랑/가방).
    va, vb = _vowel_of(a[-1]), _vowel_of(b[-1])
    return va is not None and va == vb


def extract_features(lyrics: str) -> dict:
    lines = [
        l.strip() for l in lyrics.splitlines()
        if l.strip() and not SECTION_TAG.match(l)
    ]
    body = "\n".join(lines)
    words = WORD.findall(body)
    lowered = [w.lower() for w in words]

    hangul_chars = len(HANGUL.findall(body))
    latin_chars = len(LATIN.findall(body))
    letters = hangul_chars + latin_chars

    # 인접한 줄(바로 다음 줄, 한 줄 건너 다음 줄)끼리 끝 라임이 맞는 비율
    endings = [_last_syllables(l) for l in lines]
    rhyme_pairs = 0
    comparisons = 0
    for i in range(len(endings) - 1):
        for j in (i + 1, i + 2):
            if j < len(endings):
                comparisons += 1
                if _rhymes(endings[i], endings[j]):
                    rhyme_pairs += 1

    # 3번 이상 반복되는 줄 = 훅/후렴 후보
    line_counts = Counter(lines)
    repeated_lines = [l for l, c in line_counts.most_common() if c >= 3][:5]

    top_words = [
        w for w, _ in Counter(w for w in lowered if len(w) > 1).most_common(10)
    ]

    return {
        "line_count": len(lines),
        "word_count": len(words),
        "unique_word_ratio": round(len(set(lowered)) / len(lowered), 3) if lowered else 0.0,
        "english_ratio": round(latin_chars / letters, 3) if letters else 0.0,
        "end_rhyme_ratio": round(rhyme_pairs / comparisons, 3) if comparisons else 0.0,
        "avg_syllables_per_line": round(letters / len(lines), 1) if lines else 0.0,
        "repeated_lines": repeated_lines,
        "top_words": top_words,
    }
