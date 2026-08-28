"""모듈 교재를 랩별로 렌더링하고 HTML 로 바꾼다. 다이어그램은 인라인 SVG 로 치환한다."""
import importlib.util
import re
import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import markdown as md

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L
import mdtoc  # noqa: E402  (tools/)
import topology_svg  # noqa: E402  (console 패키지 내부)
import diagramsvg  # noqa: E402  (tools/)

# tools/render-labmap.py 는 하이픈이 있어 import 문으로 부를 수 없다.
# CLI(make docs)와 웹 콘솔이 **같은 코드**로 지도를 만들게 하려고 그대로 불러온다.
_spec = importlib.util.spec_from_file_location(
    "render_labmap", L.ROOT / "tools/render-labmap.py")
_labmap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_labmap)

SRC = L.ROOT / "modules"
MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.S)
DIAGRAM_FENCE = re.compile(r"```labdiagram\n(.*?)```", re.S)
STAGE_MARK = re.compile(r"^%%\s*lab-stage:\s*(m\d+)\s*$", re.M)


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
    """부록 하나를 마크다운으로. 랩이 완성된 상태(m11) 기준으로 렌더한다."""
    d = appendix_get(doc_id)
    if not d:
        return None
    src = APX / f"{doc_id}.md.j2"
    if not src.exists():
        return None
    env = Environment(loader=FileSystemLoader(APX), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    return env.get_template(src.name).render(**L.doc_context(lab_id, "m11"), meta=d)


def lab_map(lab_id=1, stage="m11"):
    """랩 지도 — 교육생이 상시 참조하는 문서.

    **파일을 읽지 않고 그 자리에서 만든다.** 전에는 `make gen` 이 만들어 둔
    dist/lab-map.md 를 읽었는데, 두 가지가 잘못돼 있었다.
      · 관리자가 make 를 돌리기 전에는 교재가 "지도를 보라" 고 하는데 볼 곳이 없었다
      · 그 파일은 늘 lab1 기준이라 lab3 교육생에게 lab1 주소를 보여 줬다
    설계(design/*.yml)에서 바로 만들면 둘 다 생기지 않는다.
    """
    return _labmap.render(stage, lab_id)


def render_markdown(module, lab_id, kind="README"):
    d = SRC / module["dir"]
    src = d / f"{kind}.md.j2"
    if not src.exists():
        return None
    env = Environment(loader=FileSystemLoader(d), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    ctx = {**L.doc_context(lab_id, module["stage"]), "meta": module}
    ctx["topology_full"] = L.mermaid(lab_id, "m11")
    out = env.get_template(src.name).render(**ctx)
    # 목차는 교재(README)에만. 과제·검증은 짧아서 필요 없다.
    return mdtoc.insert(out) if kind == "README" else out


def diagram_specs(module, lab_id):
    """그 모듈 교재에 든 구성도 스펙을 나온 순서대로. 내려받기가 n 번째를 찾는다."""
    md = render_markdown(module, lab_id) or ""
    return DIAGRAM_FENCE.findall(md)


def to_html(text, module=None):
    """마크다운 -> HTML. ```mermaid 블록은 서버에서 그린 SVG 로 바꾼다 (CDN 불필요).

    module 이 없으면(부록) 전체 토폴로지 기준으로 그린다.
    """
    stages = []
    default_stage = module["stage"] if module else "m11"

    def swap(m):
        body = m.group(1)
        # 그림이 스스로 밝힌 단계를 쓴다. 없으면 이 모듈의 단계로 그린다.
        # 예전에는 노드 수로 짐작했는데, 전체 토폴로지의 노드가 기준치에 못 미쳐
        # 어떤 그림도 "전체" 로 판정되지 않았다 — M0 의 "최종 모습" 이 M1 로 그려졌다.
        mark = STAGE_MARK.search(body)
        stage = mark.group(1) if mark else default_stage
        stages.append(stage)
        return (f'<div class="topo-wrap" data-stage="{stage}">'
                f'{topology_svg.render(stage)}</div>')

    text = MERMAID_FENCE.sub(swap, text)

    # 교재 안의 작은 구성도. 글자 그림(ASCII)으로는 한글 폭 때문에 상자가
    # 기울어져서, 설계값에서 그리도록 바꿨다 — 스펙이 틀리면 그림 대신
    # 무엇이 틀렸는지를 그 자리에 적는다. 교재 한 장이 통째로 죽는 것보다 낫다.
    seq = [0]

    def draw(m):
        i = seq[0]
        seq[0] += 1
        mid = (module or {}).get("id", "")
        try:
            svg = diagramsvg.render(m.group(1))
        except Exception as e:                       # noqa: BLE001
            return (f'<p class="flash bad">구성도를 그리지 못했습니다 — {e}</p>')
        return (f'<div class="dia-wrap" data-dia="{mid}:{i}">{svg}</div>')

    text = DIAGRAM_FENCE.sub(draw, text)
    # toc 확장이 제목마다 id 를 붙인다. 기본 slugify 는 한글을 통째로 버려서
    # id 가 전부 빈 문자열이 되고, 목차의 모든 줄이 같은 곳을 가리킨다.
    # mdtoc 이 목차를 만들 때 쓴 규칙을 그대로 넘긴다.
    # <details> 안의 마크다운. md_in_html 은 markdown="1" 이 붙은 것만 처리한다.
    # 안 붙이면 접힌 칸 안의 표·인용구·굵게가 전부 날것 그대로 화면에 찍힌다 —
    # 교육생에게는 `**두 번 로그인한다.**` 같은 글자가 그대로 보인다.
    # 교재 원본에는 안 적는다. 그건 화면 사정이지 문서의 내용이 아니다.
    text = re.sub(r"<details(?![^>]*markdown=)", '<details markdown="1"', text)

    html = md.markdown(text, extensions=["tables", "fenced_code", "attr_list",
                                         "sane_lists", "toc", "md_in_html"],
                       extension_configs={"toc": {"slugify": lambda s, sep: mdtoc.slug(s),
                                                  "permalink": False}})
    return html
