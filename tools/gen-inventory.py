#!/usr/bin/env python3
"""
설계 -> Ansible 인벤토리 생성.

usage:  python3 tools/gen-inventory.py --lab 1 [--stage m10]

출력: infra/ansible/inventory/lab<N>/{hosts.yml, group_vars/all.yml, host_vars/<node>.yml}
"""
import sys, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

GROUP_OF = {"router": "routers", "switch": "switches", "host": "hosts_pc",
            "server": "servers", "edge": "edges"}


def _proxy_args():
    """랩 관리망에 직접 닿지 못하는 서버를 위한 경유(ProxyJump) 설정.

    관리망 브리지에 발을 걸친 서버라면 아무것도 넣지 않는다 — 경유는 느리고
    Proxmox 호스트를 SSH 경로에 끌어들이므로, 필요한 경우에만 켠다.
    """
    j = L.IPAM["access"]["jump_host"]
    if not j.get("proxy_via_proxmox"):
        return {}
    host = L.IPAM["access"]["proxmox"]["host_ip"]
    user = j.get("proxmox_ssh_user") or "root"
    # -W 를 쓰는 ProxyJump 다. 경유 호스트에는 아무것도 설치하지 않는다.
    return {"ansible_ssh_common_args":
            f"-o ProxyJump={user}@{host} -o StrictHostKeyChecking=accept-new"}


def main(lab_id, stage):
    # 노드는 전부 포함한다 — 배선은 이미 끝나 있고, 관리망 접속은 단계와 무관하게 가능해야 한다.
    # 각 노드의 node_active 가 "이 단계에서 설정을 올리는가"를 결정한다.
    nodes = [L.node_config(lab_id, n, stage) for n in L.TOPO["nodes"]]
    base = L.ROOT / f"infra/ansible/inventory/lab{lab_id}"
    (base / "host_vars").mkdir(parents=True, exist_ok=True)
    (base / "group_vars").mkdir(parents=True, exist_ok=True)

    # 접속 주소를 반드시 못 박는다.
    #   노드 이름은 web · dns · ftp · edge 처럼 흔한 이름이다. ansible_host 가 없으면
    #   Ansible 이 컨트롤러의 DNS(검색 도메인 포함)로 이 이름을 풀어 버린다.
    #   사내에 같은 이름의 실제 서버가 있으면 랩 설정 플레이북이 그 서버로 날아간다.
    #   (hostname 변경 · netplan 재작성 · nftables 설치 — 되돌릴 수 없는 사고)
    groups = {}
    for c in nodes:
        groups.setdefault(GROUP_OF[c["node_role"]], {}).setdefault("hosts", {})[c["node"]] = {
            "ansible_host": c["mgmt_ip"],
        }
    hosts = {"all": {"children": groups}}
    (base / "hosts.yml").write_text(
        "# 자동 생성 (tools/gen-inventory.py). 직접 수정하지 말 것.\n"
        + yaml.safe_dump(hosts, allow_unicode=True, sort_keys=False), encoding="utf-8")

    common = {
        "lab_id": lab_id,
        "lab_stage": stage,
        "stages": L.STAGES,
        "ansible_user": "lab",
        "ansible_python_interpreter": "/usr/bin/python3",
        **_proxy_args(),
        "mgmt": {
            "cidr": L.mgmt_cidr(lab_id),
            "gateway": L.mgmt_gateway(lab_id),
            "interface": L.IPAM["management"]["interface"],
        },
        "vlans": L.TOPO["vlans"],
        "ipv4": L.IPAM["ipv4"],
        "ipv6": L.IPAM["ipv6"],
        "public": L.IPAM["public"],
        "dns": L.IPAM["dns"],
        "service_ports": L.IPAM["services"],   # 노드의 services(list)와 충돌 방지
        "lab_domain": L.TOPO["lab"]["domain"],
        "routing": L.ROUTING,
    }
    (base / "group_vars/all.yml").write_text(
        "# 자동 생성 (tools/gen-inventory.py). 직접 수정하지 말 것.\n"
        + yaml.safe_dump(common, allow_unicode=True, sort_keys=False), encoding="utf-8")

    for c in nodes:
        c = dict(c)
        c["static_routes_m3"] = L.static_routes(c["node"])
        c["edge_routing"] = L.edge_routes(c["node"])
        c["ospf_enabled"] = c["node"] in L.ROUTING["ospf"]["routers"]
        c["ospf_passive"] = L.ROUTING["ospf"].get("passive_interfaces", {}).get(c["node"], [])
        (base / f"host_vars/{c['node']}.yml").write_text(
            "# 자동 생성 (tools/gen-inventory.py). 직접 수정하지 말 것.\n"
            + yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"generated {base}  (nodes={len(nodes)}, stage={stage})")


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--lab") + 1]) if "--lab" in a else 1,
         a[a.index("--stage") + 1] if "--stage" in a else "m10")
