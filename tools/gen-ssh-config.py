#!/usr/bin/env python3
"""
설계 -> 교육생용 ~/.ssh/config 생성 (ProxyJump 경유).

usage:  python3 tools/gen-ssh-config.py --lab 1 > ssh-config-lab1
교육생은 이 내용을 ~/.ssh/config 에 붙이면 `ssh pc1` 한 줄로 접속된다.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(lab_id):
    jump = L.IPAM["access"]["jump_host"]["office_ip"]
    out = [
        f"# ============================================================",
        f"#  my-network-lab  ·  lab{lab_id}  접속 설정",
        f"#  자동 생성: python3 tools/gen-ssh-config.py --lab {lab_id}",
        f"#  ~/.ssh/config 에 추가하면 `ssh pc1` 만으로 접속된다.",
        f"# ============================================================",
        "",
        f"Host lab{lab_id}-jump",
        f"    HostName {jump}",
        f"    User trainee",
        "",
        f"# 랩 노드 공통 설정 — 점프 호스트 경유",
        f"Host " + " ".join(n["name"] for n in L.TOPO["nodes"]),
        f"    User lab",
        f"    ProxyJump lab{lab_id}-jump",
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
    main(int(a[a.index("--lab") + 1]) if "--lab" in a else 1)
