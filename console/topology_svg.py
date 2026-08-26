"""
토폴로지를 인라인 SVG 로 그린다. 배치는 tools/topolayout.py 가 계산한다.

Mermaid.js 를 쓰지 않는 이유: 랩 서버는 폐쇄망일 수 있고, CDN 에 의존하면 그림이 안 뜬다.
설계 데이터에서 직접 그리면 의존성이 0 이 된다.

▸ 왜 장비마다 모양이 다른가
  예전에는 전부 같은 둥근 사각형이었고 채움색만 달랐다. 그러면 인쇄하거나
  색약이면 라우터와 PC 가 같은 그림이 된다. 실루엣으로 구분하면 색이 없어도 읽힌다.

▸ 색은 CSS 변수로 둔다
  화면이 밝은 모드 · 어두운 모드를 오가기 때문. 색을 SVG 에 박으면 어두운 모드에서
  흰 상자가 뜬다.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import topolayout as T

FILL = {"host": "var(--n-host)", "switch": "var(--n-switch)", "router": "var(--n-router)",
        "server": "var(--n-server)", "edge": "var(--n-edge)"}


# --------------------------------------------------------------- 장비 그림
#  각 함수는 (0,0) 기준 gw x gh 안에 그린다. 채움 · 선은 바깥에서 group 이 준다.
def _pc(w, h):
    sc, nk, bs = h - 10, 5, 5          # 화면 / 목 / 받침
    return (f'<rect x="0" y="0" width="{w}" height="{sc}" rx="2.5"/>'
            f'<rect x="{w/2-5:.1f}" y="{sc}" width="10" height="{nk}" class="sub"/>'
            f'<rect x="{w/2-15:.1f}" y="{sc+nk}" width="30" height="{bs}" rx="2" class="sub"/>')


def _switch(w, h):
    body, port = h - 10, 7
    ports = "".join(f'<rect x="{2+i*11}" y="{body+3}" width="7" height="{port}" rx="1" class="sub"/>'
                    for i in range(6))
    return f'<rect x="0" y="0" width="{w}" height="{body}" rx="3"/>{ports}'


def _router(w, h):
    r, c = w / 2, w / 2
    return (f'<circle cx="{c}" cy="{c}" r="{r}"/>'
            f'<path class="ico" d="M{c-11},{c-6} H{c+7} M{c+3},{c-10} L{c+9},{c-6} L{c+3},{c-2}"/>'
            f'<path class="ico" d="M{c+11},{c+6} H{c-7} M{c-3},{c+2} L{c-9},{c+6} L{c-3},{c+10}"/>')


def _server(w, h):
    slots = "".join(f'<rect x="6" y="{7+i*8}" width="{w-12}" height="4" rx="1" class="sub"/>'
                    for i in range(3))
    return (f'<rect x="0" y="0" width="{w}" height="{h}" rx="3"/>{slots}'
            f'<circle cx="11" cy="{h-8}" r="2" class="sub"/>'
            f'<circle cx="19" cy="{h-8}" r="2" class="sub"/>')


def _firewall(w, h):
    rows = h / 3
    out = [f'<rect x="0" y="0" width="{w}" height="{h}" rx="3"/>']
    for i in (1, 2):
        out.append(f'<path class="brick" d="M0,{rows*i:.1f} H{w}"/>')
    for i in range(3):
        xs = (w / 3, w * 2 / 3) if i % 2 == 0 else (w / 6, w / 2, w * 5 / 6)
        for x in xs:
            out.append(f'<path class="brick" d="M{x:.1f},{rows*i:.1f} V{rows*(i+1):.1f}"/>')
    return "".join(out)


def _cloud(w, h):
    return ('<path d="M8,33 C1,33 0,23 8,21 C8,10 22,6 28,14 '
            'C33,7 45,9 45,18 C55,17 58,28 52,33 Z"/>')


GLYPH_FN = {"pc": _pc, "switch": _switch, "router": _router,
            "server": _server, "firewall": _firewall, "cloud": _cloud}


def render(stage="m10", highlight=None, show_labels=True):
    d = T.layout(stage)
    hl = set(highlight or ())
    # 원본 크기를 그대로 들고 나간다. 칸이 좁다고 줄이면 글자가 못 읽는 크기가
    # 되므로, 줄이는 대신 감싼 상자가 가로로 넘긴다 (눌러서 크게도 볼 수 있다).
    out = [f'<svg viewBox="0 0 {d["w"]} {d["h"]}" width="{d["w"]}" height="{d["h"]}" '
           f'class="topo" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="랩 토폴로지 ({stage.upper()} 단계)">']

    # ---- 구역 상자. 제목은 링크가 지나가지 않는 쪽에 붙는다.
    for z in d["zones"]:
        # 좁은 구역(core)은 이름과 요약 대역이 한 줄에 못 들어가 위아래로 쌓인다.
        ly = z["title_y"] - (13 if z["stacked"] else 0)
        out.append(f'<rect class="zone" x="{z["x"]:.0f}" y="{z["y"]:.0f}" '
                   f'width="{z["w"]:.0f}" height="{z["h"]:.0f}" rx="10"/>'
                   f'<text class="zone-label" x="{z["x"]+14:.0f}" y="{ly:.0f}">'
                   f'{escape(z["label"])}</text>')
        if z["cidr"]:
            anchor = "start" if z["stacked"] else "end"
            cx = z["x"] + 14 if z["stacked"] else z["x"] + z["w"] - 14
            out.append(f'<text class="zone-cidr" x="{cx:.0f}" y="{z["title_y"]:.0f}" '
                       f'text-anchor="{anchor}">{escape(z["cidr"])}</text>')

    # ---- 링크. 직선과 직각으로만 꺾는다 — 곡선은 어디서 갈라지는지 안 보인다.
    for lk in d["links"]:
        path = "M" + " L".join(f"{x:.0f},{y:.0f}" for x, y in lk["points"])
        out.append(f'<path class="link" d="{path}"/>')
        if not show_labels:
            continue
        for i, line in enumerate(reversed(lk["label"])):      # 선 위로 쌓아 올린다
            out.append(f'<text class="seg" x="{lk["lx"]:.0f}" '
                       f'y="{lk["ly"] - 7 - i * 12:.0f}">{escape(line)}</text>')
        for e in lk["ends"]:
            out.append(f'<text class="if" x="{e["tx"]:.0f}" y="{e["ty"]:.0f}" '
                       f'text-anchor="{e["anchor"]}">{escape(e["if"])}</text>')

    # ---- 장비
    for n in d["nodes"]:
        cls = "node" + (" hl" if n["name"] in hl else "")
        body = GLYPH_FN[n["shape"]](n["gw"], n["gh"])
        out.append(
            f'<g class="{cls}" data-node="{escape(n["name"])}" '
            f'style="--f:{FILL[n["role"]]}">'
            f'<g transform="translate({n["gx"]:.0f},{n["gy"]:.0f})">{body}</g>'
            f'<text class="n-name" x="{n["cx"]:.0f}" y="{n["y"]+T.GLYPH_H+13:.0f}">'
            f'{escape(n["name"])}</text>'
            + "".join(f'<text class="n-cap" x="{n["cx"]:.0f}" '
                      f'y="{n["y"]+T.GLYPH_H+28+i*12:.0f}">{escape(line)}</text>'
                      for i, line in enumerate(n["caption"]))
            + f'<title>{escape(n["name"])} — {escape(n["desc"])}</title></g>')

    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    print(render(sys.argv[1] if len(sys.argv) > 1 else "m10"))
