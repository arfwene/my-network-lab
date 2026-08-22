#!/usr/bin/env python3
"""
design/topology.yml + design/ipam.yml  ->  design/lab-map.md

랩 지도(교육생 상시 참조본)를 설계 파일에서 생성한다.
손으로 쓰지 않는 이유: 설계와 문서가 어긋나는 순간 교육생이 제일 먼저 혼란에 빠진다.

usage:  python3 tools/render-labmap.py [--stage m4]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

# 주소 계산은 labdesign 이 단독으로 담당한다. 여기서는 표현만 한다.
ROOT, TOPO, IPAM, STAGES = L.ROOT, L.TOPO, L.IPAM, L.STAGES
stage_le = L.stage_le


def mgmt_ip(node, lab_id=1):
    try:
        return L.mgmt_ip(lab_id, node)
    except KeyError:
        return "-"


def mgmt_cidr(lab_id=1):
    return L.mgmt_cidr(lab_id)


LAB_ID = 1

ZONE_LABEL = {
    "site-a": "Site-A · 지사", "core": "Core · 백본",
    "site-b": "Site-B · 데이터센터", "edge": "Edge · 인터넷 경계", "mgmt": "관리망",
}
SHAPE = {  # role -> (open, close)
    "host": ("([", "])"), "switch": ("[", "]"), "router": ("((", "))"),
    "server": ("[(", ")]"), "edge": ("{{", "}}"),
}


def stage_le(a, b):
    return STAGES.index(a) <= STAGES.index(b)


def nodes_at(stage):
    return [n for n in TOPO["nodes"] if stage_le(n["stage"], stage)]


def links_at(stage):
    """브리지 -> 그 브리지에 붙은 노드쌍. 양쪽 노드가 모두 존재하는 링크만."""
    present = {n["name"] for n in nodes_at(stage)}
    out = []
    for br in TOPO["bridges"]:
        if br["zone"] == "mgmt" or not stage_le(br["stage"], stage):
            continue
        ends = []
        for n in TOPO["nodes"]:
            if n["name"] not in present:
                continue
            for i in n.get("interfaces", []):
                if i.get("bridge") == br["id"] and stage_le(i.get("stage", n["stage"]), stage):
                    ends.append((n["name"], i["name"]))
        if len(ends) == 2:
            out.append((br, ends[0], ends[1]))
    return out


def mermaid(stage):
    ns = nodes_at(stage)
    by_zone = {}
    for n in ns:
        by_zone.setdefault(n["zone"], []).append(n)
    lines = ["graph LR"]
    for zone in ["site-a", "core", "site-b", "edge"]:
        if zone not in by_zone:
            continue
        lines.append(f'  subgraph z_{zone.replace("-","_")}["{ZONE_LABEL[zone]}"]')
        for n in by_zone[zone]:
            o, c = SHAPE[n["role"]]
            lines.append(f'    {n["name"]}{o}"{n["name"]}"{c}')
        lines.append("  end")
    for br, (a, ai), (b, bi) in links_at(stage):
        lines.append(f'  {a} ---|"{br["id"]}"| {b}')
    return "\n".join(lines)


def node_rows(stage):
    seg = IPAM["ipv4"]["segments"]
    lo = IPAM["ipv4"]["loopback"]
    rows = []
    for n in nodes_at(stage):
        addrs = []
        for s in seg.values():
            if n["name"] in s.get("hosts", {}):
                addrs.append(f'{s["hosts"][n["name"]]}/{s["cidr"].split("/")[1]} (VLAN{s["vlan"]})')
            if s.get("gateway_node") == n["name"]:
                addrs.append(f'{s["gateway"]} *(GW VLAN{s["vlan"]})*')
        for lname, l in IPAM["ipv4"]["p2p"].items():
            if n["name"] in l["addresses"]:
                addrs.append(f'{l["addresses"][n["name"]]}/30 → {lname}')
        if n["name"] in lo:
            addrs.append(f'{lo[n["name"]]}/32 *(lo0)*')
        if n["name"] == "edge":
            addrs.append(f'{IPAM["public"]["ipv4"]["transit"]["addresses"]["edge"]}/30 *(공인)*')
        if n["name"] == "inet":
            addrs.append(f'{IPAM["public"]["ipv4"]["transit"]["addresses"]["inet"]}/30 *(공인)*')
            addrs.append(f'{IPAM["public"]["ipv4"]["external_site"]["inet_loopback"]} *(외부 사이트)*')
        rows.append((n, mgmt_ip(n["name"], LAB_ID),
                     "<br>".join(addrs) or ("*L2 전용 — 랩 IP 없음*" if n["role"] == "switch" else "-")))
    return rows


def render(stage="m10"):
    d = []
    A = d.append
    A("<!-- 자동 생성 파일. 직접 수정하지 말 것.")
    A("     생성: python3 tools/render-labmap.py")
    A("     원본: design/topology.yml, design/ipam.yml -->")
    A("")
    A("# 랩 지도 (Lab Map)")
    A("")
    A("> 실습 중 항상 열어두는 참조본. **모든 주소의 원본은 `design/ipam.yml`**")
    A("")
    A("---")
    A("")
    A("## 1. 전체 토폴로지")
    A("")
    A("```mermaid")
    A(mermaid(stage))
    A("```")
    A("")
    A("범례 — `([호스트])` `[스위치]` `((라우터))` `[(서버)]` `{{경계장비}}`")
    A("")
    A("## 2. 모듈별 성장 단계")
    A("")
    A("| 모듈 | 추가되는 노드 | 배우는 것 |")
    A("|---|---|---|")
    for s in STAGES:
        st = TOPO["stages"].get(s)
        if not st:
            continue
        add = ", ".join(f"`{x}`" for x in st["nodes"]) or "— (노드 추가 없음)"
        A(f'| **{s.upper()}** | {add} | {st["desc"]} |')
    A("")
    A("## 3. 노드 일람")
    A("")
    A("| 노드 | 역할 | 구역 | 관리 IP (mgmt0) | 랩 IP (eth1~) |")
    A("|---|---|---|---|---|")
    for n, mgmt, addrs in node_rows(stage):
        A(f'| **{n["name"]}** | {n["role"]} | {n["zone"]} | `{mgmt}` | {addrs} |')
    A("")
    A("> **관리 IP 로 통신 테스트를 하지 말 것.** 관리망은 랩과 분리돼 있어 항상 통한다.")
    A("> 모든 검증은 랩 IP(eth1 이후) 기준으로 한다.")
    A("")
    A("## 4. 링크 · 브리지 일람")
    A("")
    A(f'물리 브리지 이름 = `vmbr{{lab_id}}<ID>` — 이 문서는 **lab_id={LAB_ID}** 기준: `101` → `vmbr{LAB_ID}101`')
    A("")
    A("| ID | 별칭 | 구역 | 등장 | 연결 | 대역 |")
    A("|---|---|---|---|---|---|")
    p2p = IPAM["ipv4"]["p2p"]
    seg = IPAM["ipv4"]["segments"]
    for br in TOPO["bridges"]:
        cidr = "-"
        if br["alias"] in p2p:
            cidr = f'`{p2p[br["alias"]]["cidr"]}`'
        elif br["id"] == IPAM["public"]["ipv4"]["transit"]["bridge"]:
            cidr = f'`{IPAM["public"]["ipv4"]["transit"]["cidr"]}` **(공인)**'
        elif any(i.get("mode") == "trunk" for n in TOPO["nodes"]
                 for i in n.get("interfaces", []) if i.get("bridge") == br["id"]):
            tagged = sorted({s["vlan"] for s in seg.values() if s["zone"] == br["zone"]})
            cidr = "트렁크 — VLAN " + ", ".join(str(v) for v in tagged)
        else:
            for s in seg.values():
                for n in TOPO["nodes"]:
                    if any(i.get("bridge") == br["id"] and i.get("vlan") == s["vlan"]
                           for i in n.get("interfaces", []) if isinstance(i.get("vlan"), int)):
                        cidr = f'`{s["cidr"]}` (VLAN{s["vlan"]})'
                        break
        A(f'| `{br["id"]}` | {br["alias"]} | {br["zone"]} | {br["stage"].upper()} | {br["desc"]} | {cidr} |')
    A("")
    A("## 5. 주소 설계 요약")
    A("")
    A(f'**랩 할당 블록: `{IPAM["ipv4"]["lab_block"]}`** (사설, RFC1918)')
    A("")
    A("| 구역 | 요약 대역 | 범위 | 용도 |")
    A("|---|---|---|---|")
    for k, v in IPAM["ipv4"]["summary"].items():
        A(f'| {k} | `{v["cidr"]}` | {v["range"]} | {v["desc"]} |')
    A("")
    A("> 구역별로 대역을 모아둔 이유는 **요약(summarization)** 이다.")
    A(f'> r1 은 VLAN10·VLAN20 을 따로 광고하지 않고 '
      f'`{IPAM["ipv4"]["summary"]["site-a"]["cidr"]}` 하나로 광고한다.')
    A("")
    A("### VLAN")
    A("")
    A("| ID | 이름 | 대역 | 게이트웨이 | 용도 |")
    A("|---|---|---|---|---|")
    for v in TOPO["vlans"]:
        s = next((x for x in seg.values() if x["vlan"] == v["id"]), None)
        A(f'| {v["id"]} | {v["name"]} | {"`"+s["cidr"]+"`" if s else "-"} | '
          f'{"`"+s["gateway"]+"`" if s else "-"} | {v["desc"]} |')
    A("")
    A("### 사설 IP vs 공인 IP")
    A("")
    A("| 구분 | 대역 | 위치 | 인터넷 라우팅 |")
    A("|---|---|---|---|")
    A(f'| **사설** | `{IPAM["ipv4"]["lab_block"]}` | edge 안쪽 전체 | ✕ (NAT 필요) |')
    A(f'| **공인** | `{IPAM["public"]["ipv4"]["transit"]["cidr"]}` | edge ↔ inet | ○ |')
    A(f'| **외부 사이트** | `{IPAM["public"]["ipv4"]["external_site"]["inet_loopback"]}` | inet | ○ |')
    A("")
    A("> 기본 설정의 공인 대역은 RFC5737 **문서·교육 전용 예약 대역**을 쓴다.")
    A("> 실제 인터넷에서 라우팅되지 않으므로 실습에 안전하다. (`config/site.yml` 에서 변경 가능)")
    A("")
    A("### IPv6")
    A("")
    A(f'| 구분 | Prefix | IPv4 대응 |')
    A("|---|---|---|")
    A(f'| ULA (사설) | `{IPAM["ipv6"]["ula_prefix"]}` | RFC1918 사설 IP |')
    A(f'| GUA (공인) | `{IPAM["ipv6"]["public"]["transit"]["prefix"]}` (문서 대역 `2001:db8::/32`) | 공인 IP |')
    A("")
    A("| 세그먼트 | Prefix | 게이트웨이 |")
    A("|---|---|---|")
    for k, v in IPAM["ipv6"]["segments"].items():
        A(f'| {k} | `{v["prefix"]}` | `{v["gateway"]}` |')
    A("")
    A("## 6. 서비스 · 포트")
    A("")
    A("| 서비스 | 노드 | 주소 | 포트 |")
    A("|---|---|---|---|")
    A(f'| HTTP/HTTPS | web | `{seg["vlan40"]["hosts"]["web"]}` | 80, 443 |')
    A(f'| FTP | ftp | `{seg["vlan40"]["hosts"]["ftp"]}` | 21 + passive `{IPAM["services"]["ftp"]["passive_range"]}` |')
    A(f'| DNS | dns | `{seg["vlan40"]["hosts"]["dns"]}` | 53 (UDP/TCP) |')
    A("")
    A(f'DNS 존: **`{IPAM["dns"]["zone"]}`** — `web.lab.local`, `ftp.lab.local`, `www.lab.local`(CNAME)')
    A("")
    A("## 7. MAC 주소 규칙")
    A("")
    A("```")
    A("52:54:00:<노드ID>:00:<포트번호>")
    A("           └─ 관리망 IP 마지막 옥텟과 동일")
    A("```")
    A("")
    A("| 노드 | ID | 관리 IP | MAC 접두 |")
    A("|---|---|---|---|")
    for n in nodes_at(stage):
        nid = f'{n["id"]:02x}'
        A(f'| {n["name"]} | `0x{nid}` | `{mgmt_ip(n["name"], LAB_ID)}` | `52:54:00:{nid}:00:*` |')
    A("")
    A("> M1 에서 `bridge fdb show` 결과를 볼 때, MAC 만 보고 어느 노드인지 바로 알 수 있다.")
    A("")
    return "\n".join(d) + "\n"


if __name__ == "__main__":
    stage = "m10"
    if "--stage" in sys.argv:
        stage = sys.argv[sys.argv.index("--stage") + 1]
    if "--lab" in sys.argv:
        globals()["LAB_ID"] = int(sys.argv[sys.argv.index("--lab") + 1])
    default = stage == "m10" and LAB_ID == 1
    (ROOT / "dist").mkdir(exist_ok=True)
    out = ROOT / ("dist/lab-map.md" if default else f"dist/lab-map-lab{LAB_ID}-{stage}.md")
    out.write_text(render(stage), encoding="utf-8")
    print(f"generated {out} (stage={stage})")
