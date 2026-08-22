#!/usr/bin/env python3
"""
교육생 Proxmox 콘솔 계정 생성 절차 — Proxmox 호스트에서 쓴다.

usage:  python3 tools/gen-console-access.py [--lab N]
출력:   dist/console-access.sh   (root 로 실행)

왜 필요한가
  SSH 는 키로 들어가지만, **자기가 관리 링크를 내리면 SSH 자체가 죽는다.**
  그때 되돌릴 유일한 길이 콘솔(화면 직접 접속)이다. M0 실습 5 가 그것을 가르친다.
  실무에서 콘솔 서버가 없으면 차를 타고 현장에 가야 하는 상황과 같다.

  콘솔은 키를 쓸 수 없다. 그래서 두 가지가 필요하다.
    ① 랩 노드 `lab` 계정의 비밀번호  — var/console.db 에 있고 콘솔 화면이 알려준다
    ② Proxmox 로그인 계정            — 이 스크립트가 만든다

무엇을 주는가
  교육생마다 Proxmox 계정을 만들고 **자기 랩 VM 13대의 콘솔만** 열 수 있게 한다.
  · VM.Console  화면 접속
  · VM.Audit    목록에서 자기 VM 을 보기 위해
  VM 을 끄거나 지우거나 설정을 바꾸는 권한은 주지 않는다 — 그건 웹 콘솔 버튼의 일이다.

이 스크립트는 **파일만 만든다.** 적용 시점은 관리자가 정한다.
"""
import argparse
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import labdesign as L      # noqa: E402

ROLE = "LabConsole"
REALM = "pve"
# 화면에 불러 주기 쉬운 문자만. 콘솔 로그인은 붙여넣기가 안 되는 경우가 많다.
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_password(n=12):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def students(lab_ids):
    if not (L.ROOT / "var/console.db").exists():
        return []
    import db                                          # noqa: PLC0415
    out = []
    for lab in lab_ids:
        with db.connect() as con:
            rows = con.execute(
                "SELECT username, name FROM users "
                " WHERE lab_id=? AND role='user' AND disabled=0 ORDER BY username",
                (lab,)).fetchall()
        out += [{"lab_id": lab, "username": r["username"], "name": r["name"]} for r in rows]
    return out


def main(lab, outdir):
    lo, hi = L.IPAM["labs"]["id_range"]
    labs = [lab] if lab else list(range(lo, L.IPAM["labs"]["default_count"] + 1))
    rows = students(labs)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    sh = ["#!/bin/bash",
          "# =============================================================================",
          "#  교육생 Proxmox 콘솔 계정 — Proxmox 호스트에서 root 로 실행",
          "#  자동 생성: python3 tools/gen-console-access.py   (직접 수정하지 말 것)",
          "# =============================================================================",
          "#  각 계정은 **자기 랩 VM 의 콘솔만** 열 수 있다.",
          "#  VM 을 끄거나 지우거나 설정을 바꾸지는 못한다 — 그건 웹 콘솔 버튼의 일이다.",
          "#",
          "#  비밀번호는 이 실행에서 한 번만 출력된다. 다시 실행하면 새로 발급된다.",
          "set -euo pipefail",
          '[ "$(id -u)" -eq 0 ] || { echo "root 로 실행할 것" >&2; exit 1; }',
          "",
          "# --- 역할 -------------------------------------------------------------------",
          f'if pveum role list --output-format json | grep -q \'"{ROLE}"\'; then',
          f'  pveum role modify {ROLE} -privs "VM.Console,VM.Audit"',
          "else",
          f'  pveum role add {ROLE} -privs "VM.Console,VM.Audit"',
          "fi",
          f'echo "역할 {ROLE} 준비 (VM.Console, VM.Audit)"',
          ""]

    if not rows:
        sh += ['echo "만들 계정이 없다."',
               'echo "  콘솔에 교육생 계정을 먼저 만들 것: tools/console-user.py add <id> --lab <N>"',
               "exit 0"]
    for r in rows:
        u, lab_id = r["username"], r["lab_id"]
        uid = f"{u}@{REALM}"
        pw = gen_password()
        vmids = [L.vmid(lab_id, n["name"]) for n in L.TOPO["nodes"]]
        sh += [f'# ---- {u} (lab{lab_id}{" · " + r["name"] if r["name"] else ""}) ----',
               f'if pveum user list --output-format json | grep -q \'"{uid}"\'; then',
               f'  pveum user modify {uid} --password \'{pw}\' --comment "my-network-lab lab{lab_id}"',
               "else",
               f'  pveum user add {uid} --password \'{pw}\' --comment "my-network-lab lab{lab_id}"',
               "fi"]
        # 자기 랩 VM 에만. /vms 전체에 주면 남의 랩 화면이 열린다.
        for vmid in vmids:
            sh += [f'pveum aclmod /vms/{vmid} -user {uid} -role {ROLE} >/dev/null']
        sh += [f'echo "  {uid:<24s} lab{lab_id} · VM {vmids[0]}~{vmids[-1]} · 비밀번호 {pw}"',
               ""]

    if rows:
        sh += ['echo',
               f'echo "계정 {len(rows)}개. 위 비밀번호를 교육생에게 전달할 것 (다시 볼 수 없다)."',
               'echo "로그인: Proxmox 웹 → Realm 을 \'Linux PAM\' 이 아니라 \'Proxmox VE authentication server\' 로"']
    (out / "console-access.sh").write_text("\n".join(sh) + "\n", encoding="utf-8")
    (out / "console-access.sh").chmod(0o750)

    print(f"generated {out}/console-access.sh")
    for r in rows:
        print(f"  {r['username']}@{REALM:<8s} lab{r['lab_id']}  {r['name']}")
    if not rows:
        print("  (콘솔에 등록된 교육생이 없다)")
    print("\n  랩 노드 `lab` 계정 비밀번호는 따로다 — 교육생 콘솔의 [접속 키] 화면에 나온다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="교육생 Proxmox 콘솔 계정 생성 절차")
    ap.add_argument("--lab", type=int, help="이 랩만 (없으면 전체)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    main(a.lab, a.out)
