#!/usr/bin/env python3
"""
토폴로지 배치 엔진 — "어디에 놓을 것인가" 만 계산한다. 그리지는 않는다.

같은 배치에서 두 가지가 나온다.
  console/topology_svg.py    화면 · 교재 · 인쇄용 인라인 SVG
  tools/render-drawio.py     draw.io 에서 손볼 수 있는 .drawio

▸ 왜 구역을 먼저 놓는가
  예전 배치는 구역을 무시하고 홉 수(BFS)로만 열을 잡았다. 그 결과 M10 부터
  edge 구역 상자(x 796~1078)가 site-b 상자(646~1078) 안에 통째로 들어가
  라벨이 겹쳐 찍혔고, sw2 는 남의 구역 안에, inet 은 서버들 사이에 놓였다.
  구역을 먼저 배치하고 그 안에서만 열을 잡으면 겹침이 구조적으로 불가능해진다.

▸ 왜 연결점을 흩뿌리는가
  한 장비에 링크가 여럿이면 전부 같은 점에 붙는다. 그러면 선이 포개지고
  인터페이스 이름이 같은 자리에 겹쳐 찍힌다 — pc1 의 eth1 과 pc2 의 eth2 가
  sw1 왼쪽 한 점에서 만나던 문제. 변마다 나눠 붙이고, 꺾는 x 도 어긋나게 둔다.

▸ 왜 캔버스를 줄이지 않는가
  대역 이름이 들어가려면 통로가 넓어야 한다. 좁혀서 본문 칸에 맞추면 글자가
  4~6px 이 되어 확대해야만 읽힌다. 캔버스는 필요한 만큼 쓰고, 화면이 좁으면
  줄이는 대신 가로로 넘긴다 (그림은 눌러서 크게 볼 수 있다).
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L
import devices

# --------------------------------------------------------------------- 구역
ZONE_LABEL = {"site-a": "Site-A · 지사", "core": "Core · 백본",
              "site-b": "Site-B · 데이터센터", "edge": "Edge · 인터넷 경계"}
# 구역 요약 대역 (ipam 의 계층 배분). 교재 C2 가 설명하는 값이라 그림에 적는다.
ZONE_CIDR_KEY = {"site-a": "site-a", "core": "core-p2p", "site-b": "site-b"}

MAIN_ROW = ["site-a", "core", "site-b"]   # 왼쪽(사용자) -> 오른쪽(서버)
UPPER_ROW = {"edge": "site-b"}            # 이 구역 위에 얹고 오른쪽을 맞춘다
#  인터넷 경계를 위로 올리는 것은 실무 구성도의 관례이기도 하지만, 여기서는
#  r4 가 site-b 의 맨 왼쪽이라 오른쪽에 붙이면 링크가 구역을 가로지르기 때문이다.

# --------------------------------------------------------------------- 치수
PAD = 22                       # 캔버스 바깥 여백
CELL_W, CELL_H = 78, 90        # 노드 한 칸 (그림 + 이름 + 설명 두 줄)
GLYPH_H = 44                   # 칸 안에서 그림이 차지하는 높이. 나머지는 이름.
COL, ROW = 152, 114            # 칸 사이 간격. 통로에 대역 이름이 들어가야 한다.
Z_L, Z_R = 16, 16              # 구역 상자 안쪽 좌우 여백
Z_TITLE, Z_PLAIN = 30, 14      # 제목이 있는 쪽 / 없는 쪽의 위아래 여백
Z_GAP_X, Z_GAP_Y = 72, 34      # 구역 사이 간격
#   가로 간격이 넓다. M5 에서 게이트웨이 둘(r1 · r5)이 코어 둘(r2 · r3)에 모두
#   붙으면서 구역 경계를 지나는 선이 넷이 됐다. 좁으면 링크 대역과 포트 번호가
#   서로 위에 겹쳐 찍힌다 (tools/check-diagram.py 가 잡는다).
#  제목은 본줄 구역은 아래, 윗줄 구역은 위에 단다. 밴드를 넘는 링크가 노드
#  위로 빠져나가기 때문 — 제목을 위에 두면 그 선이 글자를 뚫고 지나간다.

# 역할 -> 그림 종류 · 크기. 장비 프리셋(tools/devices.py) 한 곳에서 정한다 —
# 색이 아니라 실루엣으로 구분한다 (흑백 인쇄 · 색약 대응).
ROLE_SHAPE = devices.ROLE_SHAPE
#  글리프는 모두 GLYPH_H 안에 세로 가운데로 놓인다 -> 링크가 붙는 높이가 같다.
GLYPH = devices.GLYPH


def _depth(nodes, links):
    """site-a 사용자 PC 로부터의 홉 수. 구역 안에서 열 순서를 정하는 데만 쓴다.

    **나중에 덧붙인 배선은 세지 않는다.** 장비가 이미 다 있는데 링크만 늘어난
    경우(M5 에서 r2 · r3 가 r5 에도 붙는 것)까지 세면 그 장비의 열이 앞으로 당겨져
    자리가 바뀐다. 자리는 그 장비가 처음 등장할 때의 배선으로 정한다.
    """
    names = {n["name"] for n in nodes}
    born = {n["name"]: n["stage"] for n in nodes}
    adj = {n: set() for n in names}
    for br, (a, _), (b, _) in links:
        later = max(born[a], born[b], key=lambda s: L.STAGES.index(s))
        if not L.stage_le(br["stage"], later):
            continue
        adj[a].add(b)
        adj[b].add(a)
    starts = [n["name"] for n in nodes if n["zone"] == "site-a" and n["role"] == "host"] \
        or [nodes[0]["name"]]
    d = {s: 0 for s in starts}
    q = deque(starts)
    while q:
        cur = q.popleft()
        for nb in sorted(adj[cur]):
            if nb not in d:
                d[nb] = d[cur] + 1
                q.append(nb)
    for n in names:
        d.setdefault(n, 0)      # 아직 아무 데도 안 붙은 노드
    return d


def _iface(node_name, if_name):
    for n in L.TOPO["nodes"]:
        if n["name"] == node_name:
            for i in n.get("interfaces", []):
                if i["name"] == if_name:
                    return i
    return {}


def _end_octet(node, ifname, ctx):
    """그 인터페이스가 들고 있는 주소의 끝자리. 없으면 None."""
    i = (ctx["ifs"].get(node) or {}).get(ifname) or {}
    v = i.get("ipv4")
    return v.split("/")[0].split(".")[-1] if v else None


def _addr_lines(name, ctx, on_link):
    """그 장비가 **자기 것으로 들고 있는 주소**. 최대 두 줄.

    링크에 대역만 적혀 있으면 "이 주소가 어느 장비 것인가" 를 끝내 알 수 없다.
    실제로 교육생이 `Time to live exceeded` 를 돌려준 주소를 보고도 그것이
    r1 인 줄 몰라 답을 못 적었다. 그림이 말해 줘야 하는 것은 대역이 아니라
    **주소와 장비의 짝**이다.

    on_link 은 링크 끝에 이미 주소가 적히는 (장비, 인터페이스) 들이다.
    거기 적히는 게이트웨이는 여기서 또 적지 않는다 — 통로가 좁아서 두 번 적으면
    옆 링크의 대역 이름을 밀어낸다.

    has_l3 로 막지 않는다. M1 은 라우팅이 없을 뿐 **주소는 있다** — pc1 과 pc2 가
    같은 `/24` 에 있다는 것이 M1 의 전부인데, 그것을 그림이 안 보여 주고 있었다.
    """
    ifs = ctx["ifs"].get(name) or {}
    gw = [i["ipv4"].split("/")[0] for nm, i in ifs.items()
          if i.get("role") == "gateway" and i.get("ipv4") and (name, nm) not in on_link]
    if gw:
        return [f"GW {a}" for a in gw[:2]]
    # 단말은 끝자리(`eth1 .11`)만으로는 대역을 알 수 없다 — 접두 길이까지 적는다.
    own = [i["ipv4"] for i in ifs.values()
           if i.get("role") in ("access", "public", "external-site") and i.get("ipv4")]
    if own:
        return own[:2]
    # 라우터 사이만 잇는 장비는 자기 주소가 P2P 뿐이고 그것은 링크 끝에 적힌다.
    # 여기서는 OSPF 의 router-id 가 되는 루프백을 보여 준다.
    lo = [i["ipv4"].split("/")[0] for i in ifs.values()
          if i.get("role") == "loopback" and i.get("ipv4")]
    return [f"lo {lo[0]}"] if lo else []


def _seg_index(stage):
    """브리지 id -> 그 링크가 속한 대역 문자열."""
    ctx = L.doc_context(1, stage)
    out = {p["bridge"]: p["cidr"] for p in ctx["p2p"].values()}
    t = ctx["public"]["transit"]
    out[t["bridge"]] = t["cidr"]
    return out, ctx


def _link_label(br, a, ai, b, bi, segs, ctx, has_l3):
    """링크에 적을 글줄. 통로가 좁으니 두 줄로 나눠 돌려준다."""
    ia, ib = _iface(a, ai), _iface(b, bi)
    if ia.get("mode") == "trunk" or ib.get("mode") == "trunk":
        v = ia.get("vlan") or ib.get("vlan") or []
        v = v if isinstance(v, list) else [v]
        return ["트렁크 802.1Q", "VLAN " + ",".join(str(x) for x in v)] if v else ["트렁크"]
    if br["id"] in segs:                       # 라우터 사이 P2P
        if br["id"] == ctx["public"]["transit"]["bridge"]:
            return ["공인 구간", segs[br["id"]]]
        return [segs[br["id"]]]
    vid = ia.get("vlan") if ia.get("mode") == "access" else ib.get("vlan")
    if isinstance(vid, int) and has_l3:
        name = ctx["vlans"].get(vid, {}).get("name", "")
        return [f"VLAN{vid} · {name}" if name else f"VLAN{vid}",
                ctx["cidr"].get(f"vlan{vid}", "")]
    return []


def _tw(s, size):
    """글자 폭 추정. 한글은 한 칸, 라틴 · 숫자는 0.6칸으로 본다.

    브라우저가 실제로 재는 값과 다르지만, 라벨이 서로 부딪히는지 가리는 데는
    이 정도면 충분하다 — 정확한 값을 알려면 폰트를 열어야 하고, 그러면 이
    파일이 폰트에 묶인다.
    """
    return sum(size * (1.0 if ord(c) > 0x2000 else 0.6) for c in s)


class _Placer:
    """이미 놓인 것과 부딪히면 옆으로 밀어 놓는다.

    통로가 라벨보다 아주 넉넉하지 않은 한, 계산만으로 자리를 잡으면 반드시
    어딘가는 겹친다. 겹치면 후보 자리를 순서대로 시도하는 편이 확실하다.
    """

    def __init__(self):
        self.boxes = []

    def add(self, b):
        self.boxes.append(b)

    def free(self, b, pad=1.0):
        return not any(b[0] < o[2] - pad and o[0] < b[2] - pad
                       and b[1] < o[3] - pad and o[1] < b[3] - pad for o in self.boxes)

    def fit(self, box_at, offsets):
        """box_at(dy) -> 상자. 비어 있는 첫 dy 를 돌려준다."""
        for dy in offsets:
            if self.free(box_at(dy)):
                self.add(box_at(dy))
                return dy
        self.add(box_at(offsets[0]))
        return offsets[0]


def _y_at(pts, x):
    """경로가 그 x 에서 몇 y 인가. 대역 이름을 선 위에 얹을 때 쓴다."""
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if not min(x1, x2) - 1 <= x <= max(x1, x2) + 1:
            continue
        if abs(y1 - y2) < 1:
            return y1
        if abs(x1 - x2) < 1:
            continue                    # 세로 구간은 x 하나로 y 가 안 정해진다
        return y1 + (y2 - y1) * (x - x1) / (x2 - x1)   # 비스듬한 구간
    return pts[len(pts) // 2][1]


def _between(A, B, placed):
    """두 장비 **사이의 열**에 다른 장비가 서 있는가."""
    lo, hi = (A, B) if A["gx"] < B["gx"] else (B, A)
    return any(lo["gx"] < n["gx"] and n["gx"] + n["gw"] <= hi["gx"] for n in placed)


def _if_spot(pts, first):
    """인터페이스 이름 자리. 선이 나가는 방향을 따라가되 글리프에서 떨어뜨린다."""
    (x1, y1), (x2, y2) = (pts[0], pts[1]) if first else (pts[-1], pts[-2])
    if abs(y1 - y2) <= abs(x1 - x2):            # 가로에 가깝다 -> 선 아래
        right = x2 > x1
        return x1 + (6 if right else -6), y1 + 12, "start" if right else "end"
    d = 24 if y2 > y1 else -24                  # 세로로 나간다 -> 선 오른쪽
    return x1 + 6, y1 + d, "start"


def _side(A, B):
    """A 에서 볼 때 B 가 어느 쪽인가. 연결점을 붙일 변을 고른다."""
    dx, dy = B["cx"] - A["cx"], B["cy"] - A["cy"]
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "bottom" if dy > 0 else "top"


def _anchor(n, side, i, total):
    """변 위에서 i 번째 연결점.

    변의 18~82% 구간에 넓게 펼친다. 균등 분할(1/3, 2/3)로 붙이면 연결점이
    11px 밖에 안 떨어져서, 그 옆에 적는 인터페이스 이름이 제 선이 아니라
    옆 선에 붙어 보인다.
    """
    f = 0.5 if total <= 1 else 0.18 + 0.64 * i / (total - 1)
    x0, y0 = n["gx"], n["gy"]
    x1, y1 = x0 + n["gw"], y0 + n["gh"]
    if side == "left":
        return x0, y0 + (y1 - y0) * f
    if side == "right":
        return x1, y0 + (y1 - y0) * f
    if side == "top":
        return x0 + (x1 - x0) * f, y0
    return x0 + (x1 - x0) * f, y1


def layout(stage="m11"):
    """단계 -> 그림 한 장의 기하 정보. 그리는 쪽은 이 값만 보면 된다."""
    nodes = [n for n in L.TOPO["nodes"] if L.stage_le(n["stage"], stage)]
    links = L.links_at(1, stage)
    if not nodes:
        return {"stage": stage, "w": 100, "h": 60, "zones": [], "nodes": [], "links": []}

    depth = _depth(nodes, links)
    # 링크 끝에 주소가 적히는 인터페이스 — 장비 밑에 또 적지 않기 위해 미리 모은다.
    on_link = {(a, ai) for _, (a, ai), _ in links} | {(b, bi) for _, _, (b, bi) in links}
    has_l3 = L.stage_le("m2", stage)           # M1 은 아직 주소가 없다
    segs, ctx = _seg_index(stage)
    zsum = ctx["zone"]

    # ---- 구역 안 격자: 열 = 구역 안에서 정규화한 홉 수, 행 = 등장 단계 · 이름
    order = {s: i for i, s in enumerate(L.STAGES)}
    grid, zorder = {}, []
    for z in MAIN_ROW + list(UPPER_ROW):
        members = [n for n in nodes if n["zone"] == z]
        if not members:
            continue
        base = min(depth[n["name"]] for n in members)
        cols = {}
        for n in sorted(members, key=lambda n: (order.get(n["stage"], 99), n["name"])):
            cols.setdefault(depth[n["name"]] - base, []).append(n)
        grid[z] = cols
        zorder.append(z)

    main = [z for z in MAIN_ROW if z in grid]
    upper = [z for z in UPPER_ROW if z in grid]

    def box_size(z):
        cols = grid[z]
        nc, nr = max(cols) + 1, max(len(v) for v in cols.values())
        return ((nc - 1) * COL + CELL_W + Z_L + Z_R,
                (nr - 1) * ROW + CELL_H + Z_TITLE + Z_PLAIN, nr,
                Z_TITLE if z in upper else Z_PLAIN)

    def title_stacked(z):
        """제목과 요약 대역이 한 줄에 못 들어가면 위아래로 쌓는다 (core 는 좁다)."""
        cidr = zsum.get(ZONE_CIDR_KEY.get(z, ""), "") if has_l3 else ""
        if not cidr:
            return False
        return _tw(ZONE_LABEL.get(z, z), 12) + _tw(cidr, 11) + 40 > sizes[z][0]

    sizes = {z: box_size(z) for z in grid}
    main_h = max((sizes[z][1] for z in main), default=0)
    upper_h = max((sizes[z][1] for z in upper), default=0)
    main_y = PAD + (upper_h + Z_GAP_Y if upper else 0)

    # ---- 구역 상자. 본줄은 왼쪽부터, 윗줄은 기준 구역에 오른쪽을 맞춘다.
    boxes, x = {}, PAD
    for z in main:
        w, h = sizes[z][:2]
        boxes[z] = [x, main_y + (main_h - h) / 2, w, h]
        x += w + Z_GAP_X
    total_w = x - Z_GAP_X + PAD
    for z in upper:
        w, h = sizes[z][:2]
        anc = boxes.get(UPPER_ROW[z])
        boxes[z] = [(anc[0] + anc[2] - w) if anc else PAD,
                    PAD + (upper_h - h) / 2, w, h]

    # ---- 노드 자리
    placed, by_name = [], {}
    for z in zorder:
        bx, by = boxes[z][0], boxes[z][1]
        nr, top_pad = sizes[z][2], sizes[z][3]
        for c, members in grid[z].items():
            off = (nr - len(members)) * ROW / 2      # 열마다 세로 가운데 정렬
            for i, n in enumerate(members):
                nx, ny = bx + Z_L + c * COL, by + top_pad + off + i * ROW
                shape = n.get("shape") or ROLE_SHAPE[n["role"]]
                gw, gh = GLYPH[shape]
                d = {"name": n["name"], "role": n["role"], "shape": shape, "zone": z,
                     "desc": n.get("desc", ""), "x": nx, "y": ny,
                     "w": CELL_W, "h": CELL_H,
                     "cx": nx + CELL_W / 2, "cy": ny + GLYPH_H / 2,
                     "gx": nx + (CELL_W - gw) / 2, "gy": ny + (GLYPH_H - gh) / 2,
                     "gw": gw, "gh": gh,
                     "caption": _addr_lines(n["name"], ctx, on_link)}
                placed.append(d)
                by_name[n["name"]] = d

    # ---- 구역 상자 정보
    zboxes = []
    for z in zorder:
        bx, by, bw, bh = boxes[z]
        band = "upper" if z in upper else "main"
        zboxes.append({"id": z, "label": ZONE_LABEL.get(z, z), "band": band,
                       "cidr": zsum.get(ZONE_CIDR_KEY.get(z, ""), "") if has_l3 else "",
                       "stacked": title_stacked(z),
                       "x": bx, "y": by, "w": bw, "h": bh,
                       # 제목 글줄의 기준선. 링크가 지나가지 않는 쪽에 둔다.
                       "title_y": by + 20 if band == "upper" else by + bh - 11})
    band_of = {b["id"]: b["band"] for b in zboxes}
    corridor = min((b["y"] for b in zboxes if b["band"] == "main"), default=0) - Z_GAP_Y / 2

    # ---- 링크마다 붙을 변을 먼저 정한다. 밴드를 넘는 링크만 위/아래로 나간다.
    plan = []
    for br, (a, ai), (b, bi) in links:
        A, B = by_name.get(a), by_name.get(b)
        if not A or not B:
            continue
        if band_of[A["zone"]] != band_of[B["zone"]]:
            up = band_of[A["zone"]] == "upper"
            sa, sb = ("bottom", "top") if up else ("top", "bottom")
            cross = True
        else:
            sa, sb, cross = _side(A, B), _side(B, A), False
        plan.append({"br": br, "a": a, "b": b, "ai": ai, "bi": bi,
                     "A": A, "B": B, "sa": sa, "sb": sb, "cross": cross})

    # ---- 변마다 몇 개가 붙는지 세고, 상대 위치 순서대로 나눠 붙인다
    sides = {}
    for p in plan:
        sides.setdefault((p["a"], p["sa"]), []).append((p["B"]["cy"], p["B"]["cx"], p["br"]["id"]))
        sides.setdefault((p["b"], p["sb"]), []).append((p["A"]["cy"], p["A"]["cx"], p["br"]["id"]))
    for v in sides.values():
        v.sort()
    idx = {k: [t[2] for t in v] for k, v in sides.items()}

    # ---- 링크 경로
    degree = {}
    for p_ in plan:
        degree[p_["a"]] = degree.get(p_["a"], 0) + 1
        degree[p_["b"]] = degree.get(p_["b"], 0) + 1
    out, seen_seg = [], set()
    for p in plan:
        A, B, br = p["A"], p["B"], p["br"]
        ka, kb = (p["a"], p["sa"]), (p["b"], p["sb"])
        fa, fb = idx[ka].index(br["id"]), idx[kb].index(br["id"])
        pa = _anchor(A, p["sa"], fa, len(idx[ka]))
        pb = _anchor(B, p["sb"], fb, len(idx[kb]))

        lines = _link_label(br, p["a"], p["ai"], p["b"], p["bi"], segs, ctx, has_l3)
        # 같은 VLAN 을 실어 나르는 트렁크가 둘이면(M5 의 sw1↔r1 · sw1↔r5)
        # 첫 링크에만 적는다. 두 번째 트렁크의 라벨이 놓일 통로에는 이미
        # 사이에 낀 장비의 포트 이름이 들어 있어 반드시 부딪힌다.
        if lines and lines[0].startswith("트렁크"):
            key = tuple(lines)
            if key in seen_seg:
                lines = []
            else:
                seen_seg.add(key)
        # 같은 대역에 링크가 여럿이면(VLAN40 은 4개) 첫 링크에만 적는다.
        if lines and lines[0].startswith("VLAN"):
            key = tuple(lines)
            if key in seen_seg:
                lines = []
            else:
                seen_seg.add(key)
        # 액세스 구간의 VLAN·대역은 링크가 아니라 그 끝의 단말 아래에 적는다.
        # 통로(96px)가 라벨(82px)보다 별로 넓지 않아 링크에 얹으면 양 끝의
        # 인터페이스 이름과 반드시 부딪힌다 — 단말 아래는 비어 있다.
        if lines and lines[0].startswith("VLAN"):
            leaf = next((n for n in (A, B) if degree[n["name"]] == 1), None)
            if leaf is not None:
                # VLAN 이름만 얹는다. 대역은 그 단말이 이미 자기 주소로 말한다
                # (`10.10.10.11/24` -> 대역이 `10.10.10.0/24` 임을 함께 알려 준다).
                leaf["caption"] = lines[:1] + leaf["caption"]
                lines = []

        if p["cross"]:
            # 아래 노드의 머리에서 곧장 위로, 두 밴드 사이 빈 통로를 타고 옆으로.
            lo, hi = (pa, pb) if p["sa"] == "top" else (pb, pa)
            pts = [lo, (lo[0], corridor), (hi[0], corridor), hi]
            ends = [(p["a"], p["ai"]), (p["b"], p["bi"])]
            if p["sa"] != "top":
                ends.reverse()
            kind = "riser"
        else:
            fwd = pa[0] <= pb[0]
            p1, p2 = (pa, pb) if fwd else (pb, pa)
            ends = [(p["a"], p["ai"]), (p["b"], p["bi"])]
            if not fwd:
                ends.reverse()
            if abs(p1[1] - p2[1]) < 1:
                pts, kind = [p1, p2], "straight"
            elif not _between(A, B, placed):
                # 이웃 열끼리는 **곧게** 잇는다. M5 의 r1↔r3 · r5↔r2 처럼 서로
                # 엇갈리는 두 링크를 직각으로 꺾어 놓으면 X 자가 사라져서,
                # 그림만 봐서는 mesh 인지 각자 한 가닥씩인지 구분이 안 된다.
                # 사이 열에 장비가 없으므로 비스듬한 선이 무엇을 가로지를 일도 없다.
                pts, kind = [p1, p2], "straight"
            else:
                # 꺾는 x 를 링크마다 어긋나게 — 같은 자리에서 꺾으면 선이 포개진다.
                far = idx[kb if fwd else ka]
                k = (fb if fwd else fa)
                t = 0.34 + 0.32 * (k / max(len(far) - 1, 1))
                mx = p1[0] + (p2[0] - p1[0]) * t
                pts, kind = [p1, (mx, p1[1]), (mx, p2[1]), p2], "bend"

        # 대역 이름은 두 열 사이 통로 한가운데에 얹는다. 경로의 가장 긴 구간을
        # 쓰면 라벨이 옆 장비 위로 삐져나온다 — 통로가 라벨보다 넓기 때문.
        if kind == "riser":
            lx, ly = (pts[1][0] + pts[2][0]) / 2, corridor
        else:
            lo, hi = (A, B) if A["gx"] < B["gx"] else (B, A)
            # 두 장비가 이웃 열이 아니면(열을 건너뛰는 링크) 가운데에 다른 장비가
            # 있다. 그 위에 대역 이름을 얹으면 글자가 장비를 덮는다 —
            # 오른쪽 장비 바로 앞의 빈 통로에 넣는다.
            left = lo["gx"] + lo["gw"]
            for n in placed:
                right = n["gx"] + n["gw"]
                if lo["gx"] < n["gx"] and right <= hi["gx"] and right > left:
                    left = right
            lx = (left + hi["gx"]) / 2
            ly = _y_at(pts, lx)

        fan = {p["a"]: fa, p["b"]: fb}
        ends_out = []
        for i, (nm, nf) in enumerate(ends):
            tx, ty, anc = _if_spot(pts, i == 0)
            # `eth2 .1` — 대역은 링크 위에 있으므로 끝자리만 적으면 주소가 정해진다.
            # 이름만 적혀 있으면 "10.10.64.1 이 누구 것인가" 를 그림이 답하지 못한다.
            oc = _end_octet(nm, nf, ctx)
            label = f"{nf} .{oc}" if oc else nf
            ends_out.append({"node": nm, "if": label, "fan": fan[nm],
                             "at": pts[0 if i == 0 else -1],
                             "tx": tx, "ty": ty, "anchor": anc})
        out.append({"a": p["a"], "b": p["b"], "a_if": p["ai"], "b_if": p["bi"],
                    "bridge": br["id"], "label": lines, "kind": kind, "points": pts,
                    "lx": lx, "ly": ly, "ends": ends_out})

    # ---- 라벨 자리 다툼을 푼다. 계산만으로는 통로가 좁은 곳에서 반드시 겹친다.
    P = _Placer()
    for n in placed:                                    # 장비 그림과 이름은 못 밀린다
        P.add((n["gx"], n["gy"], n["gx"] + n["gw"], n["gy"] + n["gh"]))
        w = _tw(n["name"], 12.5)
        P.add((n["cx"] - w / 2, n["y"] + GLYPH_H + 2, n["cx"] + w / 2, n["y"] + GLYPH_H + 15))
        for i, line in enumerate(n["caption"]):
            cw = _tw(line, 10.5)
            top = n["y"] + GLYPH_H + 19 + i * 12
            P.add((n["cx"] - cw / 2, top, n["cx"] + cw / 2, top + 11))
    for z in zboxes:
        w = _tw(z["label"], 12)
        y = z["title_y"] - (13 if z["stacked"] else 0)
        P.add((z["x"] + 14, y - 11, z["x"] + 14 + w, y + 2))
        if z["cidr"]:
            cw = _tw(z["cidr"], 11)
            cx = z["x"] + 14 if z["stacked"] else z["x"] + z["w"] - 14 - cw
            P.add((cx, z["title_y"] - 11, cx + cw, z["title_y"] + 2))

    IF_TRY = (0, 11, 22, -17, -28)
    SEG_TRY = (0, -13, -26, 15, 28, -39, 41, -52)
    for lk in out:                                      # 인터페이스 이름이 먼저 — 선에 붙어야 한다
        for e in lk["ends"]:
            w = _tw(e["if"], 10)
            x0 = e["tx"] - (w if e["anchor"] == "end" else 0)
            e["ty"] += P.fit(lambda dy: (x0, e["ty"] + dy - 8, x0 + w, e["ty"] + dy + 2), IF_TRY)
    for lk in out:
        if not lk["label"]:
            continue
        w = max(_tw(s, 10.5) for s in lk["label"])
        n = len(lk["label"])
        top = lk["ly"] - 7 - 12 * (n - 1) - 9
        lk["ly"] += P.fit(
            lambda dy: (lk["lx"] - w / 2, top + dy, lk["lx"] + w / 2, top + dy + 12 * n), SEG_TRY)

    return {"stage": stage, "w": round(total_w), "h": round(main_y + main_h + PAD),
            "zones": zboxes, "nodes": placed, "links": out}


if __name__ == "__main__":
    import json
    print(json.dumps(layout(sys.argv[1] if len(sys.argv) > 1 else "m11"),
                     ensure_ascii=False, indent=1))
