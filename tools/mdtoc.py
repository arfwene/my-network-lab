#!/usr/bin/env python3
"""교재 맨 위에 목차를 넣는다.

교재 한 편이 500줄이 넘는다. 교육생은 실습하다 막히면 되돌아와 특정 절을
찾는데, 그때 스크롤로 헤매게 두면 안 된다.

`##` 만 담는다. `###` 까지 넣으면 목차가 30줄이 되어, 목차를 찾는 데 다시
목차가 필요해진다.

앵커는 파이썬-마크다운의 toc 확장이 만드는 id 와 **같은 규칙**으로 만든다.
기본 slugify 는 한글을 통째로 버려서 id 가 전부 빈 문자열이 된다 — 그러면
목차의 모든 줄이 같은 곳을 가리킨다.
"""
import re

# 목차에 넣지 않는 절. 제목 자체가 목차 역할이거나, 맨 위·맨 아래 고정 블록이다.
SKIP = ("학습 목표", "사전 지식", "다음 —")


def slug(text):
    t = re.sub(r"[`*\[\]()]", "", text).strip().lower()
    t = re.sub(r"[^\w\s가-힣ㄱ-ㅎㅏ-ㅣ·—-]", "", t)
    t = re.sub(r"\s+", "-", t)
    return t.strip("-")


def headings(md):
    out = []
    for line in re.sub(r"```.*?```", "", md, flags=re.S).splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and not m.group(1).startswith("#"):
            out.append(m.group(1))
    return out


def build(md, min_count=4):
    """목차 마크다운. 절이 min_count 개보다 적으면 만들지 않는다."""
    hs = [h for h in headings(md) if not any(h.startswith(s) for s in SKIP)]
    if len(hs) < min_count:
        return ""
    rows = "\n".join(f"- [{h}](#{slug(h)})" for h in hs)
    return f"## 이 문서의 차례\n\n{rows}\n"


def insert(md):
    """첫 `##` 바로 앞에 목차를 끼운다. 이미 있으면 그대로 둔다."""
    if "## 이 문서의 차례" in md:
        return md
    toc = build(md)
    if not toc:
        return md
    m = re.search(r"^##\s+", md, re.M)
    if not m:
        return md
    i = m.start()
    return md[:i] + toc + "\n---\n\n" + md[i:]


if __name__ == "__main__":
    import sys
    print(insert(open(sys.argv[1], encoding="utf-8").read()))
