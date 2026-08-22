#!/usr/bin/env python3
"""
모듈 교재 렌더링.  modules/<id>/*.j2  ->  dist/modules/<id>/*.md

교재에도 주소를 하드코딩하지 않는다. config/site.yml 이 바뀌면 교재의 IP 도 따라 바뀐다.
템플릿에서 쓸 수 있는 값은 tools/labdesign.py 의 doc_context() 참조.

usage:  python3 tools/render-modules.py [--lab 1] [--module m01]
"""
import shutil
import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

SRC = L.ROOT / "modules"
OUT = L.ROOT / "dist/modules"


def module_dirs():
    return sorted(d for d in SRC.iterdir() if d.is_dir() and (d / "meta.yml").exists())


def render(lab_id=1, only=None):
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for d in module_dirs():
        meta = yaml.safe_load((d / "meta.yml").read_text(encoding="utf-8"))
        if only and meta["id"] != only:
            continue
        ctx = {**L.doc_context(lab_id, meta["stage"]), "meta": meta}
        ctx["topology_full"] = L.mermaid(lab_id, "m10")
        env = Environment(loader=FileSystemLoader(d), undefined=StrictUndefined,
                          keep_trailing_newline=True)
        dst = OUT / d.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(d.iterdir()):
            if f.suffix == ".j2":
                (dst / f.stem).write_text(env.get_template(f.name).render(**ctx), encoding="utf-8")
            elif f.name != "meta.yml":
                shutil.copy2(f, dst / f.name)
                if f.suffix == ".sh":
                    (dst / f.name).chmod(0o755)
        shutil.copy2(d / "meta.yml", dst / "meta.yml")
        index.append({**meta, "dir": d.name})
        print(f"  {meta['id']}  {meta['title']}")

    (OUT / "index.yml").write_text(
        yaml.safe_dump({"modules": index}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return index


if __name__ == "__main__":
    a = sys.argv
    lab = int(a[a.index("--lab") + 1]) if "--lab" in a else 1
    only = a[a.index("--module") + 1] if "--module" in a else None
    print(f"rendering modules (lab {lab}) -> dist/modules/")
    idx = render(lab, only)
    print(f"{len(idx)} modules")
