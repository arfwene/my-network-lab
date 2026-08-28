#!/usr/bin/env python3
"""교재 문장 규칙(docs/STYLE.md)을 기계가 잰다.

  python3 tools/check-style.py          전체
  python3 tools/check-style.py m01      한 모듈

규칙을 다 잴 수는 없다. 사람이 읽어야 아는 것(논평인가 정보인가)은 못 센다.
대신 **밀도**를 센다 — 굵게가 문장보다 많거나, 일곱 줄에 한 줄이 인용구면
읽는 사람이 무엇이 중요한지 고를 수 없다. 그건 세면 나온다.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
G, Y, R, N = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

BOLD_MAX = 0.35        # 문장당 굵게
QUOTE_MAX = 8.0        # 100줄당 인용구 줄
TABLE_MAX = 18.0       # 본문 줄 대비 표 줄 %. 날카로운 규칙은 '작은표 0' 쪽이다 —
                       # 이 비율은 '표가 많아 보인다' 는 신호일 뿐, 넘었다고 다 틀린 것은 아니다.
                       # M0 는 찾아보는 표(도구 10행·오류 대응 6행)만으로 16% 가 된다.
PREFIX = ("실무", "주의", "**소요 시간**")   # 인용구에 허용된 머리말
QUIZ_MAX = 12          # 모듈당 퀴즈 문항. 통과 기준이 100점이라 한 문항이 곧 재시험이다 —
                       # 스물이 되면 아는 사람도 다섯 번씩 다시 풀게 된다.
                       # 이것만은 재기만 하지 않고 **막는다** (아래 main 의 exit code).


def strip_blocks(t):
    """코드·다이어그램·표는 문장 규칙 대상이 아니다."""
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("|"))
    return t


def tables(t):
    """(표 개수, 표가 차지한 줄, 2열 3행 이하 표). 코드 블록은 뺀다."""
    lines = re.sub(r"```.*?```", "", t, flags=re.S).splitlines()
    blocks, cur = [], []
    for l in lines:
        if l.lstrip().startswith("|"):
            cur.append(l)
        elif cur:
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    tiny = []
    for b in blocks:
        cols = b[0].strip().strip("|").count("|") + 1
        rows = len(b) - 2                       # 머리글 + 구분선 제외
        # 2열 2행 이하는 거의 항상 표가 아니다 — 문장을 두 도막 낸 것이다.
        # 3행부터는 훑어서 한 칸을 찾는 목적이 생기므로 표로 인정한다.
        if cols <= 2 and rows <= 2:
            tiny.append(b[0].strip()[:56])
    return len(blocks), sum(len(b) for b in blocks), tiny, len(lines)


# 모양만 보고 정답을 고를 수 있으면 그 문항은 개념을 묻지 않는다.
#   대시 뒤 근거절이 일부에만 · 굵게가 정답에만 · 정답만 유난히 긺 ·
#   '항상/전부' 같은 단정이 오답에만 — 넷 다 교육생이 실제로 쓰는 요령이다.
SHAPE_OK = {
    # 틀린 단정 자체가 시험하려는 오해인 것들. '항상' 을 빼면 문제가 사라진다.
    ("m04", "q3"), ("m04", "q4"), ("m06", "q10"),
    # 정답이 긴 이유가 내용이 아니라 묻고 있는 대상(bpf 식) 자체인 것.
    ("m07", "q6"),
    # 다섯 중 넷이 정답이라 '가장 긴 것' 요령이 통하지 않는다.
    ("m07", "q4"),
}
ABSOLUTE = ("항상", "절대", "전부", "무조건", "반드시", "오직", "만이")
LEN_GAP = 8            # 정답이 2등보다 이만큼 길면 눈에 띈다


def shape_leaks(mod_dir):
    """선택지의 모양이 정답을 가리키는 문항. [(문항id, 무엇이)] 를 돌려준다."""
    f = mod_dir / "assessment.yml"
    if not f.exists():
        return []
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    out = []
    for q in ((d.get("quiz") or {}).get("questions") or []):
        if q.get("type") not in ("single", "multi"):
            continue
        if (mod_dir.name[:3], q["id"]) in SHAPE_OK:
            continue
        ch, ans = q["choices"], set(q["answer"])
        length = [len(c) for c in ch]
        why = []
        if max(length) >= LEN_GAP + sorted(length)[-2] and length.index(max(length)) in ans:
            why.append("정답만 긺")
        dash = [i for i, c in enumerate(ch) if "—" in c]
        if dash and len(dash) < len(ch):
            why.append("대시가 일부에만")
        bold = [i for i, c in enumerate(ch) if "**" in c]
        if bold and set(bold) <= ans:
            why.append("굵게가 정답에만")
        hard = [i for i in range(len(ch)) if i not in ans
                and any(a in ch[i] for a in ABSOLUTE)]
        if hard and not [i for i in ans if any(a in ch[i] for a in ABSOLUTE)]:
            why.append("단정이 오답에만")
        if why:
            out.append((q["id"], " · ".join(why)))
    return out


# 앞 모듈을 가리켜도 되는 자리. 중간 점검은 **누적 범위를 진단하는 것**이 목적이라
# 범위 표기가 곧 그 과제의 정의다. 앞 모듈의 산출물을 요구하지는 않으므로,
# 늦게 합류한 사람도 랩만 있으면 풀 수 있다.
BACK_OK = {
    ("m03", "### 과제 5. ★ 중간 점검 — 증상만 보고 진단하기 (M1~M3 범위)"),
    ("m06", "### 과제 5. ★ 중간 점검 — 증상만 보고 진단하기 (M4~M6 범위)"),
}


CODE_FENCE = re.compile(r"```[a-z]*\n.*?```", re.S)
MD_IN_CODE = re.compile(r"\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^)\n]+\)|(?<!`)`[^`\n]+`(?!`)")


def md_in_code(mod_dir):
    """코드 블록 안에 쓴 마크다운. [(파일, 줄)] 을 돌려준다.

    코드 블록은 글자를 **그대로** 보여 주는 자리다. 거기 `**같은 지도**` 라고
    쓰면 화면에 별 네 개가 그대로 찍힌다. 실제로 M4 2.3 이 그랬다.
    구성도(labdiagram · mermaid)는 자체 문법이 있으므로 제외한다.
    """
    out = []
    for f in sorted(mod_dir.glob("*.md.j2")):
        t = f.read_text(encoding="utf-8")
        for m in CODE_FENCE.finditer(t):
            b = m.group(0)
            if b.startswith("```labdiagram") or b.startswith("```mermaid"):
                continue
            for line in b.splitlines()[1:-1]:
                if MD_IN_CODE.search(line):
                    out.append((f.name, line.strip()[:60]))
    return out


# root 가 있어야 도는 명령. 교재의 ```bash 블록에 sudo 없이 적히면 교육생은
# "Operation not permitted" 를 만난다 — 실제로 M2 6.4 의 정리 명령이 그랬다.
# vtysh 의 조회(`-c 'show ...'`)만은 예외다 — 랩 계정이 frrvty 그룹에 있다.
NEEDS_ROOT = re.compile(
    r"^(nft |tcpdump -i |conntrack |ip link set |ip link add |ip link del "
    r"|ip addr add |ip addr del |ip route add |ip route del |ip route flush "
    r"|bridge vlan add |bridge vlan del |sysctl -w |systemctl (start|stop|restart) "
    r"|vtysh(?! -c ['\"]show)|iptables )")


def sudo_missing(mod_dir):
    """```bash 블록에서 sudo 가 빠진 root 명령. [(파일, 줄)] 을 돌려준다."""
    out = []
    for f in sorted(mod_dir.glob("*.md.j2")):
        for m in CODE_FENCE.finditer(f.read_text(encoding="utf-8")):
            b = m.group(0)
            if not b.startswith("```bash"):
                continue
            for line in b.splitlines()[1:-1]:
                if NEEDS_ROOT.match(line):
                    out.append((f.name, line.strip()[:60]))
    return out


def back_refs(mod_dir):
    """과제가 **앞 모듈**을 가리키는 자리. [(줄번호, 줄)] 을 돌려준다.

    과제는 그 모듈 안에서 끝나야 한다. 앞 모듈에서 만든 캡처·점검표·표를
    가져오라고 하면, 늦게 합류했거나 그 파일을 잃은 사람은 손도 못 댄다.
    앞을 가리키는 것(`M4 로 넘어가도 됩니다`)은 의존이 아니므로 놔둔다.
    캡스톤(M10)만 예외다 — 전 과정을 모아 보는 자리이기 때문.
    """
    f = mod_dir / "tasks.md.j2"
    try:
        me = int(mod_dir.name[1:3])
    except ValueError:
        return []
    if me == 10 or not f.exists():
        return []
    out = []
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if (mod_dir.name[:3], line.strip()) in BACK_OK:
            continue
        for n in re.findall(r"\bM(\d)\b", line):
            if int(n) < me:
                out.append((i, line.strip()[:70]))
                break
    return out


def quiz_count(mod_dir):
    """그 모듈의 퀴즈 문항 수. assessment.yml 이 없으면 0."""
    f = mod_dir / "assessment.yml"
    if not f.exists():
        return 0
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return len((d.get("quiz") or {}).get("questions") or [])


#  교재 · 과제는 존댓말로 쓴다 (docs/STYLE.md 1). 예전 모듈은 평서형(`~한다`)이라
#  한 번에 다 못 바꾼다 — 막지 않고 **몇 문장 남았는지만** 센다. 0 이면 다 바뀐 것이다.
#  제목과 도입부(첫 `---` 위)는 이름표에 가까우므로 세지 않는다.
PLAIN = re.compile(r"[가-힣]+(?<!니)다(?=[.\s]|$)")
# 괄호가 바로 붙어 문장이 끝나는 자리. PLAIN 은 구절 끝에서만 세므로 이것을 놓쳤다.
PAREN = re.compile(r"[가-힣]+(?<!니)다(?=\*{0,2}\()")


def plain_form(raw):
    body = strip_blocks(raw)
    body = body.split("\n---\n", 1)[-1]
    n = 0
    for line in body.splitlines():
        if line.startswith("#") or line.lstrip().startswith("- ["):
            continue
        # 문장이 괄호로 끝나는 자리 — "…보낸다(M2 에서 본 것)." 도 평서형이다.
        # 따옴표 안은 세지 않는다. 증상이나 남의 말을 그대로 옮긴 인용이라
        # 평서형이 맞기 때문이다.
        for m in PAREN.finditer(line):
            if line[:m.start()].count('"') % 2 == 0:
                n += 1
        for seg in re.split(r"(?<=다[.])\s+|\|", line):
            # 문장 끝의 괄호와 굵게 표시를 걷어내야 "(M3 에서 쓴다)" 나
            # "**...생겼다.**" 처럼 표시에 감싸인 평서형이 잡힌다. 따옴표는
            # 걷어내지 않는다 — 증상이나 남의 말을 그대로 옮긴 인용은 평서형이 맞다.
            seg = seg.strip().rstrip(".)* ").strip()
            if seg and PLAIN.search(seg) and PLAIN.search(seg).end() == len(seg):
                n += 1
    return n


def measure(p):
    raw = p.read_text(encoding="utf-8")
    body = strip_blocks(raw)
    lines = body.splitlines()
    quotes = [l for l in lines if l.lstrip().startswith(">")]
    # 이어지는 인용구 줄은 하나로 센다 (문단 수를 세는 것이 목적)
    blocks, prev = [], False
    for l in lines:
        q = l.lstrip().startswith(">")
        if q and not prev:
            blocks.append(l.lstrip()[1:].strip())
        prev = q
    bad_prefix = [b for b in blocks if not any(b.startswith(x) for x in PREFIX)]
    bold = len(re.findall(r"\*\*[^*\n]+\*\*", body))
    sent = len(re.findall(r"(?:다|요)[.]|[.!?]\s*$", body, re.M)) or 1
    ntab, tab_lines, tiny, all_lines = tables(raw)
    return {
        "plain": plain_form(raw),
        "lines": len(lines), "quote_lines": len(quotes), "quote_blocks": len(blocks),
        "bad_prefix": bad_prefix, "bold": bold, "sent": sent, "tiny": tiny,
        "bold_per_sent": bold / sent, "quote_per_100": len(quotes) / max(len(lines), 1) * 100,
        "table_pct": tab_lines / max(all_lines, 1) * 100, "tables": ntab,
    }


def main(only=None):
    files = sorted((ROOT / "modules").glob("*/README.md.j2"))
    if only:
        files = [f for f in files if f.parent.name.startswith(only)]
    bad = 0
    over, leaks, backs, mds, sudos = [], [], [], [], []
    print(f"{'모듈':<6}{'퀴즈':>6}{'모양누출':>9}{'앞모듈참조':>11}{'코드속MD':>10}{'sudo빠짐':>10}"
          f"{'굵게/문장':>10}{'인용/100줄':>11}{'표%':>7}"
          f"{'작은표':>7}{'머리말없는인용':>15}{'평서형':>8}")
    print("-" * 114)
    for f in files:
        m = measure(f)
        nq = quiz_count(f.parent)
        sl = shape_leaks(f.parent)
        br = back_refs(f.parent)
        mc = md_in_code(f.parent)
        sd = sudo_missing(f.parent)
        marks = []
        if mc:
            marks.append("코드속MD")
            mds += [(f.parent.name[:3], fn, s) for fn, s in mc]
        if sd:
            marks.append("sudo빠짐")
            sudos += [(f.parent.name[:3], fn, s) for fn, s in sd]
        if br:
            marks.append("앞모듈")
            backs += [(f.parent.name[:3], ln, s) for ln, s in br]
        if sl:
            marks.append("모양")
            leaks += [(f.parent.name[:3], qid, why) for qid, why in sl]
        if nq > QUIZ_MAX:
            marks.append("퀴즈")
            over.append((f.parent.name, nq))
        if m["bold_per_sent"] > BOLD_MAX: marks.append("굵게")
        if m["quote_per_100"] > QUOTE_MAX: marks.append("인용")
        # 표 비율은 **재기만 한다.** M10 처럼 13행짜리 명령표·9단계 점검표가 본문인
        # 문서는 30% 가 나와도 맞다. 실제로 걸러야 하는 것은 아래 '작은표' 쪽이다.
        if m["tiny"]: marks.append("작은표")
        if m["bad_prefix"]: marks.append("머리말")
        col = R if marks else G
        print(f"{col}{f.parent.name[:4]:<6}{nq:>6}{len(sl):>9}{len(br):>11}{len(mc):>10}{len(sd):>10}"
              f"{m['bold_per_sent']:>10.2f}"
              f"{m['quote_per_100']:>11.1f}{m['table_pct']:>7.1f}{len(m['tiny']):>7}"
              f"{len(m['bad_prefix']):>15}{m['plain']:>8}{N}")
        if marks:
            bad += 1
            for b in m["bad_prefix"][:2]:
                print(f"        {Y}머리말 없는 인용구{N}: {b[:60]}")
            for b in m["tiny"][:2]:
                print(f"        {Y}2열 3행 이하 표{N}: {b}")
    print("-" * 114)
    print(f"기준: 퀴즈 ≤ {QUIZ_MAX}문항 · 모양누출 0 · 앞모듈참조 0 · 코드속MD 0 · sudo빠짐 0 · "
          f"굵게/문장 ≤ {BOLD_MAX} · 인용/100줄 ≤ {QUOTE_MAX} · 작은표 0 · 머리말 없는 인용구 0")
    print("표% 와 평서형은 참고값이다 — 표는 찾아보는 문서면 높아도 맞고,")
    print("평서형은 아직 존댓말로 안 바꾼 모듈이 몇 문장 남았는지를 센다 (0 이 목표).")
    if bad:
        print(f"{Y}{bad}개 모듈이 기준을 넘는다 (docs/STYLE.md){N}")
    else:
        print(f"{G}전부 기준 안{N}")
    # 문장 밀도는 사람이 읽고 판단할 몫이라 재기만 한다. 퀴즈 문항 수는
    # 세면 답이 나오는 값이므로 여기서 막는다.
    for mod, fn, s in mds:
        print(f"{R}✘{N} {mod}/{fn} 코드 블록 안에 마크다운이 있다 — {s}")
    for mod, fn, s in sudos:
        print(f"{R}✘{N} {mod}/{fn} root 명령에 sudo 가 없다 — {s}")
    for mod, ln, s in backs:
        print(f"{R}✘{N} {mod} tasks.md.j2:{ln} 이 앞 모듈을 가리킨다 — {s}")
    for mod, qid, why in leaks:
        print(f"{R}✘{N} {mod} {qid}: {why} — 모양만 보고 정답을 고를 수 있다")
    for name, n in over:
        print(f"{R}✘{N} {name} 의 퀴즈가 {n}문항이다 — {QUIZ_MAX}문항까지만 둔다 "
              f"(modules/{name}/assessment.yml)")
    return 1 if (over or leaks or backs or mds or sudos) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
