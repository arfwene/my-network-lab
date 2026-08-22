#!/usr/bin/env python3
"""
관리망 브리지 Terraform 변수 생성 (1회성 자원).

usage:  python3 tools/gen-mgmt.py [--labs 6]
출력:   infra/terraform/envs/mgmt/{main.tf, mgmt.auto.tfvars.json}

관리망은 **전 랩 공용 VLAN-aware 브리지 하나**다. 랩 N 은 VLAN(vlan_base + N)으로 갈린다.
랩 자원이 아니라서 `terraform destroy` 로 사라지지 않는다 — 운영 서버의 트렁크 NIC 이
거기 꽂혀 있고, 랩을 지웠다 다시 만드는 동안에도 그대로여야 하기 때문이다.
근거는 _mgmt/main.tf 주석과 PLAN 7.9.1.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(labs):
    A = L.IPAM["access"]
    ms = L.mgmt_labs(labs)
    br = L.mgmt_bridge_name()
    data = {
        "pve_endpoint": A["proxmox"]["api_endpoint"],
        "pve_node": A["proxmox"]["node_name"],
        "pve_insecure": A["proxmox"]["insecure_tls"],
        "mgmt_bridge": br,
        # 소유 표시를 남긴다 — 배포 전 검사(pve.preflight)가 "남의 브리지"와 구분한다.
        "comment": (f"my-network-lab mgmt (VLAN-aware) — "
                    f"랩 {ms[0]['lab_id']}~{ms[-1]['lab_id']} = VLAN "
                    f"{ms[0]['vlan']}~{ms[-1]['vlan']}"),
        "vlans": [{"lab_id": m["lab_id"], "vlan": m["vlan"], "cidr": m["cidr"]} for m in ms],
    }
    out = L.ROOT / "infra/terraform/envs/mgmt"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(L.ROOT / "infra/terraform/envs/_mgmt/main.tf", out / "main.tf")
    (out / "mgmt.auto.tfvars.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"generated {out}/mgmt.auto.tfvars.json  "
          f"(브리지 {br} · 랩 {len(ms)}개 = VLAN {ms[0]['vlan']}~{ms[-1]['vlan']})")


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--labs") + 1]) if "--labs" in a else None)
