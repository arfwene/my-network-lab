#!/usr/bin/env python3
"""
모듈 검사 실행기 — "설정이 들어갔는가 / 의도한 대로 통신이 되는가" 를 확인한다.

  python3 tools/run-checks.py --lab 1 --module m01

동작
  1) modules/<dir>/assessment.yml 의 checks 를 읽는다 (주소는 Jinja 로 치환)
  2) 호스트별로 묶어 플레이북을 만든다
  3) 각 검사는 결과를 **컨트롤러의 JSON 파일로 직접 쓴다**
     (ansible 표준 출력 파싱은 형식이 바뀌면 깨진다)
  4) 판정 후 결과를 var/state/checks-lab<N>-<module>.json 으로 남긴다
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import yaml
from pathlib import Path
from jinja2 import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

ANSIBLE = L.ROOT / "infra/ansible"


def _bin(name):
    cand = Path(sys.executable).parent / name
    return str(cand) if cand.exists() else name


def module_dir(module_id):
    for d in sorted((L.ROOT / "modules").iterdir()):
        f = d / "meta.yml"
        if d.is_dir() and f.exists():
            if yaml.safe_load(f.read_text(encoding="utf-8"))["id"] == module_id:
                return d
    raise SystemExit(f"없는 모듈: {module_id}")


def _read_spec(module_id):
    d = module_dir(module_id)
    meta = yaml.safe_load((d / "meta.yml").read_text(encoding="utf-8"))
    f = d / "assessment.yml"
    spec = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.exists() else {}
    return meta, spec


def _included(module_id, block):
    """앞 모듈의 검사를 그대로 가져온다.

    캡스톤(M11)이 "앞 모듈 전체를 한 번에" 확인하는 방식이다 (PLAN 6.6).
    검사를 복사해 두면 원본이 고쳐졌을 때 캡스톤만 낡아 버리므로 참조로 가져온다.

      · id 는 `<출처>.<원래 id>` 로 바꾼다 — 모듈이 다르면 같은 id 가 있을 수 있다
        (m03·m04 의 cross-site-http, m03·m06 의 r1-no-leftover)
      · 제목 앞에 출처를 붙인다. 캡스톤에서는 **어느 계층이 깨졌는지**가 바로 보여야 한다
      · 가져온 모듈의 include 는 따라가지 않는다 (한 단계만) — 순환을 만들 여지를 없앤다
    """
    out = []
    for inc in block.get("include") or []:
        src = inc["from"]
        items = ((_read_spec(src)[1].get("checks") or {}).get("items")) or []
        have = {c["id"] for c in items}
        only = inc.get("only")
        drop = set(inc.get("exclude") or [])
        # 오타를 조용히 넘기지 않는다. 검사 하나가 소리 없이 빠지는 것이 가장 나쁘다 —
        # 시험은 그대로 돌아가고 아무도 빠진 줄 모른다.
        for wid in list(only or []) + sorted(drop):
            if wid not in have:
                raise SystemExit(f"{module_id}: {src} 에 '{wid}' 검사가 없다")
        for c in items:
            if only is not None and c["id"] not in only:
                continue
            if c["id"] in drop:
                continue
            c = dict(c)
            c["id"] = f"{src}.{c['id']}"
            c["title"] = f"{src.upper()} · {c['title']}"
            out.append(c)
    return out


def _cumulative(module_id, upto):
    """`upto` 단계까지의 **앞 모듈 검사**. 중간 점검이 이걸 쓴다.

    중간 점검은 여러 모듈에 걸친 장애 하나를 가려 놓고 푸는 자리인데, 판정은
    그 단계 모듈의 검사 하나로만 했다. 그래서 M1 의 링크를 내려 놓고 M3 의
    검사를 돌리면 **아무것도 실패하지 않는다** — 누르자마자 통과가 뜬다.

    어느 검사를 가져올지는 새로 정하지 않는다. 캡스톤(m11)이 이미 **끝까지
    살아남는 검사**만 골라 두었으므로 그 목록을 그대로 쓰고, 이번 단계보다
    나중 모듈만 걸러낸다. 목록이 한 곳이라 한쪽만 낡을 일이 없다.
    (그 단계 모듈 자신의 검사는 호출한 쪽이 통째로 쓴다 — 캡스톤 기준으로
     솎아낸 것이 아니라 그 단계에서 참인 것 전부여야 하기 때문이다.)
    """
    cap = ((L.SITE.get("console") or {}).get("capstone") or {}).get("module")
    if not cap or cap == module_id:
        return []
    block = _read_spec(cap)[1].get("checks") or {}
    incs = []
    for inc in block.get("include") or []:
        src = inc["from"]
        if src == module_id:
            continue
        if not L.stage_le(_read_spec(src)[0]["stage"], upto):
            continue
        incs.append(inc)
    return _included(module_id, {"include": incs})


def load_checks(module_id, lab_id, upto=None):
    meta, spec = _read_spec(module_id)
    block = spec.get("checks") or {}
    items = (_cumulative(module_id, upto) if upto else []) \
        + _included(module_id, block) + (block.get("items") or [])
    # id 가 겹치면 결과 파일 이름이 겹쳐 한쪽이 조용히 사라진다. 앞의 것을 남긴다.
    seen, uniq = set(), []
    for c in items:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    items = uniq
    if not items:
        return meta, []
    # 가져온 검사도 **이 모듈의 단계**로 치환한다.
    # 원래 단계로 치환하면 그때 아직 살아 있지 않던 인터페이스가 빠져 주소를 못 찾는다.
    ctx = L.doc_context(lab_id, meta["stage"])
    out = []
    for it in items:
        c = dict(it)
        # 판정에 쓰이는 문자열은 전부 같은 규칙으로 치환한다.
        # 하나라도 빠지면 그 항목만 조용히 `{{ ... }}` 리터럴과 비교하게 된다.
        for k in ("run", "expect", "expect_regex", "hint", "title"):
            if isinstance(c.get(k), str):
                c[k] = Template(c[k]).render(**ctx)
        for k in ("expect_all", "expect_none"):
            if c.get(k):
                c[k] = [Template(x).render(**ctx) for x in c[k]]
        out.append(c)
    return meta, out


def build_playbook(checks, result_dir):
    by_host = {}
    for c in checks:
        by_host.setdefault(c["host"], []).append(c)
    plays = []
    for host, items in by_host.items():
        tasks = []
        for c in items:
            tasks.append({
                "name": f"run::{c['id']}",
                "ansible.builtin.shell": c["run"],
                "args": {"executable": "/bin/bash"},
                # 검사는 대부분 읽기 전용이다. root 가 필요한 검사만 become: true 를 적는다.
                "become": bool(c.get("become", False)),
                "register": "chk",
                "ignore_errors": True,
                "changed_when": False,
            })
            tasks.append({
                "name": f"save::{c['id']}",
                "ansible.builtin.copy": {
                    "content": ("{{ {'id': '%s', 'rc': chk.rc | default(-1), "
                                "'stdout': chk.stdout | default(''), "
                                "'stderr': chk.stderr | default('')} | to_nice_json }}"
                                % c["id"]),
                    "dest": f"{result_dir}/{c['id']}.json",
                },
                "delegate_to": "localhost",
                "become": False,
            })
        plays.append({"hosts": host, "gather_facts": False, "become": False, "tasks": tasks})
    return plays


def evaluate(c, raw):
    """검사 하나의 통과 여부와 이유."""
    if raw is None:
        return False, "실행되지 않았다 (노드에 접속하지 못했을 수 있다)"
    out = (raw.get("stdout") or "").strip()
    rc = raw.get("rc", -1)
    want_rc = c.get("expect_rc", 0)
    if rc != want_rc:
        return False, f"종료 코드 {rc} (기대 {want_rc})" + (f" — {raw.get('stderr','')[:120]}" if raw.get("stderr") else "")
    if "expect" in c and c["expect"] not in out:
        return False, f"출력에 '{c['expect']}' 가 없다 (실제: {out[:120] or '(빈 출력)'})"
    for w in c.get("expect_all", []):
        if w not in out:
            return False, f"출력에 '{w}' 가 없다"
    for w in c.get("expect_none", []):
        if w in out:
            # 무엇이 있으면 안 되는지만 말하면 어디를 볼지 알 수 없다. 실제 출력을 함께 준다.
            return False, f"출력에 '{w}' 가 있으면 안 된다 (실제: {out[:120]})"
    if c.get("expect_regex") and not re.search(c["expect_regex"], out):
        return False, f"출력이 패턴 '{c['expect_regex']}' 와 맞지 않는다"
    return True, "통과"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=int, default=1)
    ap.add_argument("--module", required=True)
    ap.add_argument("--out")
    ap.add_argument("--inventory", help="기본: infra/ansible/inventory/lab<N>")
    ap.add_argument("--upto", help="이 단계까지의 앞 모듈 검사도 함께 돌린다 (중간 점검)")
    args = ap.parse_args()

    meta, checks = load_checks(args.module, args.lab, args.upto)
    out_path = Path(args.out) if args.out else \
        L.ROOT / f"var/state/checks-lab{args.lab}-{args.module}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = {"lab_id": args.lab, "module_id": args.module, "stage": meta["stage"],
              "total": len(checks), "ok": 0, "passed": False, "items": []}

    if not checks:
        result["passed"] = True
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("검사 항목이 없다 — 통과 처리")
        return 0

    work = L.ROOT / f"var/tmp/checks-lab{args.lab}-{args.module}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    pb = work / "checks.yml"
    pb.write_text(yaml.safe_dump(build_playbook(checks, str(work)),
                                 allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"검사 {len(checks)}개 실행 (lab{args.lab} · {args.module})")
    inv = args.inventory or f"inventory/lab{args.lab}"
    subprocess.run([_bin("ansible-playbook"), "-i", inv, str(pb)], cwd=str(ANSIBLE))

    for c in checks:
        f = work / f"{c['id']}.json"
        raw = None
        if f.exists():
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = None
        ok, reason = evaluate(c, raw)
        result["ok"] += 1 if ok else 0
        result["items"].append({
            "id": c["id"], "title": c["title"], "kind": c.get("kind", "state"),
            "host": c["host"], "ok": ok, "reason": reason,
            "run": c["run"], "stdout": (raw or {}).get("stdout", "")[:600],
            "hint": c.get("hint", ""),
        })

    result["passed"] = result["ok"] == result["total"]
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    for it in result["items"]:
        print(f"  [{'PASS' if it['ok'] else 'FAIL'}] {it['title']}")
        if not it["ok"]:
            print(f"         {it['reason']}")
            if it["hint"]:
                print(f"         힌트: {it['hint'].strip().splitlines()[0]}")
    print(f"\n{result['ok']}/{result['total']} 통과 → {'합격' if result['passed'] else '불합격'}")
    print(f"결과: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
