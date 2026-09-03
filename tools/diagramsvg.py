"""
교재 안의 작은 구성도를 인라인 SVG 로 그린다. 배치는 tools/diagram.py 가 계산한다.

웹 콘솔과 오프라인 문서(make modules)가 **같은 그림**을 써야 하므로 tools/ 에 둔다.

토폴로지와 같은 이유로 CDN 을 쓰지 않고, 같은 이유로 색을 CSS 변수로 둔다 —
랩 서버는 폐쇄망일 수 있고, 화면은 화이트와 다크를 오간다.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diagram as D
import devices


def _path(pts):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def render(spec, title="", standalone=False):
    """standalone: 파일로 나가는 그림. 콘솔 CSS 가 없으므로 색 규칙을 심는다."""
    s = D.build(spec)
    out = [f'<svg viewBox="0 0 {s.w} {s.h}" width="{s.w}" height="{s.h}" class="dia" '
           f'xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="{escape(title or s.title or "구성도")}">',
           # 화살촉은 한 번만 정의하고 선들이 가리킨다.
           '<defs><marker id="dh" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L8,4 L0,8 z" class="head"/></marker></defs>']
    if standalone:
        out.append(devices.style_tag())

    for b in s.boxes:
        cls = ("dbox " + b["tone"]).strip()
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        # 플로우차트는 **모양이 종류를 말한다** — 마름모는 갈리는 자리,
        # 점은 갈래가 다시 만나는 자리다. 색으로 구분하면 인쇄에서 사라진다.
        if b.get("shape") == "diamond":
            d = (f'M{x + w / 2:.1f},{y:.1f} L{x + w:.1f},{y + h / 2:.1f} '
                 f'L{x + w / 2:.1f},{y + h:.1f} L{x:.1f},{y + h / 2:.1f} Z')
            out.append(f'<path class="{cls}" d="{d}"/>')
        elif b.get("shape") == "dot":
            out.append(f'<circle class="{cls}" cx="{x + w / 2:.1f}" '
                       f'cy="{y + h / 2:.1f}" r="{w / 2:.1f}"/>')
        else:
            out.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" '
                       f'width="{w:.1f}" height="{h:.1f}" rx="{b["rx"]}"/>')
    # 장비는 토폴로지와 같은 그림 · 같은 색으로 그린다.
    for g in s.glyphs:
        out.append(f'<g class="node" style="--f:{devices.FILL[g["role"]]}">'
                   f'<g transform="translate({g["x"]:.1f},{g["y"]:.1f})">'
                   f'{devices.body(g["shape"], g["w"], g["h"])}</g></g>')
    for l in s.lines:
        arrow = ' marker-end="url(#dh)"' if l["arrow"] == "end" else ""
        out.append(f'<path class="{l["cls"]}" d="{_path(l["pts"])}"{arrow}/>')
    for t in s.texts:
        out.append(f'<text class="{t["cls"]}" x="{t["x"]:.1f}" y="{t["y"]:.1f}" '
                   f'text-anchor="{t["anchor"]}" font-size="{t["size"]}">'
                   f'{escape(t["s"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    print(render(sys.stdin.read()))
