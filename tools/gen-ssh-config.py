#!/usr/bin/env python3
"""
설계 -> 교육생용 ~/.ssh/config 생성 (ProxyJump 경유).

usage:  python3 tools/gen-ssh-config.py --lab 1 [--user user01] > ssh-config-lab1

--user 를 주면 점프 계정을 그 이름으로 쓴다 (tools/gen-jumpaccess.py 가 만드는 계정).
생략하면 site.yml 의 access.jump_host.user 를 쓴다.
교육생은 이 내용을 ~/.ssh/config 에 붙이면 `ssh pc1` 한 줄로 접속된다.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(lab_id, user=None):
    A = L.IPAM["access"]
    jump = A["jump_host"]["office_ip"]
    jump_user = user or A["jump_host"]["user"]
    lab_user = A["lab_user"]
    out = [
        f"# ============================================================",
        f"#  my-network-lab  ·  lab{lab_id}  접속 설정",
        f"#  자동 생성: python3 tools/gen-ssh-config.py --lab {lab_id}",
        f"#  이 내용을 SSH 설정에 붙이면 `ssh pc1` 만으로 접속된다.",
        f"#",
        f"#  Windows (PowerShell):",
        f"#    New-Item -ItemType Directory -Force $HOME\\.ssh | Out-Null",
        f"#    Get-Content $HOME\\Downloads\\ssh-config-lab{lab_id} | Add-Content $HOME\\.ssh\\config",
        f"#",
        f"#  macOS / Linux:",
        f"#    mkdir -p ~/.ssh",
        f"#    cat ~/Downloads/ssh-config-lab{lab_id} >> ~/.ssh/config && chmod 600 ~/.ssh/config",
        f"# ============================================================",
        "",
        f"Host lab{lab_id}-jump",
        f"    HostName {jump}",
        f"    User {jump_user}",
        # 이 랩은 어디서도 비밀번호로 로그인하지 않는다. 끄지 않으면 키가 실패했을 때
        # 조용히 비밀번호를 묻고, 교육생은 "무슨 비밀번호?" 에서 막힌다.
        # 꺼 두면 이유가 그대로 나온다: Permission denied (publickey).
        "    PasswordAuthentication no",
        "    PreferredAuthentications publickey",
        "",
        f"# 랩 노드 공통 설정 — 점프 호스트 경유",
        f"Host " + " ".join(n["name"] for n in L.TOPO["nodes"]),
        f"    User {lab_user}",
        f"    ProxyJump lab{lab_id}-jump",
        "    PasswordAuthentication no",
        "    PreferredAuthentications publickey",
        f"    # 랩은 자주 재생성되므로 호스트 키 검증을 끈다 (교육 환경 전용)",
        f"    StrictHostKeyChecking no",
        f"    UserKnownHostsFile /dev/null",
        f"    LogLevel ERROR",
        "",
    ]
    for n in L.TOPO["nodes"]:
        out += [f"Host {n['name']}", f"    HostName {L.mgmt_ip(lab_id, n['name'])}", ""]
    out += [
        "# ---- 자주 쓰는 명령 ----",
        "#  원격 캡처를 내 PC 의 Wireshark 로:",
        "#    ssh r1 \"sudo tcpdump -i eth2 -U -s0 -w - not port 22\" | wireshark -k -i -",
        "#    (not port 22 를 빼면 자기 SSH 가 캡처돼 무한 루프가 된다)",
        "#  pcap 회수:  scp r1:/tmp/capture.pcap .",
        "",
    ]
    print("\n".join(out))


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--lab") + 1]) if "--lab" in a else 1,
         a[a.index("--user") + 1] if "--user" in a else None)
