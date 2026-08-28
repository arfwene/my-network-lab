"""
설계 -> 실제 주소 계산 엔진.

  config/site.yml  (+ site.local.yml)   "우리 환경"의 블록들
  design/ipam.yml                       그 블록을 어떻게 자를 것인가 (절대 주소 없음)
  design/topology.yml                   노드 · 링크
  design/routing.yml                    라우팅 정책
        │
        └─> 여기서 단 한 번 계산한다. Terraform · Ansible · 문서 생성기는 결과만 소비한다.

주소 조합 로직이 여러 곳에 흩어지면 반드시 어긋난다 (PLAN 6.5 R1/R5).
"""
import ipaddress
import copy
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGES = [f"m{i}" for i in range(1, 11)]


# ----------------------------------------------------------------- 설정 로딩
def _load(rel):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _deep_merge(base, over):
    """site.local.yml 을 site.yml 위에 병합. 리스트는 통째로 교체하되 forbidden 만 이어붙인다."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k in ("forbidden", "ssh_public_keys", "forbidden_strings") and isinstance(v, list):
            out[k] = (out.get(k) or []) + v
        else:
            out[k] = v
    return out


#  세 겹으로 쌓는다. 뒤에 오는 것이 앞을 덮어쓴다.
#    1. config/site.yml        공개 저장소의 안전한 기본값 (Proxmox = localhost)
#    2. config/site.local.yml  이 설치본의 실제 값 (git 제외)
#    3. var/runtime.yml        웹 콘솔 관리자 화면에서 입력한 접속 정보 (git 제외)
#
#  3번이 있는 이유: 이 랩은 Proxmox 위에서 돌 수도, 다른 서버에서 원격 Proxmox 에
#  붙을 수도 있다. 접속 정보를 파일 편집으로만 바꿀 수 있으면 콘솔만 쓰는 관리자는
#  손댈 방법이 없다. 콘솔이 DB 에 저장한 값을 여기로 내보내
#  Terraform · Ansible · 문서 생성기가 전부 같은 값을 보게 한다.
RUNTIME = ROOT / "var/runtime.yml"


def _load_site():
    site = _load("config/site.yml")
    for extra in (ROOT / "config/site.local.yml", RUNTIME):
        if extra.exists():
            site = _deep_merge(site, yaml.safe_load(extra.read_text(encoding="utf-8")) or {})
    return site


SITE = _load_site()

RULES = _load("design/ipam.yml")
TOPO = _load("design/topology.yml")
ROUTING = _load("design/routing.yml")


# ------------------------------------------------------------- 주소 계산 도구
def _sub4(parent, prefix, index):
    """parent 를 prefix 로 잘랐을 때 index 번째 조각."""
    p = ipaddress.ip_network(parent)
    span = prefix - p.prefixlen
    if span < 0 or index >= (1 << span):
        raise ValueError(f"{parent} 를 /{prefix} 로 자를 때 index {index} 는 범위를 벗어난다")
    return ipaddress.ip_network((int(p.network_address) + (index << (32 - prefix)), prefix))


def _sub6(parent, subnet_id_hex, new_prefix=64):
    p = ipaddress.ip_network(parent)
    idx = int(str(subnet_id_hex), 16)
    span = new_prefix - p.prefixlen
    if span < 0 or idx >= (1 << span):
        raise ValueError(f"{parent} 에서 subnet id {subnet_id_hex} 는 범위를 벗어난다")
    return ipaddress.ip_network((int(p.network_address) + (idx << (128 - new_prefix)), new_prefix))


def _a4(net, offset):
    return str(net.network_address + offset)


def _a6(net, suffix):
    return f"{net.network_address}{suffix}"


# ------------------------------------------------------------------- 해석기
def _resolve():
    N = SITE["networks"]
    lab = ipaddress.ip_network(N["lab_block"])

    # --- 구역 ---
    summary = {}
    zone_net = {}
    for key, z in RULES["zones"].items():
        n = _sub4(lab, z["prefix"], z["index"])
        zone_net[key] = n
        summary[key] = {"cidr": str(n),
                        "range": f"{n.network_address} ~ {n.broadcast_address}",
                        "desc": z["desc"]}

    ula = N["ipv6"]["ula"]

    # --- 세그먼트 ---
    segments, v6segments = {}, {}
    for name, s in RULES["segments"].items():
        n = _sub4(zone_net[s["zone"]], s["prefix"], s["index"])
        seg = {"cidr": str(n), "vlan": s["vlan"], "zone": s["zone"],
               "gateway": _a4(n, s["gateway_offset"]), "gateway_node": s["gateway_node"],
               "desc": s["desc"],
               "hosts": {h: _a4(n, o) for h, o in s.get("hosts", {}).items()}}
        if "dhcp_pool" in s:
            seg["dhcp_pool"] = f'{_a4(n, s["dhcp_pool"]["start"])} - {_a4(n, s["dhcp_pool"]["end"])}'
        segments[name] = seg
        v6 = _sub6(ula, s["ipv6_subnet_id"])
        v6segments[name] = {"prefix": str(v6),
                            "gateway": _a6(v6, s["ipv6_gateway_suffix"]),
                            "hosts": {h: _a6(v6, sf) for h, sf in s.get("ipv6_host_suffix", {}).items()}}

    # --- P2P ---
    p2p, v6p2p = {}, {}
    for name, l in RULES["p2p"].items():
        n = _sub4(zone_net[l["zone"]], l["prefix"], l["index"])
        e = {"cidr": str(n), "bridge": l["bridge"], "desc": l.get("desc", ""),
             "addresses": {k: _a4(n, o) for k, o in l["offsets"].items()}}
        if "ospf_cost" in l:
            e["ospf_cost"] = l["ospf_cost"]
        p2p[name] = e
        v6 = _sub6(ula, l["ipv6_subnet_id"])
        v6p2p[name] = {"prefix": str(v6),
                       "addresses": {k: _a6(v6, sf) for k, sf in l["ipv6_suffix"].items()}}

    # --- 루프백 ---
    lo_net = zone_net["loopback"]
    lo6 = _sub6(ula, RULES["loopback"]["ipv6_subnet_id"])
    loopback = {k: _a4(lo_net, v["offset"]) for k, v in RULES["loopback"]["nodes"].items()}
    v6loopback = {k: f'{_a6(lo6, v["ipv6_suffix"])}/128' for k, v in RULES["loopback"]["nodes"].items()}

    # --- 공인 구역 ---
    P = RULES["public"]
    transit = ipaddress.ip_network(N["public_transit"])
    svc = ipaddress.ip_network(N["public_service"])
    ext = ipaddress.ip_network(N["external_net"])
    t6 = ipaddress.ip_network(N["ipv6"]["gua_transit"])
    e6 = ipaddress.ip_network(N["ipv6"]["gua_external"])
    ftp_passive = RULES["services"]["ftp"]["passive_range"]

    def _ports(lst):
        return [ftp_passive if p == "passive" else p for p in lst]

    dnat = [{"public": _a4(svc, d["offset"]),
             "private": segments["vlan40"]["hosts"][d["node"]],
             "node": d["node"], "ports": _ports(d["ports"]), "desc": d["desc"]}
            for d in P["dnat"]]

    public = {"ipv4": {
        "transit": {"cidr": str(transit), "bridge": P["transit_bridge"],
                    "addresses": {k: _a4(transit, o) for k, o in P["transit_offsets"].items()},
                    "desc": "ISP 회선 흉내"},
        "service_block": {"cidr": str(svc),
                          "routed_via": _a4(transit, P["transit_offsets"][P["snat_from"]]),
                          "note": P["service_block"]["note"]},
        "nat_pool": {"snat_address": _a4(transit, P["transit_offsets"][P["snat_from"]]), "dnat": dnat},
        "external_site": {"inet_loopback": _a4(ext, P["external_site"]["offset"]),
                          "desc": P["external_site"]["desc"]},
    }}

    ipv6 = {"ula_prefix": ula, "segments": v6segments, "p2p": v6p2p, "loopback": v6loopback,
            "public": {"transit": {"prefix": str(t6),
                                   "addresses": {k: _a6(t6, s) for k, s in P["ipv6"]["transit_suffix"].items()}},
                       "web_public": _a6(t6, P["ipv6"]["web_public_suffix"]),
                       "external_site": _a6(e6, P["ipv6"]["external_suffix"]),
                       "note": P["ipv6"]["note"]}}

    # --- DNS ---
    def _lookup(node, source=None, family="A"):
        if source == "loopback":
            return loopback[node] if family == "A" else v6loopback[node].split("/")[0]
        if source == "external_site":
            return (public["ipv4"]["external_site"]["inet_loopback"] if family == "A"
                    else ipv6["public"]["external_site"])
        for name, s in segments.items():
            if node in s["hosts"]:
                return s["hosts"][node] if family == "A" else v6segments[name]["hosts"].get(node)
        return None

    def _records(items):
        out = []
        for r in items:
            if "cname" in r:
                out.append({"name": r["name"], "type": "CNAME", "value": r["cname"]})
                continue
            for t in r["types"]:
                v = _lookup(r["node"] if "node" in r else None, r.get("source"), t.replace("AAAA", "AAAA"))
                v = _lookup(r.get("node"), r.get("source"), "A" if t == "A" else "AAAA")
                if v:
                    out.append({"name": r["name"], "type": t, "value": v})
        return out

    D = RULES["dns"]
    dns = {"zone": D["zone"],
           "server": segments["vlan40"]["hosts"][D["server_node"]],
           "server_v6": v6segments["vlan40"]["hosts"][D["server_node"]],
           "records": _records(D["records"]),
           "external_zone": {"zone": D["external_zone"]["zone"],
                             "note": D["external_zone"]["note"],
                             "records": _records(D["external_zone"]["records"])}}

    # --- 단계별 오버라이드 ---
    overrides = {}
    for st, ov in RULES.get("stage_overrides", {}).items():
        o = dict(ov)
        hi = {}
        for node, spec in ov.get("host_segment_override", {}).items():
            seg = segments[spec["segment"]]
            n = ipaddress.ip_network(seg["cidr"])
            hi[node] = f'{_a4(n, spec["offset"])}/{n.prefixlen}'
        o["host_ipv4"] = hi
        overrides[st] = o

    return {"ipv4": {"lab_block": str(lab), "summary": summary, "segments": segments,
                     "p2p": p2p, "loopback": loopback},
            "ipv6": ipv6, "public": public, "dns": dns,
            "services": RULES["services"],
            "management": {**RULES["management"]},
            "labs": SITE["labs"], "access": SITE["access"],
            "stage_overrides": overrides,
            "rationale": RULES["rationale"]}


IPAM = _resolve()


def reload():
    """var/runtime.yml 이 바뀐 뒤 재기동 없이 다시 읽는다.

    웹 콘솔은 오래 떠 있는 프로세스다. 관리자가 Proxmox 주소를 바꿨는데
    다음 배포가 예전 주소로 나가면 원인을 찾기 어렵다.
    """
    global SITE, IPAM
    SITE = _load_site()
    IPAM = _resolve()
    return IPAM


# ---------------------------------------------------------------- 이름 · 관리망
def stage_le(a, b):
    return STAGES.index(a) <= STAGES.index(b)


def bridge_name(lab_id, bridge_id):
    return IPAM["labs"]["naming"]["bridge"].format(lab_id=lab_id, bridge_id=bridge_id)


def vm_name(lab_id, node):
    return IPAM["labs"]["naming"]["vm_name"].format(lab_id=lab_id, node=node)


def vmid(lab_id, node):
    idx = [n["name"] for n in TOPO["nodes"]].index(node) + 1
    return vmid_range(lab_id)[0] + idx - 1


def vmid_range(lab_id):
    """이 랩이 점유하는 VMID 구간 (양 끝 포함). 배포 전 충돌 검사에 쓴다."""
    L_ = IPAM["labs"]
    base = int(L_.get("vmid_start", 0)) + lab_id * int(L_["vmid_base"])
    return base + 1, base + len(TOPO["nodes"])


def mgmt_net(lab_id):
    """랩 N 의 관리망 = networks.management 를 /24 로 자른 N 번째 조각."""
    return _sub4(SITE["networks"]["management"], IPAM["management"]["subnet_prefix"], lab_id)


def mgmt_cidr(lab_id):
    return str(mgmt_net(lab_id))


def mgmt_prefixlen(lab_id):
    return mgmt_net(lab_id).prefixlen


def mgmt_gateway(lab_id):
    return _a4(mgmt_net(lab_id), IPAM["management"]["gateway_octet"])


def mgmt_ip(lab_id, node):
    return _a4(mgmt_net(lab_id), IPAM["management"]["hosts"][node])


def jump_ip(lab_id):
    return _a4(mgmt_net(lab_id), IPAM["access"]["jump_host"]["host_octet"])


# ------------------------------------------------------- 인터페이스 주소 해석
def _seg_by_vlan(vlan):
    for name, s in IPAM["ipv4"]["segments"].items():
        if s["vlan"] == vlan:
            return name, s
    return None, None


def _v6_seg(seg_name):
    return IPAM["ipv6"]["segments"].get(seg_name, {})


def _plen(cidr):
    return int(cidr.split("/")[1])


def resolve_interface(node, itf):
    """인터페이스 하나의 IPv4/IPv6 주소를 결정한다. 없으면 None."""
    name = node["name"]
    out = {"ipv4": None, "ipv6": None, "role": None}

    # 1) 루프백
    if itf.get("type") == "loopback":
        if name in IPAM["ipv4"]["loopback"]:
            out["ipv4"] = f'{IPAM["ipv4"]["loopback"][name]}/32'
            out["ipv6"] = IPAM["ipv6"]["loopback"].get(name)
            out["role"] = "loopback"
            if name == RULES["public"]["snat_from"]:
                # ISP 가 라우팅해 준 공인 서비스 블록의 주소들 (DNAT 대상)
                out["extra_ipv4"] = [f'{d["public"]}/32'
                                     for d in IPAM["public"]["ipv4"]["nat_pool"]["dnat"]]
                out["extra_ipv6"] = [f'{IPAM["ipv6"]["public"]["web_public"]}/128']
                out["extra_stage"] = "m9"
        elif name == "inet":
            out["ipv4"] = f'{IPAM["public"]["ipv4"]["external_site"]["inet_loopback"]}/32'
            out["ipv6"] = f'{IPAM["ipv6"]["public"]["external_site"]}/128'
            out["role"] = "external-site"
        return out

    # 2) VLAN 서브인터페이스 (router-on-a-stick)
    if itf.get("parent"):
        seg_name, seg = _seg_by_vlan(itf["vlan"])
        v6 = _v6_seg(seg_name)
        if seg.get("gateway_node") == name:
            out["ipv4"] = f'{seg["gateway"]}/{_plen(seg["cidr"])}'
            out["ipv6"] = f'{v6["gateway"]}/64' if v6 else None
            out["role"] = "gateway"
        return out

    br = itf.get("bridge")

    # 3) P2P 링크
    for lname, link in IPAM["ipv4"]["p2p"].items():
        if link["bridge"] == br and name in link["addresses"]:
            out["ipv4"] = f'{link["addresses"][name]}/{_plen(link["cidr"])}'
            v6 = IPAM["ipv6"]["p2p"].get(lname, {})
            if name in v6.get("addresses", {}):
                out["ipv6"] = f'{v6["addresses"][name]}/64'
            out["role"] = "p2p"
            out["link"] = lname
            out["ospf_cost"] = link.get("ospf_cost")
            return out

    # 4) 공인 구간
    pub = IPAM["public"]["ipv4"]["transit"]
    if pub["bridge"] == br and name in pub["addresses"]:
        out["ipv4"] = f'{pub["addresses"][name]}/{_plen(pub["cidr"])}'
        v6 = IPAM["ipv6"]["public"]["transit"]
        if name in v6["addresses"]:
            out["ipv6"] = f'{v6["addresses"][name]}/64'
        out["role"] = "public"
        return out

    # 5) 액세스 세그먼트 (호스트 / 서버 / 게이트웨이)
    if isinstance(itf.get("vlan"), int):
        seg_name, seg = _seg_by_vlan(itf["vlan"])
        if seg:
            v6 = _v6_seg(seg_name)
            if name in seg.get("hosts", {}):
                out["ipv4"] = f'{seg["hosts"][name]}/{_plen(seg["cidr"])}'
                out["ipv6"] = f'{v6["hosts"][name]}/64' if name in v6.get("hosts", {}) else None
                out["role"] = "access"
                out["gateway"] = seg["gateway"]
                out["gateway6"] = v6.get("gateway")
            elif seg.get("gateway_node") == name:
                out["ipv4"] = f'{seg["gateway"]}/{_plen(seg["cidr"])}'
                out["ipv6"] = f'{v6["gateway"]}/64' if v6 else None
                out["role"] = "gateway"
    return out


def _apply_stage_override(node_name, r, stage):
    ov = IPAM.get("stage_overrides", {}).get(stage)
    if not ov or r.get("role") != "access":
        return r
    if node_name in ov.get("host_ipv4", {}):
        r = dict(r)
        r["ipv4"] = ov["host_ipv4"][node_name]
        r["ipv4_overridden"] = True
        r.pop("ipv6", None)
    if ov.get("default_route") is False:
        # 라우터가 없는 단계 — 게이트웨이를 주면 "왜 안 되는지"를 배울 기회가 사라진다
        r = dict(r)
        r.pop("gateway", None)
        r.pop("gateway6", None)
    return r


def node_config(lab_id, node, stage="m10", config_stage=None):
    """노드 하나의 완성된 설정 (Ansible host_vars 로 그대로 사용).

    stage 는 **무엇이 존재하는가**(배선 · 포트), config_stage 는 **어디까지 설정돼
    있는가**다. 둘을 가르는 자리가 여기다.

      배선과 물리 포트의 주소   stage        랩이 깔아 준다
      VLAN 서브인터페이스       config_stage 교육생이 만든다 (M2 의 router-on-a-stick)
      단계 예외(stage_override) config_stage M1 은 VLAN 없이 한 대역, 게이트웨이 없음

    M2 의 초기 구성이 M1 의 모습이어야 교육생이 "끊긴 통신"에서 시작할 수 있다.
    """
    cs = config_stage or stage
    ifaces = []
    for itf in node.get("interfaces", []):
        ist = itf.get("stage", node["stage"])
        r = _apply_stage_override(node["name"], resolve_interface(node, itf), cs)
        if r.get("ipv4"):
            r["ipv4_network"] = str(ipaddress.ip_interface(r["ipv4"]).network)
        if r.get("ipv6"):
            r["ipv6_network"] = str(ipaddress.ip_interface(r["ipv6"]).network)
        ifaces.append({
            "name": itf["name"], "mac": itf.get("mac"), "bridge_id": itf.get("bridge"),
            "bridge": bridge_name(lab_id, itf["bridge"]) if itf.get("bridge") else None,
            "peer": itf.get("peer"), "vlan": itf.get("vlan"), "mode": itf.get("mode"),
            "native": itf.get("native"), "parent": itf.get("parent"), "type": itf.get("type"),
            "stage": ist,
            #  서브인터페이스는 설정이다. 물리 포트는 배선이다.
            "active": stage_le(ist, cs if itf.get("parent") else stage),
            "desc": itf.get("desc"),
            **{k: v for k, v in r.items() if v is not None},
        })

    # 라우터가 아닌 노드(inet 등)의 정적 경로는 netplan 으로 처리한다
    if node["role"] in ("host", "server"):
        for rt in edge_routes(node["name"]).get("static", []):
            via = rt.get("via")
            if not via:
                continue
            for i in ifaces:
                net = i.get("ipv4_network")
                if net and ipaddress.ip_address(via) in ipaddress.ip_network(net):
                    i.setdefault("routes", []).append(
                        {"to": rt["prefix"], "via": via, "stage": "m9", "desc": rt.get("desc")})

    return {
        "node": node["name"], "lab_id": lab_id,
        "vm_name": vm_name(lab_id, node["name"]), "vmid": vmid(lab_id, node["name"]),
        "node_role": node["role"], "zone": node["zone"], "stage": node["stage"],
        # 노드는 항상 존재한다(배선 완료). node_active 는 "이 단계에서 설정을 올리는가".
        "node_active": stage_le(node["stage"], stage),
        "desc": node.get("desc", ""), "services": node.get("services", []),
        "bridge_name_local": node.get("bridge_name"),
        "cores": node.get("cores", TOPO["defaults"]["cores"]),
        "memory": node.get("memory", TOPO["defaults"]["memory"]),
        "disk": node.get("disk", TOPO["defaults"]["disk"]),
        "ansible_host": mgmt_ip(lab_id, node["name"]),
        "mgmt_ip": mgmt_ip(lab_id, node["name"]),
        "mgmt_cidr": mgmt_cidr(lab_id), "mgmt_prefixlen": mgmt_prefixlen(lab_id),
        "mgmt_gateway": mgmt_gateway(lab_id), "mgmt_interface": IPAM["management"]["interface"],
        "mgmt_mac": f'52:54:00:{node["id"]:02x}:00:00',
        "lab_interfaces": ifaces,
        "loopback_ipv4": IPAM["ipv4"]["loopback"].get(node["name"]),
        "loopback_ipv6": IPAM["ipv6"]["loopback"].get(node["name"]),
        "stage_override": IPAM.get("stage_overrides", {}).get(cs, {}),
    }


def all_nodes(lab_id, stage="m10"):
    return [node_config(lab_id, n, stage) for n in TOPO["nodes"] if stage_le(n["stage"], stage)]


# ----------------------------------------------------------------- 관리망(OOB)
#  관리망은 랩 자원이 아니다. **전 랩 공용 VLAN-aware 브리지 하나**를 랩별 VLAN 으로 나눈다.
#
#  왜 브리지를 랩마다 두지 않는가
#    운영 서버(Terraform·Ansible·콘솔)는 각 랩의 관리망에 발을 걸쳐야 한다.
#    브리지가 랩마다면 랩을 만들 때마다 운영 서버에 NIC 을 붙여야 하고, 랩을 지우면
#    `terraform destroy` 가 그 NIC 이 꽂힌 브리지를 지우려 든다.
#    VLAN 이면 운영 서버는 트렁크 NIC 하나로 모든 랩에 닿는다 — 랩이 늘어도 줄어도
#    Proxmox 와 VM 하드웨어를 건드리지 않는다. (근거: PLAN 7.9.1)
#
#  격리는 그대로다. 태깅은 브리지가 하고 게스트는 untagged 프레임만 본다 →
#  랩 노드가 다른 VLAN 을 주입할 수 없다.
def mgmt_bridge_name(lab_id=None):        # noqa: ARG001  (랩과 무관 — 호출부 호환을 위해 받는다)
    """전 랩 공용 관리망 브리지 이름."""
    return IPAM["labs"]["naming"]["mgmt_bridge"]


def mgmt_vlan(lab_id):
    """랩 N 의 관리 VLAN ID."""
    return int(IPAM["management"]["vlan_base"]) + int(lab_id)


def mgmt_iface(lab_id):
    """운영 서버 안에서 이 랩 관리망을 받는 VLAN 서브인터페이스 이름.

    이름을 우리가 정한다 — ens19 같은 커널 이름 순서에 기대지 않는다.
    """
    return f"mgmt{lab_id}"


def all_bridges(lab_id, stage="m10"):
    """이 랩이 만드는 브리지 = 랩 링크뿐. 관리망은 여기 없다."""
    return [{"id": b["id"], "name": bridge_name(lab_id, b["id"]), "alias": b["alias"],
             "zone": b["zone"], "stage": b["stage"], "desc": b["desc"]}
            for b in TOPO["bridges"] if stage_le(b["stage"], stage)]


def ops_trunk_mac():
    """운영 서버 트렁크 NIC 의 MAC.

    노드는 52:54:00:<노드ID>:00:00 을 쓰고 노드 ID 는 11~51 이다.
    ff 를 써서 어떤 랩 노드와도 겹치지 않게 한다 — 겹치면 같은 브리지에서
    MAC 충돌이 나고, 그 증상은 "가끔 안 된다" 라 원인을 찾기 어렵다.
    """
    return "52:54:00:ff:00:09"


def mgmt_labs(labs=None):
    """랩 1..N 의 관리망 정보 (VLAN · 대역 · 운영 서버 주소)."""
    n = int(labs or IPAM["labs"]["default_count"])
    lo, hi = IPAM["labs"]["id_range"]
    n = max(1, min(n, hi - lo + 1))
    return [{"lab_id": i,
             "vlan": mgmt_vlan(i),
             "iface": mgmt_iface(i),
             "cidr": mgmt_cidr(i),
             "gateway": mgmt_gateway(i),
             "ops_ip": _a4(mgmt_net(i), IPAM["access"]["jump_host"]["host_octet"]),
             "prefixlen": IPAM["management"]["subnet_prefix"]}
            for i in range(lo, lo + n)]


# ----------------------------------------------------------------- 라우팅 해석
def resolve_route(r):
    """design/routing.yml 의 상징적 경로를 실제 prefix/next-hop 으로 바꾼다."""
    to, via = r["to"], r["via"]
    if to.get("default"):
        prefix = "0.0.0.0/0"
    elif "zone" in to:
        prefix = IPAM["ipv4"]["summary"][to["zone"]]["cidr"]
    elif "block" in to:
        prefix = SITE["networks"][to["block"]]
    else:
        raise ValueError(f"경로 목적지를 해석할 수 없다: {to}")

    if "link" in via:
        nh = IPAM["ipv4"]["p2p"][via["link"]]["addresses"][via["node"]]
    elif "transit" in via:
        nh = IPAM["public"]["ipv4"]["transit"]["addresses"][via["transit"]]
    else:
        raise ValueError(f"next-hop 을 해석할 수 없다: {via}")

    return {"prefix": prefix, "via": nh, "desc": r.get("desc", "")}


def static_routes(node, phase="static_m3"):
    return [resolve_route(r) for r in ROUTING.get(phase, {}).get(node, [])]


def edge_routes(node):
    e = ROUTING["edge_routing_m9"].get(node, {})
    return {**e, "static": [resolve_route(r) for r in e.get("static", [])]}


# ------------------------------------------------------------------ 다이어그램
ZONE_LABEL = {"site-a": "Site-A · 지사", "core": "Core · 백본",
              "site-b": "Site-B · 데이터센터", "edge": "Edge · 인터넷 경계", "mgmt": "관리망"}
SHAPE = {"host": ("([", "])"), "switch": ("[", "]"), "router": ("((", "))"),
         "server": ("[(", ")]"), "edge": ("{{", "}}")}


def links_at(lab_id, stage):
    """브리지 -> 양 끝 노드. 양쪽이 모두 존재하는 링크만."""
    present = {n["name"] for n in TOPO["nodes"] if stage_le(n["stage"], stage)}
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


def mermaid(lab_id=1, stage="m10", show_bridge=True):
    ns = [n for n in TOPO["nodes"] if stage_le(n["stage"], stage)]
    by_zone = {}
    for n in ns:
        by_zone.setdefault(n["zone"], []).append(n)
    # 어느 단계의 그림인지 그림 자신이 말한다.
    #  웹 콘솔은 ```mermaid 블록을 서버에서 SVG 로 바꾸는데, 어느 단계로 그릴지
    #  노드 수로 짐작하고 있었다. M0 은 "최종 모습"과 "지금 모습"을 나란히 보여
    #  주는데, 짐작이 빗나가 둘 다 지금 모습으로 그려졌다. mermaid 주석이라
    #  그대로 흘러가도 그림에는 영향이 없다.
    lines = [f"%% lab-stage: {stage}", "graph LR"]
    for zone in ["site-a", "core", "site-b", "edge"]:
        if zone not in by_zone:
            continue
        lines.append(f'  subgraph z_{zone.replace("-", "_")}["{ZONE_LABEL[zone]}"]')
        for n in by_zone[zone]:
            o, c = SHAPE[n["role"]]
            lines.append(f'    {n["name"]}{o}"{n["name"]}"{c}')
        lines.append("  end")
    for br, (a, ai), (b, bi) in links_at(lab_id, stage):
        label = br["id"] if show_bridge else ""
        lines.append(f'  {a} ---|"{ai} — {bi}"| {b}' if not show_bridge
                     else f'  {a} ---|"{ai} ↔ {bi}"| {b}')
    return "\n".join(lines)


def _solicited6(addr):
    """IPv6 주소 -> solicited-node 멀티캐스트 주소와 그 프레임의 목적지 MAC.

    NDP 가 브로드캐스트를 쓰지 않는다는 것을 교재에서 보여 주려면 이 두 값이 필요하다.
    손으로 적으면 반드시 틀린다 (실제로 한 번 틀렸다).
    """
    p = ipaddress.ip_address(addr).packed
    sol = ipaddress.ip_address(
        int(ipaddress.ip_address("ff02::1:ff00:0")) | (p[13] << 16) | (p[14] << 8) | p[15])
    q = sol.packed
    mac = "33:33:" + ":".join(f"{b:02x}" for b in q[12:])
    return {"addr": str(sol), "mac": mac}


def _eui64_id(mac):
    """MAC -> EUI-64 인터페이스 ID (64비트 정수).

    가운데에 ff:fe 를 끼우고 첫 바이트의 U/L 비트를 뒤집는다.
    M5 교재가 이 값을 손으로 적지 않게 하려고 여기서 계산한다.
    """
    b = [int(x, 16) for x in mac.split(":")]
    b[0] ^= 0x02
    parts = b[:3] + [0xff, 0xfe] + b[3:]
    v = 0
    for x in parts:
        v = (v << 8) | x
    return v


def _addr6(prefix, iid):
    net = ipaddress.ip_network(prefix)
    return str(ipaddress.ip_address(int(net.network_address) + iid))


# --------------------------------------------------------------- 교재용 컨텍스트
def doc_context(lab_id=1, stage="m10"):
    """모듈 교재 템플릿에 넘길 값. 주소를 교재에 하드코딩하지 않기 위한 것."""
    nodes = {c["node"]: c for c in all_nodes(lab_id, stage)}
    ip, ipc, mac, ifs = {}, {}, {}, {}
    ip6, ip6c = {}, {}
    for name, c in nodes.items():
        for i in c["lab_interfaces"]:
            if i.get("active") and i.get("ipv4") and i.get("role") in ("access", "public", "external-site"):
                ip[name], ipc[name] = i["ipv4"].split("/")[0], i["ipv4"]
                # dual-stack 교재(M5)가 주소를 지어내지 않도록 IPv6 도 같은 규칙으로 뽑는다
                if i.get("ipv6"):
                    ip6[name], ip6c[name] = i["ipv6"].split("/")[0], i["ipv6"]
                break
        for i in c["lab_interfaces"]:
            if i.get("active") and i.get("mac"):
                mac[name] = i["mac"]
                break
        ifs[name] = {i["name"]: i for i in c["lab_interfaces"] if i.get("active")}

    # 어느 포트로 어느 장비에 붙어 있는가. 구성도가 "eth2" 를 손으로 적지 않게 한다.
    port = {n: {i["peer"]: i["name"] for i in v.values() if i.get("peer")}
            for n, v in ifs.items()}

    def end(node, peer):
        """링크 한쪽 끝의 표기 — `포트 .끝자리`. 랩 지도(topolayout)와 같은 규칙이다.

        교재의 작은 구성도가 포트와 주소를 손으로 적으면 설계가 바뀔 때 어긋난다.
        같은 함수에서 나오면 지도와 구성도가 같은 말을 한다.
        """
        for i in (ifs.get(node) or {}).values():
            if i.get("peer") != peer:
                continue
            v = (i.get("ipv4") or "").split("/")[0]
            return f'{i["name"]} .{v.split(".")[-1]}' if v else i["name"]
        raise KeyError(f"{node} 에는 {peer} 로 가는 포트가 없다 (stage={stage})")

    def netmask(cidr):
        """`10.10.32.0/19` → `255.255.224.0`. Cisco 표기를 교재가 손으로 적지 않게 한다."""
        return str(ipaddress.ip_network(cidr, strict=False).netmask)

    _byname = {n["name"]: n for n in TOPO["nodes"]}

    def ip_at(node, config_stage, cidr=False):
        """그 노드가 config_stage 까지 설정된 상태에서 갖는 주소.

        M2 처럼 초기 구성이 stage_override 로 달라지는 모듈이 "시작할 때의 주소"를
        손으로 적지 않게 한다. cidr=True 면 프리픽스까지 붙는다.
        """
        c = node_config(lab_id, _byname[node], stage, config_stage)
        for i in c["lab_interfaces"]:
            if (i.get("active") and i.get("ipv4")
                    and i.get("role") in ("access", "public", "external-site")):
                return i["ipv4"] if cidr else i["ipv4"].split("/")[0]
        raise KeyError(f"{node} 에는 {config_stage} 시점의 주소가 없다")

    segs = IPAM["ipv4"]["segments"]

    # MAC 에서 파생되는 IPv6 값들 (M5 의 EUI-64 / SLAAC 설명용)
    eui64, lladdr6, slaac6 = {}, {}, {}
    for name, m in mac.items():
        iid = _eui64_id(m)
        eui64[name] = _addr6("::/64", iid)
        lladdr6[name] = _addr6("fe80::/64", iid)
        for seg_name, seg in segs.items():
            if name in seg.get("hosts", {}):
                slaac6[name] = _addr6(IPAM["ipv6"]["segments"][seg_name]["prefix"], iid)
                break

    return {
        "lab_id": lab_id, "stage": stage,
        "nodes": nodes, "ifs": ifs, "port": port, "end": end, "ip_at": ip_at,
        "netmask": netmask,
        "ip": ip, "ipc": ipc, "mac": mac,
        "cidr": {k: v["cidr"] for k, v in segs.items()},
        "gw": {k: v["gateway"] for k, v in segs.items()},
        "ip6": ip6, "ip6c": ip6c,
        # SLAAC / link-local 은 MAC 에서 파생된다 — 교재가 손으로 적지 않게 계산해 둔다
        "eui64": eui64, "lladdr6": lladdr6, "slaac6": slaac6,
        "sol6": {n: _solicited6(a) for n, a in ip6.items()},
        "sol6gw": {k: _solicited6(v["gateway"]) for k, v in IPAM["ipv6"]["segments"].items()},
        "v6": {k: v["prefix"] for k, v in IPAM["ipv6"]["segments"].items()},
        "gw6": {k: v["gateway"] for k, v in IPAM["ipv6"]["segments"].items()},
        "p2p6": IPAM["ipv6"]["p2p"], "loopback6": IPAM["ipv6"]["loopback"],
        "zone": {k: v["cidr"] for k, v in IPAM["ipv4"]["summary"].items()},
        "p2p": IPAM["ipv4"]["p2p"], "loopback": IPAM["ipv4"]["loopback"],
        # 라우팅 정책(area · 타이머 · passive 목록)도 교재가 지어내지 않게 여기서 넘긴다
        "routing": ROUTING,
        "public": IPAM["public"]["ipv4"], "dns": IPAM["dns"],
        # 서비스 포트(FTP passive 대역 등)도 교재가 지어내지 않게 넘긴다 — M7 · M8 · M9
        "services": IPAM["services"],
        "mgmt": {n: mgmt_ip(lab_id, n) for n in IPAM["management"]["hosts"]},
        "mgmt_cidr": mgmt_cidr(lab_id), "mgmt_if": IPAM["management"]["interface"],
        "lab_block": IPAM["ipv4"]["lab_block"],
        "console_url": IPAM["access"]["proxmox"]["api_endpoint"],
        "jump_ip": IPAM["access"]["jump_host"]["office_ip"],
        "lab_user": IPAM["access"]["lab_user"],
        "topology": mermaid(lab_id, stage),
        "vlans": {v["id"]: v for v in TOPO["vlans"]},
        # 실습용 보조 값 — 교재가 주소를 지어내지 않도록 여기서 계산한다
        "unused_ip": {k: str(ipaddress.ip_network(v["cidr"]).network_address + 99)
                      for k, v in segs.items()},
        "bcast": {k: str(ipaddress.ip_network(v["cidr"]).broadcast_address)
                  for k, v in segs.items()},
        "fake_mac": "52:54:00:de:ad:01",
        # 중간 점검이 놓인 단계. 교재가 "M3 · M6" 을 손으로 적지 않게 한다 —
        # config/site.yml 에서 지점을 옮기면 교재도 따라 바뀌어야 하기 때문.
        "checkpoints": [c["stage"].upper()
                        for c in SITE.get("console", {}).get("drill", {})
                        .get("checkpoints", [])],
    }
