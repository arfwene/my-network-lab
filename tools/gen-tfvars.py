#!/usr/bin/env python3
"""
설계 -> Terraform 변수(JSON) 생성.

usage:  python3 tools/gen-tfvars.py --lab 1 [--stage m10]
출력:   infra/terraform/envs/lab<N>/lab.auto.tfvars.json

Terraform 이 YAML 을 직접 파싱하지 않게 한다. 주소 조합 로직은 labdesign.py 한 곳에만 둔다.
"""
import shutil
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(lab_id, stage):
    nodes = L.all_nodes(lab_id, stage)
    A = L.IPAM["access"]
    data = {
        "lab_id": lab_id,
        "pve_endpoint": A["proxmox"]["api_endpoint"],
        "pve_node": A["proxmox"]["node_name"],
        "pve_insecure": A["proxmox"]["insecure_tls"],
        "datastore_id": A["proxmox"]["datastore"],
        "lab_user": A["lab_user"],
        "ssh_public_keys": A["ssh_public_keys"],
        "lab_stage": stage,
        "template_vmid": L.IPAM["labs"]["template_vmid"],
        "mgmt_bridge": L.mgmt_bridge_name(),
        "mgmt_vlan": L.mgmt_vlan(lab_id),
        "mgmt_cidr": L.mgmt_cidr(lab_id),
        "mgmt_gateway": L.mgmt_gateway(lab_id),
        "bridges": [
            {"name": b["name"], "comment": f'lab{lab_id} {b["alias"]} — {b["desc"]}'}
            for b in L.all_bridges(lab_id, stage)
        ],
        "nodes": [
            {
                "name": c["node"],
                "vm_name": c["vm_name"],
                "vmid": c["vmid"],
                "cores": c["cores"],
                "memory": c["memory"],
                "disk": c["disk"],
                "role": c["node_role"],
                "desc": c["desc"],
                "mgmt_ip": c["mgmt_ip"],
                "mgmt_prefixlen": c["mgmt_prefixlen"],
                "mgmt_mac": c["mgmt_mac"],
                # net0 = 관리망, net1.. = 랩 링크 (물리 NIC 이 필요한 것만)
                #   관리망만 VLAN 태그를 단다. 전 랩이 같은 브리지를 쓰고 VLAN 으로 갈린다.
                #   태깅은 브리지가 하므로 게스트는 untagged 만 본다 — 노드 설정은 랩마다 동일하다.
                "nics": [{"bridge": L.mgmt_bridge_name(), "mac": c["mgmt_mac"],
                          "purpose": "mgmt", "vlan_id": L.mgmt_vlan(lab_id)}]
                        + [{"bridge": i["bridge"], "mac": i["mac"], "purpose": i["name"],
                            "vlan_id": None}
                           for i in c["lab_interfaces"]
                           if i.get("bridge") and i["active"]],
            }
            for c in nodes
        ],
    }
    out = L.ROOT / f"infra/terraform/envs/lab{lab_id}"
    out.mkdir(parents=True, exist_ok=True)
    # 랩마다 같은 main.tf 를 쓴다. 손으로 복사하지 않도록 여기서 배치한다.
    shutil.copy2(L.ROOT / "infra/terraform/envs/_template/main.tf", out / "main.tf")
    (out / "lab.auto.tfvars.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    total_mem = sum(n["memory"] for n in data["nodes"])
    print(f"generated {out}/lab.auto.tfvars.json  "
          f"(nodes={len(data['nodes'])}, bridges={len(data['bridges'])}, RAM={total_mem}MB)")


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--lab") + 1]) if "--lab" in a else 1,
         a[a.index("--stage") + 1] if "--stage" in a else "m10")
