#!/usr/bin/env python3
"""
Proxmox 호스트에 적용할 방화벽 규칙 생성 (선택 적용).

usage:  python3 tools/render-host-guard.py [--labs 6]
출력:   dist/host-guard.nft

왜 필요한가
  랩 VM 의 관리망(mgmt0)은 Proxmox 호스트와 같은 브리지에 있다. 호스트가 그 브리지에
  주소를 갖고 있으므로, 교육생은 랩 VM 에서 하이퍼바이저 IP 로 접속을 시도할 수 있다.
  랩은 "망가뜨리며 배우는" 환경이다. 그 반경이 하이퍼바이저까지 넓어지면 안 된다.

무엇을 막는가
  1. 랩 관리망 → 호스트 자신     (SSH·8006 등 모든 신규 연결)
  2. 랩 관리망 → 호스트를 거친 외부 (호스트가 포워딩을 켜 두었을 경우의 사내망 유출)
  허용은 그대로 둔다: 호스트가 **먼저 연 연결의 응답**(established)은 통과시켜야
  점프 호스트·Ansible 이 동작한다.

이 프로젝트는 호스트 설정을 자동으로 건드리지 않는다. 적용 여부와 시점은 관리자가 정한다.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(labs):
    # 관리망은 브리지 하나를 랩별 VLAN 으로 나눈 것이다.
    # 호스트가 관리망에 주소를 준다면 그 인터페이스는 <브리지>.<VLAN> 형태가 된다.
    # 이 규칙은 그 인터페이스들을 대상으로 한다 (주소를 주지 않았다면 애초에 도달 자체가 없다).
    ms = L.mgmt_labs(labs)
    br = L.mgmt_bridge_name()
    bridges = [f"{br}.{m['vlan']}" for m in ms]
    lst = ", ".join(f'"{b}"' for b in bridges)
    fwd = "\n".join(f'        iifname "{b}" oifname != "{b}" drop' for b in bridges)
    nets = ", ".join(m["cidr"] for m in ms)

    out = f"""#!/usr/sbin/nft -f
# =============================================================================
#  my-network-lab — Proxmox 호스트 보호 규칙  (자동 생성: tools/render-host-guard.py)
# =============================================================================
#  랩 관리망에서 하이퍼바이저로 들어오는 신규 연결을 막는다.
#  랩은 망가뜨리며 배우는 환경이다. 그 반경이 호스트까지 넓어지면 안 된다.
#
#  대상 인터페이스 : {', '.join(bridges)}
#                   (관리망 브리지 {br} 의 랩별 VLAN 서브인터페이스)
#  대상 대역   : {nets}
#
#  적용 (Proxmox 호스트에서, root):
#      nft -f host-guard.nft          # 즉시 적용 (재부팅하면 사라진다)
#      cp host-guard.nft /etc/nftables.d/ && systemctl enable --now nftables
#
#  해제:
#      nft delete table inet labguard
#
#  ▸ 기존 규칙을 지우지 않는다. 별도 테이블을 더할 뿐이므로 Proxmox 방화벽이나
#    다른 VM 의 정책에는 영향이 없다. policy 는 accept 이고 drop 만 추가한다.
#  ▸ 호스트가 **먼저 연** 연결의 응답은 통과시킨다. 이게 없으면 점프 호스트와
#    Ansible 이 랩 노드에 붙지 못한다.
# =============================================================================

table inet labguard {{
    chain input {{
        type filter hook input priority filter; policy accept;

        ct state established,related accept
        # 랩 관리망에서 호스트로 새로 여는 연결은 전부 막는다 (SSH·8006·그 외 전부)
        iifname {{ {lst} }} ct state new drop
    }}

    chain forward {{
        type filter hook forward priority filter; policy accept;

        ct state established,related accept
        # 호스트가 포워딩을 켜 두었더라도 관리망이 그 브리지 밖으로 나가지 못하게 한다.
        # VLAN 마다 따로 쓰는 이유: 같은 랩 안(운영 서버 ↔ 랩 노드)은 살려야 하고,
        # 다른 랩 관리망으로 넘어가는 것은 막아야 하기 때문이다.
{fwd}
    }}
}}
"""
    dst = L.ROOT / "dist/host-guard.nft"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(f"generated {dst}  (관리망 {br} · VLAN {len(bridges)}개)")


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--labs") + 1]) if "--labs" in a
         else L.SITE["labs"]["default_count"])
