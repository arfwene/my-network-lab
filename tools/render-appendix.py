#!/usr/bin/env python3
"""
부록 렌더링.  docs/appendix/*.md.j2  ->  dist/appendix/*.md

부록에도 주소를 하드코딩하지 않는다. 치트시트에 적힌 IP 가 랩과 다르면
그 치트시트는 쓸모가 없는 정도가 아니라 **틀린 곳으로 안내한다.**

usage:  python3 tools/render-appendix.py [--lab 1]
"""
import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

SRC = L.ROOT / "docs/appendix"
OUT = L.ROOT / "dist/appendix"


def index():
    return (yaml.safe_load((SRC / "index.yml").read_text(encoding="utf-8")) or {}).get("appendix", [])


def render(lab_id=1, only=None):
    OUT.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(SRC), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    # 부록은 랩이 완성된 상태(m10)를 전제로 쓴다 — 과정 내내, 그리고 수료 후에 보는 문서다.
    ctx = L.doc_context(lab_id, "m10")
    out = []
    for d in index():
        if only and d["id"] != only:
            continue
        src = f"{d['id']}.md.j2"
        if not (SRC / src).exists():
            print(f"  !! {src} 없음", file=sys.stderr)
            continue
        (OUT / f"{d['id']}.md").write_text(
            env.get_template(src).render(**ctx, meta=d), encoding="utf-8")
        out.append(d)
        print(f"  {d['id']:<12} {d['title']}")
    (OUT / "index.yml").write_text(
        yaml.safe_dump({"appendix": out}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    a = sys.argv
    lab = int(a[a.index("--lab") + 1]) if "--lab" in a else 1
    only = a[a.index("--doc") + 1] if "--doc" in a else None
    print(f"rendering appendix (lab {lab}) -> dist/appendix/")
    print(f"{len(render(lab, only))} docs")
