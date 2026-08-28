#!/usr/bin/env python3
"""
장비 프리셋 — 구성도에 나오는 장비의 모양 · 색 · 크기를 한 곳에서 정한다.

▸ 왜 파일 하나로 모았나
  같은 `r1` 이 랩 지도에서는 동그란 라우터였는데 교재의 작은 구성도에서는 회색
  네모였다. 교육생 눈에는 다른 장비로 보인다. 모양과 색을 정하는 자리가 네 곳
  (topolayout · topology_svg · diagram · topodrawio) 으로 흩어져 있었기 때문이다.
  여기 하나만 고치면 화면 · 교재 · draw.io 가 함께 바뀐다.

▸ 색이 아니라 실루엣이 먼저다
  흑백으로 인쇄해도, 색약이어도 라우터와 PC 가 구분돼야 한다. 색은 거들 뿐이다.

▸ 이름으로 역할을 알아낸다
  큰 토폴로지는 설계 파일에 role 이 적혀 있지만, 교재의 작은 구성도는 이름만
  적는다. `r1` `sw2` `pc1` `web` 같은 이 랩의 작명 규칙에서 역할을 끌어낸다.
  규칙에 걸리지 않는 이름(예: "서버망 DHCP")은 장비가 아니라 개념 상자이므로
  글자만 든 네모로 남는다 — 억지로 장비 그림을 붙이지 않는다.
"""
import re

# ------------------------------------------------------------ 역할 -> 모양·색
#  role 은 설계 파일(design/topology.yml)이 쓰는 낱말을 그대로 쓴다.
ROLE = {
    "host":   {"shape": "pc",       "var": "--n-host",   "hex": ("#dae8fc", "#6c8ebf")},
    "switch": {"shape": "switch",   "var": "--n-switch", "hex": ("#d5e8d4", "#82b366")},
    "router": {"shape": "router",   "var": "--n-router", "hex": ("#fff2cc", "#d6b656")},
    "server": {"shape": "server",   "var": "--n-server", "hex": ("#e1d5e7", "#9673a6")},
    "edge":   {"shape": "firewall", "var": "--n-edge",   "hex": ("#f8cecc", "#b85450")},
    "cloud":  {"shape": "cloud",    "var": "--n-cloud",  "hex": ("#f5f5f5", "#999999")},
}
ROLE_SHAPE = {r: v["shape"] for r, v in ROLE.items()}
FILL = {r: f'var({v["var"]})' for r, v in ROLE.items()}
HEX = {r: v["hex"] for r, v in ROLE.items()}

# 글리프는 모두 GLYPH_H 안에 세로 가운데로 놓인다 -> 링크가 붙는 높이가 같다.
GLYPH = {"pc": (48, 38), "switch": (64, 44), "router": (42, 42),
         "server": (34, 42), "firewall": (48, 40), "cloud": (60, 36)}
#  교재의 작은 구성도는 자리가 좁다. 실루엣은 같고 크기만 줄인다.
GLYPH_SM = {"pc": (36, 29), "switch": (48, 33), "router": (32, 32),
            "server": (26, 32), "firewall": (36, 30), "cloud": (46, 27)}

# draw.io 의 네트워크 기호. 제 크기가 정해져 있어 화면 글리프보다 크다.
DRAWIO_SHAPE = {"pc":       ("mxgraph.networks.pc", 100, 70),
                "switch":   ("mxgraph.networks.switch", 100, 30),
                "router":   ("mxgraph.networks.router", 100, 30),
                "server":   ("mxgraph.networks.server", 90, 100),
                "firewall": ("mxgraph.networks.firewall", 100, 100),
                "cloud":    ("mxgraph.networks.cloud", 90, 50)}

# ------------------------------------------------------------------ 이름 규칙
#  이 랩의 작명 규칙. 앞 낱말 하나만 본다 ("pc1 :41000" 도 pc1 이다).
NAME_RULES = [
    (re.compile(r"^r\d+$", re.I), "router"),
    (re.compile(r"^sw\d*$", re.I), "switch"),
    (re.compile(r"^pc\d*$", re.I), "host"),
    (re.compile(r"^(web|dns|srv|app|db|ftp|http|mail|smtp)\d*$", re.I), "server"),
    (re.compile(r"^(edge|fw)\d*$", re.I), "edge"),
    (re.compile(r"^(inet|internet)$", re.I), "cloud"),
]
#  한국어로 적은 장비 이름. 낱말 전체가 같을 때만 친다 — "서버망" 은 서버가 아니다.
ALIAS = {"라우터": "router", "스위치": "switch", "피시": "host", "피씨": "host",
         "서버": "server", "방화벽": "edge", "게이트웨이": "router", "인터넷": "cloud"}


def role_of(name, explicit=None):
    """구성도에 적힌 이름 -> 역할. 장비로 볼 수 없으면 None."""
    if explicit:
        return explicit if explicit in ROLE else None
    head = str(name or "").strip().split()[0].strip(":·,()[]") if str(name or "").strip() else ""
    if head in ALIAS:
        return ALIAS[head]
    for pat, role in NAME_RULES:
        if pat.match(head):
            return role
    return None


def shape_of(name, explicit=None):
    role = role_of(name, explicit)
    return ROLE[role]["shape"] if role else None


# --------------------------------------------------------------- 장비 그림
#  각 함수는 (0,0) 기준 w x h 안에 그린다. 채움 · 선은 바깥에서 group 이 준다.
def _pc(w, h):
    sc, nk, bs = h - 10, 5, 5          # 화면 / 목 / 받침
    return (f'<rect x="0" y="0" width="{w}" height="{sc}" rx="2.5"/>'
            f'<rect x="{w/2-5:.1f}" y="{sc}" width="10" height="{nk}" class="sub"/>'
            f'<rect x="{w/2-15:.1f}" y="{sc+nk}" width="30" height="{bs}" rx="2" class="sub"/>')


def _switch(w, h):
    body, port = h - 10, 7
    n = max(3, int((w - 4) // 11))
    ports = "".join(f'<rect x="{2+i*11}" y="{body+3}" width="7" height="{port}" rx="1" class="sub"/>'
                    for i in range(n))
    return f'<rect x="0" y="0" width="{w}" height="{body}" rx="3"/>{ports}'


def _router(w, h):
    r = c = w / 2
    a = w / 4                          # 화살표 크기는 글리프에 따라 줄어든다
    return (f'<circle cx="{c}" cy="{c}" r="{r}"/>'
            f'<path class="ico" d="M{c-a-1:.1f},{c-a/2:.1f} H{c+a*0.7:.1f} '
            f'M{c+a*0.3:.1f},{c-a*0.9:.1f} L{c+a*0.9:.1f},{c-a/2:.1f} '
            f'L{c+a*0.3:.1f},{c-a*0.1:.1f}"/>'
            f'<path class="ico" d="M{c+a+1:.1f},{c+a/2:.1f} H{c-a*0.7:.1f} '
            f'M{c-a*0.3:.1f},{c+a*0.1:.1f} L{c-a*0.9:.1f},{c+a/2:.1f} '
            f'L{c-a*0.3:.1f},{c+a*0.9:.1f}"/>')


def _server(w, h):
    n = 3 if h >= 36 else 2
    slots = "".join(f'<rect x="{w*0.18:.1f}" y="{7+i*8}" width="{w*0.64:.1f}" '
                    f'height="4" rx="1" class="sub"/>' for i in range(n))
    return (f'<rect x="0" y="0" width="{w}" height="{h}" rx="3"/>{slots}'
            f'<circle cx="{w*0.32:.1f}" cy="{h-8}" r="2" class="sub"/>'
            f'<circle cx="{w*0.56:.1f}" cy="{h-8}" r="2" class="sub"/>')


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
    sx, sy = w / 60, h / 36            # 원본은 60x36 에 그려져 있다
    p = [(8, 33), (1, 33), (0, 23), (8, 21), (8, 10), (22, 6), (28, 14),
         (33, 7), (45, 9), (45, 18), (55, 17), (58, 28), (52, 33)]
    q = [(x * sx, y * sy) for x, y in p]
    return (f'<path d="M{q[0][0]:.1f},{q[0][1]:.1f} '
            f'C{q[1][0]:.1f},{q[1][1]:.1f} {q[2][0]:.1f},{q[2][1]:.1f} {q[3][0]:.1f},{q[3][1]:.1f} '
            f'C{q[4][0]:.1f},{q[4][1]:.1f} {q[5][0]:.1f},{q[5][1]:.1f} {q[6][0]:.1f},{q[6][1]:.1f} '
            f'C{q[7][0]:.1f},{q[7][1]:.1f} {q[8][0]:.1f},{q[8][1]:.1f} {q[9][0]:.1f},{q[9][1]:.1f} '
            f'C{q[10][0]:.1f},{q[10][1]:.1f} {q[11][0]:.1f},{q[11][1]:.1f} {q[12][0]:.1f},{q[12][1]:.1f} Z"/>')


GLYPH_FN = {"pc": _pc, "switch": _switch, "router": _router,
            "server": _server, "firewall": _firewall, "cloud": _cloud}


def body(shape, w, h):
    """(0,0) 기준 w x h 짜리 장비 그림의 SVG 조각."""
    return GLYPH_FN[shape](w, h)


# ------------------------------------------------------- 파일로 빠져나가는 SVG
#  콘솔 안에서는 페이지의 app.css 가 그림에 색을 준다. 그러나 오프라인 교재
#  (dist/modules/**/diagrams/*.svg)나 /topology.svg 를 직접 열면 그 CSS 가 없어
#  전부 검게 칠해진 그림이 나온다. 색 규칙을 두 벌 적지 않기 위해, 스타일시트
#  한 벌에서 SVG 에 해당하는 규칙만 뽑아 파일 안에 넣는다.
_CSS = None
_RULE = re.compile(r"(?<=[};])?\s*([^{}]+)\{([^{}]*)\}")


def svg_css():
    """app.css 에서 SVG 용 규칙만 추린 문자열. 파일로 나가는 그림에 심는다."""
    global _CSS
    if _CSS is None:
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "console/static/app.css").read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        #  @media 는 중괄호가 겹쳐 있어 아래 규칙 하나짜리 훑기가 헝클어진다.
        #  파일로 나가는 그림에는 화면 폭 규칙이 필요 없으므로 통째로 걷어낸다.
        src = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", src)
        out = []
        for sel, body in _RULE.findall(src):
            sel = " ".join(sel.split())
            if sel == ":root" or "svg.topo" in sel or "svg.dia" in sel:
                out.append(f"{sel}{{{' '.join(body.split())}}}")
        _CSS = "\n".join(out)
    return _CSS


def style_tag():
    return f"<style>{svg_css()}</style>"
