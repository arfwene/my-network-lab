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
PREFIX = ("실무", "주의", "**소요 시간**")   # 인용구에 허용된 머리말


def strip_blocks(t):
    """코드·다이어그램·표는 규칙 대상이 아니다."""
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("|"))
    return t


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
    return {
        "lines": len(lines), "quote_lines": len(quotes), "quote_blocks": len(blocks),
        "bad_prefix": bad_prefix, "bold": bold, "sent": sent,
        "bold_per_sent": bold / sent, "quote_per_100": len(quotes) / max(len(lines), 1) * 100,
    }


def main(only=None):
    files = sorted((ROOT / "modules").glob("*/README.md.j2"))
    if only:
        files = [f for f in files if f.parent.name.startswith(only)]
    bad = 0
    print(f"{'모듈':<6}{'굵게/문장':>10}{'인용/100줄':>11}{'머리말없는인용':>15}")
    print("-" * 44)
    for f in files:
        m = measure(f)
        marks = []
        if m["bold_per_sent"] > BOLD_MAX: marks.append("굵게")
        if m["quote_per_100"] > QUOTE_MAX: marks.append("인용")
        if m["bad_prefix"]: marks.append("머리말")
        col = R if marks else G
        print(f"{col}{f.parent.name[:4]:<6}{m['bold_per_sent']:>10.2f}"
              f"{m['quote_per_100']:>11.1f}{len(m['bad_prefix']):>15}{N}")
        if marks:
            bad += 1
            for b in m["bad_prefix"][:3]:
                print(f"        {Y}머리말 없는 인용구{N}: {b[:64]}")
    print("-" * 44)
    print(f"기준: 굵게/문장 ≤ {BOLD_MAX} · 인용/100줄 ≤ {QUOTE_MAX} · 머리말 없는 인용구 0")
    if bad:
        print(f"{Y}{bad}개 모듈이 기준을 넘는다 (docs/STYLE.md){N}")
    else:
        print(f"{G}전부 기준 안{N}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
