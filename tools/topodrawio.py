#!/usr/bin/env python3
"""
같은 배치에서 .drawio 파일을 만든다 — draw.io 로 열어 손보거나 내보내기 위한 것.

화면에 뜨는 그림은 console/topology_svg.py 가 그린다. 이 파일은 그것과 경쟁하지
않는다. 배치(tools/topolayout.py)가 하나이므로 둘은 항상 같은 그림을 그린다.

▸ 왜 좌표를 늘려 쓰는가
  draw.io 의 네트워크 기호는 제 크기가 정해져 있다 (라우터 100x30, 서버 90x100).
  화면용 글리프(42x42 등)보다 커서, 같은 좌표에 놓으면 서로 겹친다. 배치를
  그대로 두고 좌표만 2.2배로 늘리면 통로가 함께 넓어져 기호가 편히 들어간다.

usage:  python3 tools/topodrawio.py m10 > topology-m10.drawio
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import topolayout as T

S = 2.2                       # 화면용 좌표 -> draw.io 좌표

# 스타일은 draw.io 의 mxgraph.networks 규약을 그대로 쓴다 — 파일을 열었을 때
# 다른 사람이 만든 구성도와 같은 모습이어야 손보기 쉽다.
BASE = ("verticalAlign=top;verticalLabelPosition=bottom;labelPosition=center;align=center;"
        "html=1;outlineConnect=0;gradientColor=none;strokeWidth=2;")
SHAPE = {"pc":       ("mxgraph.networks.pc", 100, 70),
         "switch":   ("mxgraph.networks.switch", 100, 30),
         "router":   ("mxgraph.networks.router", 100, 30),
         "server":   ("mxgraph.networks.server", 90, 100),
         "firewall": ("mxgraph.networks.firewall", 100, 100),
         "cloud":    ("mxgraph.networks.cloud", 90, 50)}
# 역할색은 화면과 같은 뜻을 지킨다 (draw.io 기본 팔레트에서 고른 값).
COLOR = {"host":   ("#dae8fc", "#6c8ebf"), "switch": ("#d5e8d4", "#82b366"),
         "router": ("#fff2cc", "#d6b656"), "server": ("#e1d5e7", "#9673a6"),
         "edge":   ("#f8cecc", "#b85450")}
ZONE_STYLE = ("rounded=1;dashed=1;fillColor=#f9f9f9;strokeColor=#999999;"
              "verticalAlign=top;align=left;spacingLeft=12;fontStyle=1;fontSize=15;"
              "fontColor=#555555;container=1;collapsible=0;pointerEvents=0;")
EDGE_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
              "html=1;endArrow=none;strokeWidth=2;strokeColor=#666666;"
              "labelBackgroundColor=#ffffff;fontSize=12;fontColor=#333333;")


def _side(pt, n):
    """끝점이 글리프의 어느 변에 붙었는가 -> draw.io 의 exit/entry 비율."""
    x, y = pt
    gx, gy, gw, gh = n["gx"], n["gy"], n["gw"], n["gh"]
    if abs(x - gx) < 1.5:
        return 0.0, round(min(max((y - gy) / gh, 0), 1), 3)
    if abs(x - (gx + gw)) < 1.5:
        return 1.0, round(min(max((y - gy) / gh, 0), 1), 3)
    f = round(min(max((x - gx) / gw, 0), 1), 3)
    return f, (0.0 if abs(y - gy) < 1.5 else 1.0)


def drawio(stage="m10"):
    d = T.layout(stage)
    by_name = {n["name"]: n for n in d["nodes"]}
    cells, nid = [], [2]

    def cell(xml):
        cells.append(xml)

    def take():
        nid[0] += 1
        return f"n{nid[0]}"

    zone_id = {}
    for z in d["zones"]:
        i = take()
        zone_id[z["id"]] = i
        title = z["label"] + (f"  —  {z['cidr']}" if z["cidr"] else "")
        cell(f'<mxCell id="{i}" value={quoteattr(title)} style="{ZONE_STYLE}" '
             f'vertex="1" parent="1">'
             f'<mxGeometry x="{z["x"]*S:.0f}" y="{z["y"]*S:.0f}" '
             f'width="{z["w"]*S:.0f}" height="{z["h"]*S:.0f}" as="geometry"/></mxCell>')

    node_id = {}
    for n in d["nodes"]:
        i = take()
        node_id[n["name"]] = i
        shape, w, h = SHAPE[n["shape"]]
        fill, stroke = COLOR[n["role"]]
        z = next(b for b in d["zones"] if b["id"] == n["zone"])
        # 구역 상자의 자식이므로 좌표는 상자 기준 상대값이다.
        x = (n["cx"] - z["x"]) * S - w / 2
        y = (n["cy"] - z["y"]) * S - h / 2
        label = "&#xa;".join([n["name"]] + n["caption"])
        cell(f'<mxCell id="{i}" value="{label}" '
             f'style="shape={shape};{BASE}fillColor={fill};strokeColor={stroke};" '
             f'vertex="1" parent="{zone_id[n["zone"]]}">'
             f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" '
             f'as="geometry"/></mxCell>')

    for lk in d["links"]:
        i = take()
        A, B = by_name[lk["ends"][0]["node"]], by_name[lk["ends"][1]["node"]]
        ex, ey = _side(lk["points"][0], A)
        nx, ny = _side(lk["points"][-1], B)
        text = "&#xa;".join(lk["label"] + [f'{lk["ends"][0]["if"]} ↔ {lk["ends"][1]["if"]}'])
        cell(f'<mxCell id="{i}" value="{text}" '
             f'style="{EDGE_STYLE}exitX={ex};exitY={ey};exitDx=0;exitDy=0;'
             f'entryX={nx};entryY={ny};entryDx=0;entryDy=0;" '
             f'edge="1" parent="1" source="{node_id[A["name"]]}" '
             f'target="{node_id[B["name"]]}">'
             f'<mxGeometry relative="1" as="geometry"/></mxCell>')

    body = "\n        ".join(cells)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mxfile host="my-network-lab">\n'
            f'  <diagram name={quoteattr(stage.upper() + " 랩 토폴로지")}>\n'
            f'    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" page="1" '
            f'pageWidth="1169" pageHeight="826" math="0" shadow="0">\n'
            f'      <root>\n'
            f'        <mxCell id="0"/>\n'
            f'        <mxCell id="1" parent="0"/>\n'
            f'        {body}\n'
            f'      </root>\n'
            f'    </mxGraphModel>\n'
            f'  </diagram>\n'
            f'</mxfile>\n')


if __name__ == "__main__":
    sys.stdout.write(drawio(sys.argv[1] if len(sys.argv) > 1 else "m10"))
