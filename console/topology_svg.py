"""
토폴로지를 인라인 SVG 로 그린다.

Mermaid.js 를 쓰지 않는 이유: 랩 서버는 폐쇄망일 수 있고, CDN 에 의존하면 그림이 안 뜬다.
설계 데이터(labdesign)에서 직접 그리면 의존성이 0 이 된다.
"""
import sys
from collections import deque
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L

ROLE_STYLE = {
    "host":   {"fill": "var(--n-host)",   "shape": "round", "label": "PC"},
    "switch": {"fill": "var(--n-switch)", "shape": "rect",  "label": "SW"},
    "router": {"fill": "var(--n-router)", "shape": "circle", "label": "R"},
    "server": {"fill": "var(--n-server)", "shape": "round", "label": "SRV"},
    "edge":   {"fill": "var(--n-edge)",   "shape": "hex",   "label": "FW"},
}
ZONE_LABEL = {"site-a": "Site-A · 지사", "core": "Core · 백본",
              "site-b": "Site-B · 데이터센터", "edge": "Edge · 인터넷 경계"}

COL_W, ROW_H, PAD_X, PAD_Y = 150, 74, 70, 54
BOX_W, BOX_H = 84, 34


def _layout(stage):
    """왼쪽(사용자) → 오른쪽(외부) 흐름으로 열을 잡는다. 열 = 시작점으로부터의 홉 수."""
    nodes = [n for n in L.TOPO["nodes"] if L.stage_le(n["stage"], stage)]
    names = {n["name"] for n in nodes}
    adj = {n: set() for n in names}
    for br, (a, _), (b, _) in L.links_at(1, stage):
        adj[a].add(b)
        adj[b].add(a)

    starts = [n["name"] for n in nodes if n["zone"] == "site-a" and n["role"] == "host"] \
        or [nodes[0]["name"]]
    depth = {s: 0 for s in starts}
    q = deque(starts)
    while q:
        cur = q.popleft()
        for nb in sorted(adj[cur]):
            if nb not in depth:
                depth[nb] = depth[cur] + 1
                q.append(nb)
    for n in names:                      # 고립 노드
        depth.setdefault(n, 0)

    cols = {}
    for n in sorted(names, key=lambda x: (depth[x], x)):
        cols.setdefault(depth[n], []).append(n)

    pos, maxrows = {}, max((len(v) for v in cols.values()), default=1)
    for c, members in cols.items():
        for i, n in enumerate(members):
            y = PAD_Y + (maxrows - len(members)) * ROW_H / 2 + i * ROW_H
            pos[n] = (PAD_X + c * COL_W, y)
    w = PAD_X * 2 + max(cols) * COL_W + BOX_W
    h = PAD_Y * 2 + (maxrows - 1) * ROW_H + BOX_H
    return nodes, pos, w, h


def render(stage="m10", highlight=None, show_labels=True):
    nodes, pos, w, h = _layout(stage)
    by_name = {n["name"]: n for n in nodes}
    out = [f'<svg viewBox="0 0 {w:.0f} {h:.0f}" class="topo" '
           f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="랩 토폴로지">']

    # 구역 배경
    zones = {}
    for n in nodes:
        x, y = pos[n["name"]]
        z = zones.setdefault(n["zone"], [x, y, x, y])
        z[0], z[1] = min(z[0], x), min(z[1], y)
        z[2], z[3] = max(z[2], x), max(z[3], y)
    for zone, (x0, y0, x1, y1) in zones.items():
        out.append(
            f'<rect class="zone" x="{x0-24:.0f}" y="{y0-30:.0f}" '
            f'width="{x1-x0+BOX_W+48:.0f}" height="{y1-y0+BOX_H+44:.0f}" rx="10"/>'
            f'<text class="zone-label" x="{x0-16:.0f}" y="{y0-14:.0f}">'
            f'{escape(ZONE_LABEL.get(zone, zone))}</text>')

    # 링크
    for br, (a, ai), (b, bi) in L.links_at(1, stage):
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        x1, y1 = ax + BOX_W, ay + BOX_H / 2
        x2, y2 = bx, by + BOX_H / 2
        if bx < ax:
            x1, x2 = ax, bx + BOX_W
        mid = (x1 + x2) / 2
        out.append(f'<path class="link" d="M{x1:.0f},{y1:.0f} C{mid:.0f},{y1:.0f} '
                   f'{mid:.0f},{y2:.0f} {x2:.0f},{y2:.0f}"/>')
        if show_labels:
            out.append(f'<text class="if" x="{x1+6:.0f}" y="{y1-5:.0f}">{escape(ai)}</text>'
                       f'<text class="if if-r" x="{x2-6:.0f}" y="{y2-5:.0f}">{escape(bi)}</text>')

    # 노드
    for n in nodes:
        name = n["name"]
        x, y = pos[name]
        st = ROLE_STYLE[n["role"]]
        cls = "node" + (" hl" if highlight and name in highlight else "")
        rx = 17 if st["shape"] in ("round", "circle") else 6
        out.append(
            f'<g class="{cls}" data-node="{escape(name)}">'
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{BOX_W}" height="{BOX_H}" rx="{rx}" '
            f'style="fill:{st["fill"]}"/>'
            f'<text class="n-name" x="{x+BOX_W/2:.0f}" y="{y+BOX_H/2+1:.0f}">{escape(name)}</text>'
            f'<title>{escape(name)} — {escape(n.get("desc", ""))}</title></g>')

    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "m10"
    print(render(st))
