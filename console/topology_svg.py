"""
토폴로지를 인라인 SVG 로 그린다. 배치는 tools/topolayout.py 가 계산한다.

Mermaid.js 를 쓰지 않는 이유: 랩 서버는 폐쇄망일 수 있고, CDN 에 의존하면 그림이 안 뜬다.
설계 데이터에서 직접 그리면 의존성이 0 이 된다.

▸ 왜 장비마다 모양이 다른가
  예전에는 전부 같은 둥근 사각형이었고 채움색만 달랐다. 그러면 인쇄하거나
  색약이면 라우터와 PC 가 같은 그림이 된다. 실루엣으로 구분하면 색이 없어도 읽힌다.
  그 실루엣과 색은 tools/devices.py 가 정한다 — 교재의 작은 구성도도 같은 것을 본다.

▸ 색은 CSS 변수로 둔다
  화면이 밝은 모드 · 어두운 모드를 오가기 때문. 색을 SVG 에 박으면 어두운 모드에서
  흰 상자가 뜬다.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import topolayout as T
import devices

#  모양 · 색은 장비 프리셋(tools/devices.py)이 정한다. 교재 안의 작은 구성도도
#  같은 프리셋을 보므로, 랩 지도의 r1 과 교재의 r1 은 같은 그림이다.
FILL = devices.FILL
GLYPH_FN = devices.GLYPH_FN


def render(stage="m10", highlight=None, show_labels=True, standalone=False):
    d = T.layout(stage)
    hl = set(highlight or ())
    # 원본 크기를 그대로 들고 나간다. 칸이 좁다고 줄이면 글자가 못 읽는 크기가
    # 되므로, 줄이는 대신 감싼 상자가 가로로 넘긴다 (눌러서 크게도 볼 수 있다).
    out = [f'<svg viewBox="0 0 {d["w"]} {d["h"]}" width="{d["w"]}" height="{d["h"]}" '
           f'class="topo" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="랩 토폴로지 ({stage.upper()} 단계)">']
    if standalone:                       # 파일로 열면 콘솔 CSS 가 없다
        out.append(devices.style_tag())

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
