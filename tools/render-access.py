#!/usr/bin/env python3
"""
접근 설계 문서 생성.  config/site.yml -> dist/access.md

실제 환경 값이 들어가므로 dist/ 에 쓴다 (git 제외).
"""
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

A = L.IPAM["access"]
LB = L.IPAM["labs"]
nodes = L.all_nodes(1)
ram = sum(n["memory"] for n in nodes)

ctx = dict(
    office_lan=A["office_lan"],
    proxmox_ip=A["proxmox"]["host_ip"],
    console_url=A["proxmox"]["api_endpoint"],
    jump_office_ip=A["jump_host"]["office_ip"],
    jump_user=A["jump_host"]["user"],
    lab_user=A["lab_user"],
    mgmt_bridge=L.mgmt_bridge_name(),
    mgmt_vlan=L.mgmt_vlan(1),
    mgmt_cidr=L.mgmt_cidr(1),
    mgmt_gw=L.mgmt_gateway(1),
    mgmt_if=L.IPAM["management"]["interface"],
    mgmt_prefix=L.IPAM["management"]["subnet_prefix"],
    mgmt_supernet=L.SITE["networks"]["management"],
    jump_lab_ip=L.jump_ip(1),
    mgmt_hosts=[(n["node"], n["mgmt_ip"]) for n in nodes[:4]],
    lab_block=L.IPAM["ipv4"]["lab_block"],
    node_count=len(nodes),
    naming_bridge=LB["naming"]["bridge"],
    naming_vm=LB["naming"]["vm_name"],
    vmid_base=LB["vmid_base"],
    vmid_start=LB.get("vmid_start", 0),
    vmid_range=L.vmid_range(3),
    example_bridge=L.bridge_name(3, "101"),
    example_vmid=L.vmid(3, "pc1"),
    example_vm=L.vm_name(3, "pc1"),
    example_mgmt=L.mgmt_ip(3, "pc1"),
    example_pc1=L.IPAM["ipv4"]["segments"]["vlan10"]["hosts"]["pc1"],
    jump_nics=[(i, f'{L.mgmt_bridge_name()} VLAN {L.mgmt_vlan(i)}', L.jump_ip(i))
               for i in range(1, LB["default_count"] + 1)],
    lab_ram_gb=round(ram / 1024, 2),
    total_ram_gb=round(ram * LB["default_count"] / 1024, 1),
    default_count=LB["default_count"],
    max_labs=LB["id_range"][1],
)

env = Environment(loader=FileSystemLoader(L.ROOT / "docs/templates"),
                  undefined=StrictUndefined, keep_trailing_newline=True)
out = L.ROOT / "dist/access.md"
out.parent.mkdir(exist_ok=True)
out.write_text(env.get_template("access.md.j2").render(**ctx), encoding="utf-8")
print(f"generated {out}")
