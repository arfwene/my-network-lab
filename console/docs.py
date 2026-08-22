"""모듈 교재를 랩별로 렌더링하고 HTML 로 바꾼다. 다이어그램은 인라인 SVG 로 치환한다."""
import re
import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import markdown as md

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L
import topology_svg  # noqa: E402  (console 패키지 내부)

SRC = L.ROOT / "modules"
MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.S)


def modules():
    out = []
    for d in sorted(SRC.iterdir()):
        f = d / "meta.yml"
        if d.is_dir() and f.exists():
            m = yaml.safe_load(f.read_text(encoding="utf-8"))
            m["dir"] = d.name
            out.append(m)
    return out


def get(module_id):
    for m in modules():
        if m["id"] == module_id:
            return m
    return None


# ------------------------------------------------------------------ 부록
#  부록은 모듈이 아니다 — 통과 게이팅도 퀴즈도 없고, 처음부터 전부 열려 있다.
#  과정 중에는 곁에 두고 보고, 수료 후에는 현장에서 꺼내 보는 문서다.
APX = L.ROOT / "docs/appendix"


def appendix():
    f = APX / "index.yml"
    if not f.exists():
        return []
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("appendix", [])


def appendix_get(doc_id):
    return next((d for d in appendix() if d["id"] == doc_id), None)


def render_appendix(doc_id, lab_id):
    """부록 하나를 마크다운으로. 랩이 완성된 상태(m10) 기준으로 렌더한다."""
    d = appendix_get(doc_id)
    if not d:
        return None
    src = APX / f"{doc_id}.md.j2"
    if not src.exists():
        return None
    env = Environment(loader=FileSystemLoader(APX), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    return env.get_template(src.name).render(**L.doc_context(lab_id, "m10"), meta=d)


def lab_map():
    """랩 지도 — 교육생이 상시 참조하는 문서.

    `make gen`(tools/render-labmap.py)이 만든 dist/lab-map.md 를 그대로 읽는다.
    교재가 "지도를 보라" 고 하는데 볼 곳이 없으면 안 된다 —
    교육생은 파일 시스템에 접근할 수 없다.
    """
    f = L.ROOT / "dist/lab-map.md"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8")


def render_markdown(module, lab_id, kind="README"):
    d = SRC / module["dir"]
    src = d / f"{kind}.md.j2"
    if not src.exists():
        return None
    env = Environment(loader=FileSystemLoader(d), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    ctx = {**L.doc_context(lab_id, module["stage"]), "meta": module}
    ctx["topology_full"] = L.mermaid(lab_id, "m10")
    return env.get_template(src.name).render(**ctx)


def to_html(text, module=None):
    """마크다운 -> HTML. ```mermaid 블록은 서버에서 그린 SVG 로 바꾼다 (CDN 불필요).

    module 이 없으면(부록) 전체 토폴로지 기준으로 그린다.
    """
    stages = []
    default_stage = module["stage"] if module else "m10"

    def swap(m):
        body = m.group(1)
        # 전체 토폴로지인지 현재 단계인지 구분: 노드 수로 판단
        stage = "m10" if body.count("([") + body.count("((") >= 8 else default_stage
        stages.append(stage)
        return (f'<div class="topo-wrap" data-stage="{stage}">'
                f'{topology_svg.render(stage)}</div>')

    text = MERMAID_FENCE.sub(swap, text)
    html = md.markdown(text, extensions=["tables", "fenced_code", "attr_list", "sane_lists"])
    return html
