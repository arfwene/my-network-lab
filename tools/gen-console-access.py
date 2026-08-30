#!/usr/bin/env python3
"""
교육생 Proxmox 콘솔 계정 절차 — Proxmox 호스트에서 쓴다.

usage:  python3 tools/gen-console-access.py [--lab N]
출력:   dist/console-access.sh   (Proxmox 호스트에서 root 로 실행)

왜 필요한가
  SSH 는 키로 들어가지만, **자기가 관리 링크를 내리면 SSH 자체가 죽는다.**
  그때 되돌릴 유일한 길이 콘솔(화면 직접 접속)이다. M0 실습 5 가 그것을 가르친다.
  실무에서 콘솔 서버가 없으면 차를 타고 현장에 가야 하는 상황과 같다.

  콘솔은 키를 쓸 수 없다. 그래서 두 가지가 필요하다.
    ① 랩 노드 `lab` 계정의 비밀번호  — var/console.db 에 있고 콘솔 화면이 알려준다
    ② Proxmox 로그인 계정            — 이 스크립트가 만든다

왜 **랩당 1계정**인가 (교육생 1인 1계정이 아니라)
  · 전체를 하나로 통일하면 남의 랩 화면이 열린다. 노드 `lab` 비밀번호는 전 랩
    공용이라, 화면만 열리면 바로 로그인된다 — 랩 격리도 시험도 그 자리에서 끝난다.
  · 1인 1계정은 교육생이 늘 때마다 Proxmox 호스트에서 root 작업이 생긴다.
    그게 "매번 손으로 해 주는" 일의 절반이었다.
  · 같은 랩 교육생은 어차피 같은 VM 전부를 함께 쓴다. 랩 경계 안에서 계정을
    나눠 봐야 지키는 것이 없다.
  결과: 만들 일은 **랩을 늘릴 때뿐**이고 교육생 수와 무관하다.

무엇을 주는가
  랩마다 계정 하나를 만들고 **그 그 랩 VM 전부의 콘솔만** 열 수 있게 한다.
    · VM.Console  화면 접속
    · VM.Audit    목록에서 자기 VM 을 보기 위해
  VM 을 끄거나 지우거나 설정을 바꾸는 권한은 주지 않는다 — 그건 웹 콘솔 버튼의 일이다.

비밀번호는 var/console.db 에 있고 **교육생 [접속 키] 화면이 자기 랩 것만 보여 준다.**
관리자가 사람마다 전달할 일이 없다. 다시 실행해도 값이 바뀌지 않는다.

이 스크립트는 **파일만 만든다.** 적용 시점은 관리자가 정한다.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import labdesign as L      # noqa: E402

ROLE = "LabConsole"


def lab_rows(lab_ids):
    """[(lab_id, userid, password, [vmid...])] — 비밀번호는 DB 가 갖고 있다."""
    import db                                          # noqa: PLC0415
    out = []
    for lab in lab_ids:
        uid, pw = db.lab_pve_account(lab)
        vmids = [L.vmid(lab, n["name"]) for n in L.TOPO["nodes"]]
        out.append((lab, uid, pw, vmids))
    return out


def students_by_lab(lab_ids):
    """랩마다 등록된 교육생 수 — 안내에만 쓴다."""
    if not (L.ROOT / "var/console.db").exists():
        return {}
    import db                                          # noqa: PLC0415
    with db.connect() as con:
        rows = con.execute(
            "SELECT lab_id, COUNT(*) n FROM users "
            " WHERE role='user' AND disabled=0 GROUP BY lab_id").fetchall()
    return {r["lab_id"]: r["n"] for r in rows if r["lab_id"] in lab_ids}


def main(lab, outdir):
    lo, _hi = L.IPAM["labs"]["id_range"]
    labs = [lab] if lab else list(range(lo, L.IPAM["labs"]["default_count"] + 1))
    if not (L.ROOT / "var/console.db").exists():
        sys.exit("중단: var/console.db 가 없다. 콘솔을 한 번 띄운 뒤 다시 실행할 것")
    rows = lab_rows(labs)
    counts = students_by_lab(labs)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    sh = ["#!/bin/bash",
          "# =============================================================================",
          "#  교육생 Proxmox 콘솔 계정 — Proxmox 호스트에서 root 로 실행",
          "#  자동 생성: python3 tools/gen-console-access.py   (직접 수정하지 말 것)",
          "# =============================================================================",
          "#  **랩당 1계정**이다. 교육생이 늘어도 다시 실행할 필요가 없다.",
          "#  각 계정은 그 그 랩 VM 전부의 콘솔만 열 수 있다.",
          "#  VM 을 끄거나 지우거나 설정을 바꾸지는 못한다 — 그건 웹 콘솔 버튼의 일이다.",
          "#",
          "#  비밀번호는 콘솔 DB 에 있는 값 그대로다. 교육생은 [접속 키] 화면에서 본다.",
          "#  다시 실행해도 같은 값으로 맞춘다 (교육생에게 다시 알릴 필요가 없다).",
          "set -euo pipefail",
          '[ "$(id -u)" -eq 0 ] || { echo "root 로 실행할 것" >&2; exit 1; }',
          'command -v pveum >/dev/null || { echo "pveum 이 없다. Proxmox 호스트에서 실행할 것" >&2; exit 1; }',
          "",
          "# --- 역할 -------------------------------------------------------------------",
          f'if pveum role list --output-format json | grep -q \'"{ROLE}"\'; then',
          f'  pveum role modify {ROLE} -privs "VM.Console,VM.Audit"',
          "else",
          f'  pveum role add {ROLE} -privs "VM.Console,VM.Audit"',
          "fi",
          f'echo "역할 {ROLE} 준비 (VM.Console, VM.Audit)"',
          ""]

    for lab_id, uid, pw, vmids in rows:
        n = counts.get(lab_id, 0)
        sh += [f'# ---- lab{lab_id} · VM {vmids[0]}~{vmids[-1]} · 교육생 {n}명 ----',
               f'if pveum user list --output-format json | grep -q \'"{uid}"\'; then',
               f'  pveum user modify {uid} --password \'{pw}\' '
               f'--comment "my-network-lab lab{lab_id} 콘솔 (공용)"',
               "else",
               f'  pveum user add {uid} --password \'{pw}\' '
               f'--comment "my-network-lab lab{lab_id} 콘솔 (공용)"',
               "fi"]
        # 자기 랩 VM 에만. /vms 전체에 주면 남의 랩 화면이 열린다.
        for vmid in vmids:
            sh += [f'pveum aclmod /vms/{vmid} -user {uid} -role {ROLE} >/dev/null']
        sh += [f'echo "  {uid:<22s} VM {vmids[0]}~{vmids[-1]} · 교육생 {n}명"', ""]

    sh += ['echo',
           f'echo "랩 {len(rows)}개 계정 준비 완료."',
           'echo "비밀번호는 교육생이 웹 콘솔 [접속 키] 화면에서 직접 본다 — 전달할 것이 없다."',
           'echo "로그인 시 Realm 은 \'Linux PAM\' 이 아니라 \'Proxmox VE authentication server\'"']
    (out / "console-access.sh").write_text("\n".join(sh) + "\n", encoding="utf-8")
    (out / "console-access.sh").chmod(0o750)

    print(f"generated {out}/console-access.sh")
    for lab_id, uid, _pw, vmids in rows:
        print(f"  lab{lab_id}  {uid:<22s} VM {vmids[0]}~{vmids[-1]}  "
              f"교육생 {counts.get(lab_id, 0)}명")
    print("\n  비밀번호는 화면에 찍지 않는다 — 교육생 [접속 키] 화면에 나온다.")
    print("  랩 노드 `lab` 계정 비밀번호도 같은 화면에 있다 (다른 값이다).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="교육생 Proxmox 콘솔 계정 절차 (랩당 1계정)")
    ap.add_argument("--lab", type=int, help="이 랩만 (없으면 전체)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    main(a.lab, a.out)
