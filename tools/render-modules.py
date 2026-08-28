#!/usr/bin/env python3
"""
모듈 교재 렌더링.  modules/<id>/*.j2  ->  dist/modules/<id>/*.md

교재에도 주소를 하드코딩하지 않는다. config/site.yml 이 바뀌면 교재의 IP 도 따라 바뀐다.
템플릿에서 쓸 수 있는 값은 tools/labdesign.py 의 doc_context() 참조.

usage:  python3 tools/render-modules.py [--lab 1] [--module m01]
"""
import re
import shutil
import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L
import diagramsvg
import mdtoc

SRC = L.ROOT / "modules"
OUT = L.ROOT / "dist/modules"


# 해설·평가 정의는 **정답지**다. 운영 서버를 점프 호스트로도 쓰면 교육생이 같은 장비에
# 붙으므로, 파일 권한만으로 한 겹 막아 둔다 (콘솔은 같은 계정으로 돌아 읽을 수 있다).
SECRET = {"answers.md", "assessment.yml"}


def _protect(path):
    try:
        path.chmod(0o600 if path.name in SECRET else 0o644)
    except OSError:
        pass


DIAGRAM = re.compile(r"```labdiagram\n(.*?)```", re.S)


def _diagrams(text, dst):
    """```labdiagram 블록을 SVG 파일로 빼고 그림 링크만 남긴다.

    화면에서는 콘솔이 같은 코드로 인라인 SVG 를 그린다. 오프라인 문서에는
    YAML 덩어리를 남길 수 없고, 인라인 SVG 를 넣으면 마크다운 뷰어가 대부분
    지워 버린다 — 파일로 빼서 그림으로 거는 것이 어디서나 보인다.
    """
    seq = [0]

    def swap(m):
        i = seq[0]
        seq[0] += 1
        d = dst / "diagrams"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i}.svg").write_text(diagramsvg.render(m.group(1), standalone=True), encoding="utf-8")
        return f"![구성도](diagrams/{i}.svg)"

    return DIAGRAM.sub(swap, text)


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
                out = dst / f.stem
                body = env.get_template(f.name).render(**ctx)
                # 콘솔과 같은 목차를 넣는다. 두 곳이 다른 문서를 내면 안 된다.
                if f.stem == "README.md":
                    body = mdtoc.insert(body)
                out.write_text(_diagrams(body, dst), encoding="utf-8")
                _protect(out)
            elif f.name != "meta.yml":
                shutil.copy2(f, dst / f.name)
                _protect(dst / f.name)
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
