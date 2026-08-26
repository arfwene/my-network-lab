#!/usr/bin/env python3
"""
구성도가 겹치지 않는지 검사한다. 단계 m1~m10 을 전부 본다.

▸ 왜 필요한가
  구성도는 design/topology.yml 에서 자동으로 그려진다. 노드를 하나 더하거나
  이름을 길게 바꾸면 라벨이 장비를 덮거나 선이 장비를 통과할 수 있는데,
  그림은 그래도 그려지므로 아무도 모른 채 배포된다. 실제로 예전 배치는
  M9 부터 구역 상자가 겹친 채로 오래 나갔다.

▸ 글자 폭은 추정한다
  정확히 재려면 폰트를 열어야 하고, 그러면 이 검사가 폰트에 묶인다.
  한글 한 칸 · 라틴 0.6칸으로 보면 부딪히는지 가리기에는 충분하다.

usage:  python3 tools/check-diagram.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L
import topolayout as T


def box(x, y, w, h):
    return (x, y, x + w, y + h)


def hit(a, b, pad=1.0):
    return (a[0] < b[2] - pad and b[0] < a[2] - pad
            and a[1] < b[3] - pad and b[1] < a[3] - pad)


def crosses(p1, p2, b):
    """가로 · 세로 선분이 상자를 지나는가."""
    (x1, y1), (x2, y2) = p1, p2
    if abs(y1 - y2) < 1:
        return b[1] < y1 < b[3] and min(x1, x2) < b[2] and b[0] < max(x1, x2)
    return b[0] < x1 < b[2] and min(y1, y2) < b[3] and b[1] < max(y1, y2)


def check(stage, err):
    d = T.layout(stage)
    solid = []          # 못 밀어내는 것 — 장비 그림과 이름
    for n in d["nodes"]:
        solid.append((n["name"], box(n["gx"], n["gy"], n["gw"], n["gh"])))
        w = T._tw(n["name"], 12.5)
        solid.append((n["name"] + " 이름",
                      box(n["cx"] - w / 2, n["y"] + T.GLYPH_H + 2, w, 13)))

    texts = []
    for n in d["nodes"]:
        for i, line in enumerate(n["caption"]):
            w = T._tw(line, 10.5)
            texts.append((f"{n['name']} 설명 «{line}»",
                          box(n["cx"] - w / 2, n["y"] + T.GLYPH_H + 19 + i * 12, w, 11)))
    for z in d["zones"]:
        ty = z["title_y"] - (13 if z["stacked"] else 0)
        texts.append((f"구역 «{z['label']}»",
                      box(z["x"] + 14, ty - 11, T._tw(z["label"], 12), 13)))
        if z["cidr"]:
            w = T._tw(z["cidr"], 11)
            cx = z["x"] + 14 if z["stacked"] else z["x"] + z["w"] - 14 - w
            texts.append((f"구역대역 «{z['cidr']}»", box(cx, z["title_y"] - 11, w, 13)))
    for lk in d["links"]:
        for i, line in enumerate(reversed(lk["label"])):
            w = T._tw(line, 10.5)
            texts.append((f"{lk['a']}-{lk['b']} «{line}»",
                          box(lk["lx"] - w / 2, lk["ly"] - 7 - i * 12 - 9, w, 11)))
        for e in lk["ends"]:
            w = T._tw(e["if"], 10)
            x = e["tx"] - (w if e["anchor"] == "end" else 0)
            texts.append((f"{e['node']}.{e['if']}", box(x, e["ty"] - 8, w, 10)))

    for i in range(len(solid)):
        for j in range(i + 1, len(solid)):
            if hit(solid[i][1], solid[j][1]):
                err(stage, f"장비가 겹친다: {solid[i][0]} ↔ {solid[j][0]}")
    for tn, tb in texts:
        for sn, sb in solid:
            if hit(tb, sb):
                err(stage, f"글자가 장비를 덮는다: {tn} ↔ {sn}")
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if hit(texts[i][1], texts[j][1]):
                err(stage, f"글자끼리 겹친다: {texts[i][0]} ↔ {texts[j][0]}")
    for lk in d["links"]:
        for p1, p2 in zip(lk["points"], lk["points"][1:]):
            for sn, sb in solid[::2]:                 # 글리프만
                if sn in (lk["a"], lk["b"]):
                    continue
                if crosses(p1, p2, sb):
                    err(stage, f"선이 장비를 통과한다: {lk['a']}-{lk['b']} → {sn}")
    for tn, tb in texts:
        if tb[0] < 0 or tb[2] > d["w"] or tb[1] < 0 or tb[3] > d["h"]:
            err(stage, f"글자가 그림 밖으로 나갔다: {tn}")


def main():
    found = []
    check_all = lambda s, m: found.append(f"[{s}] {m}")     # noqa: E731
    for stage in L.STAGES:
        check(stage, check_all)
    if found:
        print("구성도 검사 — 겹침 발견")
        for f in found:
            print("  " + f)
        return 1
    sizes = ", ".join(f"{s} {T.layout(s)['w']}x{T.layout(s)['h']}" for s in ("m1", "m10"))
    print(f"구성도 {len(L.STAGES)}단계 겹침 없음 ({sizes})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
