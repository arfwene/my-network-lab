#!/usr/bin/env python3
"""
교재 안의 작은 구성도들 — 배치만 계산한다. 그리지는 않는다.

토폴로지(tools/topolayout.py)와 같은 방식이다. 한 배치에서 두 가지가 나온다.
  console/diagram_svg.py     화면 · 교재 · 인쇄용 인라인 SVG
  tools/diagramdrawio.py     draw.io 에서 손볼 수 있는 .drawio

▸ 왜 만들었나
  교재의 구성도가 글자 그림(ASCII)으로 그려져 있었다. 한글은 화면에서 두 칸을
  먹는데 파이썬의 `%-20s` 는 **글자 수**로 채운다. 그래서 M0 의 상자는 위쪽 절반이
  53칸, 아래쪽 절반이 55칸으로 눈에 보이게 기울어 있었다. 랩 대역 길이가 바뀌면
  더 틀어진다 — 글자로 그리는 한 구조적으로 못 고친다.

▸ 왜 원시 요소를 하나로 뒀나
  모양은 여섯 가지지만 그리는 것은 상자 · 글자 · 선 셋뿐이다. 배치 함수마다
  SVG 를 짜면 여섯 벌이 되고, .drawio 까지 하면 열두 벌이 된다.
  배치는 여섯, 그리는 것은 둘 — 이 경계를 지킨다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml
from topolayout import _tw            # 글자 폭 추정. 규칙을 두 벌 두지 않는다.

PAD = 18                              # 캔버스 바깥 여백
FS = 13                               # 기본 글자 크기
FS_SM = 11.5                          # 곁들이는 글자
LH = 18                               # 줄 간격


class Scene:
    """상자 · 글자 · 선만 담는다. 좌표는 전부 절대값이다."""

    def __init__(self):
        self.boxes, self.texts, self.lines = [], [], []
        self.w = self.h = 0

    def box(self, x, y, w, h, tone="", rx=8, **kw):
        self.boxes.append({"x": x, "y": y, "w": w, "h": h, "tone": tone, "rx": rx, **kw})
        return self.boxes[-1]

    def text(self, x, y, s, cls="t", anchor="start", size=FS):
        if s is None or s == "":
            return None
        self.texts.append({"x": x, "y": y, "s": str(s), "cls": cls,
                           "anchor": anchor, "size": size})
        return self.texts[-1]

    def line(self, pts, cls="l", arrow=""):
        self.lines.append({"pts": pts, "cls": cls, "arrow": arrow})
        return self.lines[-1]

    def fit(self, extra=PAD):
        """그려 넣은 것을 다 감싸는 캔버스 크기를 정한다."""
        xs, ys = [0], [0]
        for b in self.boxes:
            xs += [b["x"], b["x"] + b["w"]]
            ys += [b["y"], b["y"] + b["h"]]
        for t in self.texts:
            tw = _tw(t["s"], t["size"])
            x0 = t["x"] - (tw if t["anchor"] == "end" else tw / 2 if t["anchor"] == "middle" else 0)
            xs += [x0, x0 + tw]
            ys += [t["y"] - t["size"], t["y"] + 4]
        for l in self.lines:
            for x, y in l["pts"]:
                xs.append(x)
                ys.append(y)
        self.w = round(max(xs) + extra)
        self.h = round(max(ys) + extra)
        return self


# ------------------------------------------------------------------ 도우미
def _lines(v):
    """문자열 하나든 목록이든 목록으로."""
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _boxw(title, rows, pad=28, floor=0):
    return max([floor, _tw(title, FS) + pad] + [_tw(r, FS_SM) + pad for r in rows])


# ------------------------------------------------------------------ 모양들
def _zones(spec, S):
    """구역 상자를 위아래로 쌓는다. 관리망 / 서비스망처럼 '층' 을 보여 줄 때."""
    layers = spec["layers"]
    inner = max(_boxw(l.get("title", ""), _lines(l.get("lines")), 34, 200)
                for l in layers)
    side = spec.get("side")
    x0 = PAD + (_tw(side, FS) + 34 if side else 0)
    y = PAD
    for i, l in enumerate(layers):
        rows = _lines(l.get("lines"))
        node = l.get("tone") == "node"
        w = _tw(l.get("title", ""), FS) + 52 if node else inner
        h = 34 if node else 30 + LH * len(rows) + 8
        bx = x0 + (inner - w) / 2
        S.box(bx, y, w, h, tone=l.get("tone", ""), rx=10 if not node else 8)
        S.text(bx + w / 2 if node else bx + 16, y + 21,
               l.get("title", ""), cls="t b",
               anchor="middle" if node else "start")
        for j, r in enumerate(rows):
            S.text(bx + 16, y + 21 + 6 + LH * (j + 1), r, cls="t m", size=FS_SM)
        if l.get("note"):
            S.text(bx + w + 14, y + 21, "← " + l["note"],
                   cls="t note " + (l.get("tone") or ""), size=FS_SM)
        if side and i == 0:
            S.text(x0 - 16, y + 21, side, cls="t", anchor="end")
            S.line([(x0 - 12, y + 16), (bx, y + 16)], arrow="end")
        if i + 1 < len(layers):
            S.line([(x0 + inner / 2, y + h), (x0 + inner / 2, y + h + 26)])
        y += h + 26
    return S


def _fields(spec, S):
    """바이트 필드 띠. 프레임·패킷의 머리말을 칸으로 보여 줄 때."""
    cells = spec["cells"]
    units = sum(c.get("grow", 1) for c in cells)
    need = sum(max(_tw(c.get("label", ""), FS_SM), _tw(c.get("sub", ""), FS_SM)) + 22
               for c in cells)
    total = max(need, 120 * units / max(units, 1))
    x, y, h = PAD, PAD + 6, 52
    spots = []
    for c in cells:
        share = max(_tw(c.get("label", ""), FS_SM), _tw(c.get("sub", ""), FS_SM)) + 22
        w = share / need * total
        S.box(x, y, w, h, tone=c.get("tone", ""), rx=0)
        S.text(x + w / 2, y + (21 if c.get("sub") else 30), c.get("label", ""),
               cls="t b", anchor="middle", size=FS_SM)
        if c.get("sub"):
            S.text(x + w / 2, y + 38, c["sub"], cls="t m", anchor="middle", size=FS_SM)
        spots.append(x + w / 2)
        x += w
    for co in _lines(spec.get("callout")):
        cx = spots[co["at"]]
        S.line([(cx, y + h), (cx, y + h + 18), (cx + 14, y + h + 18)])
        S.text(cx + 20, y + h + 22, co["text"], cls="t m", size=FS_SM)
    return S


def _seq(spec, S):
    """주고받는 차례. 배우를 세로 기둥으로 세우고 화살을 가로로 긋는다."""
    actors = spec["actors"]
    steps = spec["steps"]
    labw = max([_tw(s.get("label", ""), FS_SM) for s in steps] + [90]) + 40
    colw = max(labw, 120)
    xs = {a: PAD + 40 + i * colw for i, a in enumerate(actors)}
    top = PAD + 26
    ny = max([_tw(s.get("n", ""), FS_SM) for s in steps] + [0])
    y = top + 30
    for s in steps:
        a, b = xs[s["from"]], xs[s["to"]]
        if s.get("n"):
            S.text(PAD, y + 4, s["n"], cls="t m", size=FS_SM)
        S.line([(a, y), (b, y)], cls="l " + (s.get("tone") or ""), arrow="end")
        S.text((a + b) / 2, y - 7, s.get("label", ""), cls="t seg",
               anchor="middle", size=FS_SM)
        if s.get("note"):
            S.text(max(a, b) + 22, y + 4, s["note"],
                   cls="t m " + (s.get("tone") or ""), size=FS_SM)
        y += 34
    for a in actors:
        S.line([(xs[a], top + 8), (xs[a], y - 20)], cls="l dash")
        S.box(xs[a] - 40, PAD, 80, 26, tone="node", rx=6)
        S.text(xs[a], PAD + 18, a, cls="t b", anchor="middle", size=FS_SM)
    del ny
    return S


def _steps(spec, S):
    """왼쪽에서 오른쪽으로 이어지는 단계. 상태 전이 · 처리 순서."""
    items = [i if isinstance(i, dict) else {"title": i} for i in spec["items"]]
    x, y = PAD, PAD + 8
    spots = []
    for i, it in enumerate(items):
        w = _tw(it["title"], FS) + 30
        S.box(x, y, w, 34, tone=it.get("tone", ""), rx=6)
        S.text(x + w / 2, y + 22, it["title"], cls="t b", anchor="middle")
        spots.append((x, x + w))
        x += w
        if i + 1 < len(items):
            S.line([(x, y + 17), (x + 26, y + 17)], arrow="end")
            x += 26
    for u in _lines(spec.get("under")):
        a, b = spots[u["at"]]
        cx = (a + b) / 2
        S.line([(cx, y + 34), (cx, y + 52)])
        S.text(cx - 6, y + 68, u["text"], cls="t m", size=FS_SM)
    for sp in _lines(spec.get("span")):
        a = spots[sp["from"]][0]
        b = spots[sp["to"]][1]
        S.line([(a, y + 48), (a, y + 40), (b, y + 40), (b, y + 48)], cls="l dash")
        S.text((a + b) / 2, y + 66, sp["text"], cls="t m", anchor="middle", size=FS_SM)
    return S


def _branch(spec, S):
    """결정 나무. 물음 하나에서 갈래가 아래로 뻗는다 — 중첩할 수 있다."""
    x0, y = PAD, PAD + 8
    root = spec.get("root")
    if root:
        rows = _lines(spec.get("sub"))
        w = _boxw(root, rows, 34)
        h = 30 + LH * len(rows) + (2 if rows else -6)
        S.box(x0, y, w, h, tone=spec.get("tone", ""), rx=8)
        S.text(x0 + 16, y + 21, root, cls="t b")
        for j, r in enumerate(rows):
            S.text(x0 + 16, y + 21 + 6 + LH * (j + 1), r, cls="t m", size=FS_SM)
        y += h

    def walk(items, left, top):
        """갈래를 세로로 늘어놓고 왼쪽 세로줄에 매단다. 끝나는 y 를 돌려준다."""
        spine, cy = left + 14, top
        last = top
        for it in items:
            rows = _lines(it.get("lines"))
            cy += 16
            S.line([(spine, last + 4), (spine, cy), (spine + 16, cy)], cls="l")
            lab = it.get("label", "")
            S.text(spine + 24, cy + 5, lab, cls="t b", size=FS_SM)
            ly = cy
            for r in rows:
                ly += LH
                S.text(spine + 24, ly + 5, r, cls="t m", size=FS_SM)
            if it.get("items"):
                ly = walk(it["items"], spine + 24, ly + 6)
            cy = last = ly
        return cy

    y = walk(spec["items"], x0, y)
    return S


def _mini(spec, S):
    """작은 토폴로지 스케치. 열마다 장비를 세우고 이웃 열끼리 잇는다."""
    cols = [c if isinstance(c, list) else [c] for c in spec["cols"]]
    links = _lines(spec.get("links"))
    marks = spec.get("marks") or {}
    NW, NH, GAP, ROW = 84, 32, 132, 58
    rows = max(len(c) for c in cols)
    top = PAD + 8
    pos = {}
    for i, col in enumerate(cols):
        x = PAD + i * (NW + GAP)
        off = (rows - len(col)) * ROW / 2
        for j, n in enumerate(col):
            yy = top + off + j * ROW
            S.box(x, yy, NW, NH, tone="node", rx=7)
            S.text(x + NW / 2, yy + 21, n, cls="t b", anchor="middle", size=FS_SM)
            pos[n] = (x, yy)
    mid = top + (rows - 1) * ROW / 2 + NH / 2
    subs = []
    for i in range(len(cols) - 1):
        lk = links[i] if i < len(links) else {}
        x1 = PAD + i * (NW + GAP) + NW
        x2 = PAD + (i + 1) * (NW + GAP)
        # pair: 같은 두 장비를 잇는 선을 둘 그린다 (L2 루프처럼 경로가 둘일 때)
        offs = (-9, 9) if lk.get("pair") else (0,)
        for a in cols[i]:
            for b in cols[i + 1]:
                for d in offs:
                    S.line([(pos[a][0] + NW, pos[a][1] + NH / 2),
                            ((x1 + x2) / 2, pos[a][1] + NH / 2 + d),
                            ((x1 + x2) / 2, pos[b][1] + NH / 2 + d),
                            (pos[b][0], pos[b][1] + NH / 2)],
                           cls="l " + (lk.get("tone") or ""),
                           arrow="end" if lk.get("arrow") else "")
        subs.append(((x1 + x2) / 2, lk.get("sub", "")))
        S.text((x1 + x2) / 2, mid - 12, lk.get("label", ""),
               cls="t seg", anchor="middle", size=FS_SM)
    # 아래로 가는 글줄은 층을 나눠 쌓는다 — 같은 높이에 두면 반드시 부딪힌다.
    bottom = top + (rows - 1) * ROW + NH
    if any(s for _, s in subs):
        for cx, s in subs:
            S.text(cx, bottom + 18, s, cls="t m", anchor="middle", size=FS_SM)
        bottom += 18 + 6
    for n, rowsm in marks.items():
        x, yy = pos[n]
        for j, r in enumerate(_lines(rowsm)):
            S.text(x + NW / 2, bottom + 18 + j * LH, r, cls="t m",
                   anchor="middle", size=FS_SM)
    if marks:
        bottom += 18 + max(len(_lines(v)) for v in marks.values()) * LH - LH + 6
    for j, note in enumerate(_lines(spec.get("notes"))):
        S.text(PAD, bottom + 24 + j * LH, "← " + note, cls="t m", size=FS_SM)
    return S


def _spans(spec, S):
    """고정폭 한 줄에 밑줄 주석을 단다. 주소·포트의 어느 부분이 무엇인지 짚을 때."""
    x, y = PAD, PAD + 16
    marks = []
    for part in spec["parts"]:
        s = part["text"]
        w = _tw(s, FS + 1)
        S.text(x, y, s, cls="t seg", size=FS + 1)
        if part.get("under"):
            marks.append((x, x + w, part["under"]))
        x += w
    # 이름표가 부딪히면 한 층 아래로 내린다. 칸이 "11" 처럼 좁으면 반드시 겹친다.
    rows_used = []
    for a, b, label in marks:
        w = _tw(label, FS_SM)
        x0, x1 = (a + b) / 2 - w / 2, (a + b) / 2 + w / 2
        r = 0
        while any(r == rr and x0 < xb + 6 and xa < x1 + 6 for rr, xa, xb in rows_used):
            r += 1
        rows_used.append((r, x0, x1))
        S.line([(a + 1, y + 8), (a + 1, y + 14 + r * 16),
                (b - 1, y + 14 + r * 16), (b - 1, y + 8)], cls="l")
        S.text((a + b) / 2, y + 30 + r * 16, label, cls="t m", anchor="middle", size=FS_SM)
    if spec.get("note"):
        S.text(x + 18, y, "← " + spec["note"], cls="t m", size=FS_SM)
    return S


def _checks(spec, S):
    """순서대로 밟는 점검. 각 칸마다 '아니면 여기서 끝' 이 옆으로 빠진다."""
    x, y = PAD, PAD + 8
    W = max(_tw(f'{i.get("n","")} {i.get("title","")}   {i.get("ask","")}', FS) + 40
            for i in spec["items"])
    for k, it in enumerate(spec["items"]):
        S.box(x, y, W, 40, rx=8)
        S.text(x + 16, y + 25, f'{it.get("n","")}  {it.get("title","")}', cls="t b")
        S.text(x + 16 + 108, y + 25, it.get("ask", ""), cls="t")
        if it.get("tool"):
            S.text(x + W + 16, y + 25, it["tool"], cls="t seg", size=FS_SM)
        if it.get("fail"):
            S.line([(x + 26, y + 40), (x + 26, y + 58), (x + 44, y + 58)], cls="l warn")
            S.text(x + 52, y + 62, "아니면 → " + it["fail"], cls="t m warn", size=FS_SM)
            y += 40 + 30
        else:
            y += 40
        if k + 1 < len(spec["items"]):
            S.line([(x + W / 2, y), (x + W / 2, y + 22)], arrow="end")
            y += 22
    return S


KINDS = {"zones": _zones, "fields": _fields, "seq": _seq, "steps": _steps,
         "branch": _branch, "mini": _mini, "spans": _spans, "checks": _checks}


def build(spec):
    """스펙(dict) -> Scene. 그리는 쪽은 이 값만 보면 된다."""
    if isinstance(spec, str):
        spec = yaml.safe_load(spec)
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ValueError(f"모르는 구성도 종류: {kind!r} (가능: {', '.join(KINDS)})")
    return KINDS[kind](spec, Scene()).fit()


if __name__ == "__main__":
    import json
    s = build(sys.stdin.read())
    print(json.dumps({"w": s.w, "h": s.h, "boxes": s.boxes,
                      "texts": s.texts, "lines": s.lines}, ensure_ascii=False, indent=1))
