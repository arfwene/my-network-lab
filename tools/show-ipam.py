#!/usr/bin/env python3
"""계산된 주소 계획을 표로 출력한다.  python3 tools/show-ipam.py [--lab 1]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

lab = int(sys.argv[sys.argv.index("--lab") + 1]) if "--lab" in sys.argv else 1
I = L.IPAM
H = lambda t: print(f"\n\033[1m{t}\033[0m\n" + "-" * 72)

print(f"config/site.yml 기준 · lab_id={lab}")
H("구역 배분 (lab_block = " + I["ipv4"]["lab_block"] + ")")
for k, v in I["ipv4"]["summary"].items():
    print(f"  {k:10s} {v['cidr']:18s} {v['range']:32s} {v['desc']}")

H("세그먼트")
for k, s in I["ipv4"]["segments"].items():
    v6 = I["ipv6"]["segments"][k]
    print(f"  {k:8s} VLAN{s['vlan']:<4d} {s['cidr']:16s} GW {s['gateway']:15s} {s['desc']}")
    print(f"  {'':8s} {'':8s} {v6['prefix']:16s} GW {v6['gateway']}")
    for h, a in s["hosts"].items():
        print(f"  {'':8s}   - {h:5s} {a:15s} {v6['hosts'].get(h,'')}")

H("P2P 링크")
for k, l in I["ipv4"]["p2p"].items():
    v6 = I["ipv6"]["p2p"][k]
    cost = f"cost {l['ospf_cost']}" if "ospf_cost" in l else ""
    print(f"  {k:9s} {l['cidr']:17s} {l['addresses']}  {cost}")
    print(f"  {'':9s} {v6['prefix']:17s} {v6['addresses']}")

H("루프백 (OSPF router-id)")
for k, a in I["ipv4"]["loopback"].items():
    print(f"  {k:6s} {a:15s} {I['ipv6']['loopback'][k]}")

H("공인 구역")
p = I["public"]["ipv4"]
print(f"  transit  {p['transit']['cidr']:17s} {p['transit']['addresses']}")
print(f"  service  {p['service_block']['cidr']:17s} routed via {p['service_block']['routed_via']}")
print(f"  SNAT     {p['nat_pool']['snat_address']}")
for d in p["nat_pool"]["dnat"]:
    print(f"  DNAT     {d['public']:15s} -> {d['private']:15s} {d['ports']}  {d['desc']}")
print(f"  외부사이트 {p['external_site']['inet_loopback']:15s} {I['ipv6']['public']['external_site']}")

H(f"관리망 (lab {lab})")
print(f"  {L.mgmt_cidr(lab)}   GW {L.mgmt_gateway(lab)} (Proxmox)   점프 {L.jump_ip(lab)}")
for n, o in I["management"]["hosts"].items():
    print(f"    {n:6s} {L.mgmt_ip(lab, n):15s} MAC 52:54:00:{o:02x}:00:*")

H("DNS")
for r in I["dns"]["records"]:
    print(f"  {r['name']:6s} {r['type']:6s} {r['value']}")
