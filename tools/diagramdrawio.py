#!/usr/bin/env python3
"""
교재 구성도를 .drawio 로 내보낸다. 배치는 tools/diagram.py 가 계산한다.

화면 SVG 와 같은 배치에서 나오므로 둘이 어긋날 일이 없다. 상자 · 글자 · 선
셋뿐이라 토폴로지(topodrawio.py)보다 단순하다 — 여기서는 도형 이름을 쓰지
않고 좌표 그대로 옮긴다. 열어서 손보면 그때부터는 draw.io 의 그림이다.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagram as D
import devices

S = 1.6                      # 화면용 좌표 -> draw.io 좌표 (글자가 작지 않게)
TONE = {"":     ("#ffffff", "#b3b3b3"),
        "ok":   ("#eaf4ff", "#6c8ebf"),
        "warn": ("#fff4e0", "#d6b656"),
        "node": ("#f2f2f2", "#999999"),
        "soft": ("#fafafa", "#cccccc"),
        "dot":  ("#666666", "#666666")}


def drawio(spec, title="구성도"):
    sc = D.build(spec)
    cells, n = [], [1]

    def nid():
        n[0] += 1
        return f"c{n[0]}"

    for b in sc.boxes:
        fill, stroke = TONE.get(b["tone"], TONE[""])
        # 플로우차트의 마름모·합류점. draw.io 에도 같은 모양으로 나가야
        # 열어 본 사람이 화면에서 본 그림과 같은 것으로 읽는다.
        shape = {"diamond": "shape=rhombus;", "dot": "ellipse;"}.get(b.get("shape"), "")
        cells.append(
            f'<mxCell id="{nid()}" value="" style="{shape}'
            f'rounded={1 if b["rx"] and not shape else 0};'
            f'arcSize={min(50, int(b["rx"] * 6))};whiteSpace=wrap;html=1;'
            f'fillColor={fill};strokeColor={stroke};" vertex="1" parent="1">'
            f'<mxGeometry x="{b["x"]*S:.0f}" y="{b["y"]*S:.0f}" '
            f'width="{b["w"]*S:.0f}" height="{b["h"]*S:.0f}" as="geometry"/></mxCell>')

    # 장비는 draw.io 의 네트워크 기호로 나간다 — 토폴로지 .drawio 와 같은 모양이다.
    for g in sc.glyphs:
        stencil, _, _ = devices.DRAWIO_SHAPE[g["shape"]]
        fill, stroke = devices.HEX[g["role"]]
        cells.append(
            f'<mxCell id="{nid()}" value="" style="shape={stencil};html=1;'
            f'outlineConnect=0;gradientColor=none;strokeWidth=2;'
            f'fillColor={fill};strokeColor={stroke};" vertex="1" parent="1">'
            f'<mxGeometry x="{g["x"]*S:.0f}" y="{g["y"]*S:.0f}" '
            f'width="{g["w"]*S:.0f}" height="{g["h"]*S:.0f}" as="geometry"/></mxCell>')

    for l in sc.lines:
        pts = l["pts"]
        way = "".join(f'<mxPoint x="{x*S:.0f}" y="{y*S:.0f}"/>' for x, y in pts[1:-1])
        dash = ";dashed=1" if "dash" in l["cls"] else ""
        color = "#d79b00" if "warn" in l["cls"] else "#666666"
        end = "classic" if l["arrow"] == "end" else "none"
        cells.append(
            f'<mxCell id="{nid()}" value="" style="edgeStyle=orthogonalEdgeStyle;'
            f'rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow={end};'
            f'strokeColor={color};strokeWidth=2{dash};" edge="1" parent="1">'
            f'<mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{pts[0][0]*S:.0f}" y="{pts[0][1]*S:.0f}" as="sourcePoint"/>'
            f'<mxPoint x="{pts[-1][0]*S:.0f}" y="{pts[-1][1]*S:.0f}" as="targetPoint"/>'
            f'{f"<Array as=\"points\">{way}</Array>" if way else ""}'
            f'</mxGeometry></mxCell>')

    for t in sc.texts:
        align = {"start": "left", "middle": "center", "end": "right"}[t["anchor"]]
        bold = 1 if " b" in f' {t["cls"]} ' or t["cls"].endswith("b") else 0
        color = "#333333"
        if "m" in t["cls"].split():
            color = "#767676"
        if "warn" in t["cls"].split():
            color = "#b06000"
        w = D._tw(t["s"], t["size"]) * S + 8
        cells.append(
            f'<mxCell id="{nid()}" value={quoteattr(t["s"])} '
            f'style="text;html=1;align={align};verticalAlign=middle;'
            f'fontSize={t["size"]*S:.0f};fontColor={color};fontStyle={bold};" '
            f'vertex="1" parent="1"><mxGeometry '
            f'x="{(t["x"]*S - (w if align == "right" else w/2 if align == "center" else 0)):.0f}" '
            f'y="{(t["y"] - t["size"]) * S - 4:.0f}" '
            f'width="{w:.0f}" height="{t["size"]*S + 10:.0f}" as="geometry"/></mxCell>')

    body = "\n        ".join(cells)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mxfile host="my-network-lab">\n'
            f'  <diagram name={quoteattr(title)}>\n'
            f'    <mxGraphModel dx="1000" dy="700" grid="1" gridSize="10" page="1" '
            f'pageWidth="1169" pageHeight="826" math="0" shadow="0">\n'
            f'      <root>\n        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n'
            f'        {body}\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n')


if __name__ == "__main__":
    sys.stdout.write(drawio(sys.stdin.read()))
