#!/usr/bin/env python3
"""교재 문장 규칙(docs/STYLE.md)을 기계가 잰다.

  python3 tools/check-style.py          전체
  python3 tools/check-style.py m01      한 모듈

규칙을 다 잴 수는 없다. 사람이 읽어야 아는 것(논평인가 정보인가)은 못 센다.
대신 **밀도**를 센다 — 굵게가 문장보다 많거나, 일곱 줄에 한 줄이 인용구면
읽는 사람이 무엇이 중요한지 고를 수 없다. 그건 세면 나온다.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G, Y, R, N = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

BOLD_MAX = 0.35        # 문장당 굵게
QUOTE_MAX = 8.0        # 100줄당 인용구 줄
TABLE_MAX = 18.0       # 본문 줄 대비 표 줄 %. 날카로운 규칙은 '작은표 0' 쪽이다 —
                       # 이 비율은 '표가 많아 보인다' 는 신호일 뿐, 넘었다고 다 틀린 것은 아니다.
                       # M0 는 찾아보는 표(도구 10행·오류 대응 6행)만으로 16% 가 된다.
PREFIX = ("실무", "주의", "**소요 시간**")   # 인용구에 허용된 머리말


def strip_blocks(t):
    """코드·다이어그램·표는 문장 규칙 대상이 아니다."""
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("|"))
    return t


def tables(t):
    """(표 개수, 표가 차지한 줄, 2열 3행 이하 표). 코드 블록은 뺀다."""
    lines = re.sub(r"```.*?```", "", t, flags=re.S).splitlines()
    blocks, cur = [], []
    for l in lines:
        if l.lstrip().startswith("|"):
            cur.append(l)
        elif cur:
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    tiny = []
    for b in blocks:
        cols = b[0].strip().strip("|").count("|") + 1
        rows = len(b) - 2                       # 머리글 + 구분선 제외
        if cols <= 2 and rows <= 3:
            tiny.append(b[0].strip()[:56])
    return len(blocks), sum(len(b) for b in blocks), tiny, len(lines)


def measure(p):
    raw = p.read_text(encoding="utf-8")
    body = strip_blocks(raw)
    lines = body.splitlines()
    quotes = [l for l in lines if l.lstrip().startswith(">")]
    # 이어지는 인용구 줄은 하나로 센다 (문단 수를 세는 것이 목적)
    blocks, prev = [], False
    for l in lines:
        q = l.lstrip().startswith(">")
        if q and not prev:
            blocks.append(l.lstrip()[1:].strip())
        prev = q
    bad_prefix = [b for b in blocks if not any(b.startswith(x) for x in PREFIX)]
    bold = len(re.findall(r"\*\*[^*\n]+\*\*", body))
    sent = len(re.findall(r"(?:다|요)[.]|[.!?]\s*$", body, re.M)) or 1
    ntab, tab_lines, tiny, all_lines = tables(raw)
    return {
        "lines": len(lines), "quote_lines": len(quotes), "quote_blocks": len(blocks),
        "bad_prefix": bad_prefix, "bold": bold, "sent": sent, "tiny": tiny,
        "bold_per_sent": bold / sent, "quote_per_100": len(quotes) / max(len(lines), 1) * 100,
        "table_pct": tab_lines / max(all_lines, 1) * 100, "tables": ntab,
    }


def main(only=None):
    files = sorted((ROOT / "modules").glob("*/README.md.j2"))
    if only:
        files = [f for f in files if f.parent.name.startswith(only)]
    bad = 0
    print(f"{'모듈':<6}{'굵게/문장':>10}{'인용/100줄':>11}{'표%':>7}{'작은표':>7}{'머리말없는인용':>15}")
    print("-" * 60)
    for f in files:
        m = measure(f)
        marks = []
        if m["bold_per_sent"] > BOLD_MAX: marks.append("굵게")
        if m["quote_per_100"] > QUOTE_MAX: marks.append("인용")
        if m["table_pct"] > TABLE_MAX: marks.append("표")
        if m["tiny"]: marks.append("작은표")
        if m["bad_prefix"]: marks.append("머리말")
        col = R if marks else G
        print(f"{col}{f.parent.name[:4]:<6}{m['bold_per_sent']:>10.2f}"
              f"{m['quote_per_100']:>11.1f}{m['table_pct']:>7.1f}{len(m['tiny']):>7}"
              f"{len(m['bad_prefix']):>15}{N}")
        if marks:
            bad += 1
            for b in m["bad_prefix"][:2]:
                print(f"        {Y}머리말 없는 인용구{N}: {b[:60]}")
            for b in m["tiny"][:2]:
                print(f"        {Y}2열 3행 이하 표{N}: {b}")
    print("-" * 60)
    print(f"기준: 굵게/문장 ≤ {BOLD_MAX} · 인용/100줄 ≤ {QUOTE_MAX} · "
          f"표 ≤ {TABLE_MAX}% · 작은표 0 · 머리말 없는 인용구 0")
    if bad:
        print(f"{Y}{bad}개 모듈이 기준을 넘는다 (docs/STYLE.md){N}")
    else:
        print(f"{G}전부 기준 안{N}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
