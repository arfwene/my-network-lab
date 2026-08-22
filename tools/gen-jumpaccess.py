#!/usr/bin/env python3
"""
교육생 점프 계정 생성 절차 생성 — 운영 서버에서 쓴다.

usage:  python3 tools/gen-jumpaccess.py [--lab N] [--out dist]
출력:   dist/jump-access.sh     계정 생성/갱신 스크립트 (root 로 실행)
        dist/jump-access.conf   sshd 설정 조각

왜 필요한가
  랩 노드는 사무실에서 직접 보이지 않는다. 교육생은 운영 서버를 ProxyJump 로 거쳐 들어온다.
  그런데 운영 서버는 콘솔·Terraform·Ansible 이 도는 장비다. 여기에 교육생 **셸**을 주면

    · 모듈 해설(answers.md)과 캡스톤 장애 대응표를 읽을 수 있고
    · 다른 랩의 관리망으로 그대로 나갈 수 있다 (운영 서버는 전 랩에 발을 걸치고 있다)

  그래서 이 계정은 **셸이 없고, 자기 랩 노드 22번으로만 나갈 수 있다.**
    · shell = nologin + ForceCommand   → 대화형 접속 불가
    · PermitOpen = 자기 랩 13대:22     → 남의 랩으로 못 나간다
    · -W 방식(ProxyJump)은 세션을 열지 않으므로 셸이 없어도 동작한다

  공개키는 교육생이 콘솔 [접속 키] 에 등록한 그 키를 그대로 쓴다 — 관리자가 옮겨 적지 않는다.

이 스크립트는 **파일만 만든다.** 적용 시점은 관리자가 정한다 (호스트 설정을 자동으로 바꾸지 않는다).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import labdesign as L      # noqa: E402

SSHD_SNIPPET = "/etc/ssh/sshd_config.d/60-lab-jump.conf"


def students(lab_ids):
    """콘솔에 등록된 교육생 중 **공개키가 있는** 사람만."""
    if not (L.ROOT / "var/console.db").exists():
        return []
    import db                                          # noqa: PLC0415
    out = []
    for lab in lab_ids:
        for k in db.lab_keys(lab):
            out.append({"lab_id": lab, **k})
    return out


RESERVED = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
            "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "nobody",
            "sshd", "systemd-network", "ubuntu", "admin", "lab", "trainee"}


def main(lab, outdir):
    lo, hi = L.IPAM["labs"]["id_range"]
    labs = [lab] if lab else list(range(lo, L.IPAM["labs"]["default_count"] + 1))
    rows = students(labs)
    clash = [r["username"] for r in rows if r["username"].lower() in RESERVED]
    if clash:
        sys.exit(f"거부: 시스템 계정과 이름이 겹친다: {', '.join(clash)}\n"
                 f"  이 이름으로 점프 계정을 만들면 그 시스템 계정을 망가뜨린다.\n"
                 f"  콘솔에서 다른 이름으로 바꿀 것")
    per_lab = {n: [L.mgmt_ip(n, x["name"]) for x in L.TOPO["nodes"]] for n in labs}
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 스크립트
    sh = ["#!/bin/bash",
          "# =============================================================================",
          "#  교육생 점프 계정 — 운영 서버에서 root 로 실행",
          "#  자동 생성: python3 tools/gen-jumpaccess.py   (직접 수정하지 말 것)",
          "# =============================================================================",
          "#  이 계정들은 **셸이 없다.** ProxyJump 통로로만 쓰인다.",
          f"#  sshd 제한은 {SSHD_SNIPPET} 에 들어간다 — 같이 배치할 것.",
          "#",
          "#  다시 실행해도 안전하다 (있으면 키만 갱신한다).",
          "set -euo pipefail",
          '[ "$(id -u)" -eq 0 ] || { echo "root 로 실행할 것" >&2; exit 1; }',
          "",
          'NOLOGIN=$(command -v nologin || echo /usr/sbin/nologin)',
          'TAG="my-network-lab"          # 우리가 만든 계정 표시 (GECOS)',
          "",
          "# 같은 이름의 기존 계정을 가로채지 않는다.",
          "#   운영 서버에는 관리자 계정도 있다. 콘솔 계정 이름이 우연히 겹치면",
          "#   그 사람의 셸을 nologin 으로 바꿔 로그인을 막아 버린다. 되돌리기 전까지 못 들어온다.",
          "guard() {",
          '  local u="$1"',
          '  id -u "$u" >/dev/null 2>&1 || return 0          # 없으면 새로 만들면 된다',
          '  if getent passwd "$u" | cut -d: -f5 | grep -q "$TAG"; then return 0; fi',
          '  echo "중단: 계정 $u 이(가) 이미 있고 이 랩이 만든 것이 아니다." >&2',
          '  echo "       그대로 두면 그 계정의 로그인이 막힌다." >&2',
          '  echo "       콘솔에서 교육생 계정 이름을 겹치지 않게 바꿀 것." >&2',
          '  exit 1',
          "}",
          "",
          ""]

    if not rows:
        sh += ['echo "만들 계정이 없다."',
               'echo "  교육생이 콘솔 [접속 키] 에서 공개키를 등록하면 여기에 나타난다."',
               'echo "  등록 현황: python3 tools/console-user.py keys --lab <N>"',
               "exit 0"]
    for r in rows:
        u = r["username"]
        sh += [f'# ---- {u} (lab{r["lab_id"]}{" · " + r["name"] if r["name"] else ""}) ----',
               f'guard {u}',
               f'if id -u {u} >/dev/null 2>&1; then',
               f'  usermod -s "$NOLOGIN" -c "$TAG lab{r["lab_id"]}" {u}',
               f'else',
               f'  useradd -m -s "$NOLOGIN" -c "$TAG lab{r["lab_id"]}" {u}',
               f'fi',
               f'install -d -m 700 -o {u} -g {u} /home/{u}/.ssh',
               f'cat > /home/{u}/.ssh/authorized_keys <<\'KEY\'',
               r["key"],
               "KEY",
               f'chown {u}:{u} /home/{u}/.ssh/authorized_keys',
               f'chmod 600 /home/{u}/.ssh/authorized_keys',
               # 비밀번호 로그인은 막는다. 키로만 들어온다.
               f'passwd -l {u} >/dev/null',
               f'echo "  {u:<16s} lab{r["lab_id"]} · 키 1개"',
               ""]

    if rows:
        sh += ['echo',
               f'echo "계정 {len(rows)}개 처리했다."',
               f'echo "다음: {SSHD_SNIPPET} 배치 후  sshd -t && systemctl reload ssh"']
    (out / "jump-access.sh").write_text("\n".join(sh) + "\n", encoding="utf-8")
    (out / "jump-access.sh").chmod(0o750)

    # ---------------------------------------------------------------- sshd 조각
    cf = ["# =============================================================================",
          "#  교육생 점프 접속 제한 — 자동 생성: tools/gen-jumpaccess.py",
          "# =============================================================================",
          f"#  배치: {SSHD_SNIPPET}",
          "#  적용: sshd -t && systemctl reload ssh      ← -t 로 먼저 검사할 것.",
          "#        틀린 설정을 reload 하면 지금 열린 세션 말고는 아무도 못 들어온다.",
          "#",
          "#  각 계정은 **자기 랩 노드 22번으로만** 나갈 수 있고 셸을 얻지 못한다.",
          "#  ProxyJump(-W)는 세션이 아니라 direct-tcpip 채널이라 셸 없이도 동작한다.",
          ""]
    for r in rows:
        u, lab = r["username"], r["lab_id"]
        targets = " ".join(f"{ip}:22" for ip in per_lab[lab])
        cf += [f"# {u} — lab{lab}" + (f" ({r['name']})" if r["name"] else ""),
               f"Match User {u}",
               f"    PermitOpen {targets}",
               "    AllowTcpForwarding yes",
               "    ForceCommand /usr/sbin/nologin",
               "    PermitTTY no",
               "    X11Forwarding no",
               "    AllowAgentForwarding no",
               "    PermitTunnel no",
               ""]
    if not rows:
        cf += ["# (등록된 교육생 키가 없다 — 콘솔 [접속 키] 등록 후 다시 생성할 것)", ""]
    # Match 블록은 다음 Match 또는 파일 끝이 아니라 **파싱 끝까지** 이어진다.
    # sshd_config.d 는 보통 sshd_config 앞부분에서 include 되므로, 여기서 닫지 않으면
    # 나머지 전역 설정이 전부 마지막 Match 안으로 들어가 버린다.
    cf += ["# 전역 문맥으로 되돌린다. 이 줄을 지우지 말 것 —",
           "# 없으면 아래(=include 이후)의 모든 설정이 마지막 Match 안에 갇힌다.",
           "Match all", ""]
    (out / "jump-access.conf").write_text("\n".join(cf), encoding="utf-8")

    print(f"generated {out}/jump-access.sh  ·  {out}/jump-access.conf")
    if rows:
        for r in rows:
            print(f"  {r['username']:<16s} lab{r['lab_id']}  {r['name']}")
    else:
        print("  (등록된 교육생 키가 없다 — 콘솔 [접속 키] 에서 등록하면 여기 나온다)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="교육생 점프 계정 생성 절차")
    ap.add_argument("--lab", type=int, help="이 랩만 (없으면 전체)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    main(a.lab, a.out)
