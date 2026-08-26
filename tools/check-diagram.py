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
import diagram as DG


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


def check_docs(err):
    """교재 안의 작은 구성도들. 글자가 서로 겹치거나 캔버스를 벗어나지 않는지."""
    import re
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    import yaml as Y
    FENCE = re.compile(r"```labdiagram\n(.*?)```", re.S)
    n = 0
    for d in sorted((L.ROOT / "modules").iterdir()):
        if not (d / "meta.yml").exists():
            continue
        meta = Y.safe_load((d / "meta.yml").read_text(encoding="utf-8"))
        env = Environment(loader=FileSystemLoader(d), undefined=StrictUndefined,
                          keep_trailing_newline=True)
        ctx = {**L.doc_context(1, meta["stage"]), "meta": meta, "topology_full": ""}
        for f in sorted(d.glob("*.md.j2")):
            for k, spec in enumerate(FENCE.findall(env.get_template(f.name).render(**ctx))):
                where = f"{d.name}/{f.stem} #{k + 1}"
                try:
                    s = DG.build(spec)
                except Exception as e:                       # noqa: BLE001
                    err(where, f"그리지 못한다: {e}")
                    continue
                n += 1
                tb = []
                for x in s.texts:
                    w = DG._tw(x["s"], x["size"])
                    x0 = x["x"] - (w if x["anchor"] == "end"
                                   else w / 2 if x["anchor"] == "middle" else 0)
                    tb.append((x["s"], box(x0, x["y"] - x["size"], w, x["size"] + 3)))
                for i in range(len(tb)):
                    for j in range(i + 1, len(tb)):
                        if hit(tb[i][1], tb[j][1], pad=2):
                            err(where, f"글자끼리 겹친다: «{tb[i][0]}» ↔ «{tb[j][0]}»")
                for name, b in tb:
                    if b[0] < -1 or b[2] > s.w + 1 or b[1] < -1 or b[3] > s.h + 1:
                        err(where, f"글자가 그림 밖으로 나갔다: «{name}»")
                bx = [box(b["x"], b["y"], b["w"], b["h"]) for b in s.boxes]
                for i in range(len(bx)):
                    for j in range(i + 1, len(bx)):
                        if hit(bx[i], bx[j], pad=2):
                            err(where, "상자끼리 겹친다")
    return n


def main():
    found = []
    check_all = lambda s, m: found.append(f"[{s}] {m}")     # noqa: E731
    for stage in L.STAGES:
        check(stage, check_all)
    n = check_docs(lambda w, m: found.append(f"[{w}] {m}"))
    if found:
        print("구성도 검사 — 겹침 발견")
        for f in found:
            print("  " + f)
        return 1
    sizes = ", ".join(f"{s} {T.layout(s)['w']}x{T.layout(s)['h']}" for s in ("m1", "m10"))
    print(f"토폴로지 {len(L.STAGES)}단계 겹침 없음 ({sizes}) · 교재 구성도 {n}개 겹침 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
