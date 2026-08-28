#!/usr/bin/env python3
"""
설계 -> Ansible 인벤토리 생성.

usage:  python3 tools/gen-inventory.py --lab 1 [--stage m10] [--config-stage m9]

  --stage         어떤 장비·링크가 존재하는가
  --config-stage  어느 모듈의 목표 설정까지 올릴 것인가 (없으면 --stage 와 같다)

출력: infra/ansible/inventory/lab<N>/{hosts.yml, group_vars/all.yml, host_vars/<node>.yml}
"""
import sys, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "console"))
import labdesign as L
import sshkeys

GROUP_OF = {"router": "routers", "switch": "switches", "host": "hosts_pc",
            "server": "servers", "edge": "edges"}


def _valid_admin_keys():
    """site.yml 의 공개키 중 **실제로 쓸 수 있는 것**만.

    예시값(AAAA...)이 그대로 남아 있는 경우가 흔하다. 그대로 배포하면
    authorized_keys 에 쓰레기 줄이 남고, 진짜 문제(키가 없다)는 가려진다.
    """
    raw = L.IPAM["access"].get("ssh_public_keys") or []
    good, bad = [], []
    for k in raw:
        try:
            good.append(sshkeys.normalize(k))
        except Exception as e:                          # noqa: BLE001
            bad.append(f"{str(k)[:30]}... ({e})")
    if bad:
        print(f"경고: site.yml 의 ssh_public_keys 중 {len(bad)}개가 유효하지 않아 뺐다:",
              file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
    if not good:
        print("경고: 배포할 관리자 공개키가 하나도 없다. "
              "Ansible 이 노드에 접속하지 못한다 — site.local.yml 의 "
              "access.ssh_public_keys 를 채울 것", file=sys.stderr)
    return good


def _lab_keys(lab_id):
    """이 랩에 배포할 교육생 공개키.

    콘솔 DB 에서 읽는다 — site.yml 에 두면 **전 랩 전 노드**에 박히고, 명단이 바뀔 때마다
    VM 을 다시 만들어야 한다. 여기서 읽으면 배정된 랩에만 들어가고 즉시 반영된다.

    DB 가 없으면(설치 직후·CI) 빈 목록이다. 운영 서버 키는 어차피 cloud-init 으로
    들어가 있으므로 Ansible 접속에는 지장이 없다.
    """
    if not (L.ROOT / "var/console.db").exists():
        return []
    try:
        import db                                      # noqa: PLC0415
        out = []
        for k in db.lab_keys(lab_id):
            try:
                out.append({"username": k["username"], "name": k["name"],
                            "key": sshkeys.normalize(k["key"])})
            except Exception as e:                     # noqa: BLE001
                print(f"경고: {k['username']} 의 공개키를 뺐다 ({e})", file=sys.stderr)
        return out
    except Exception as e:                             # noqa: BLE001
        print(f"경고: 교육생 키를 읽지 못했다 ({type(e).__name__}: {e}). "
              f"관리자 키만 배포된다", file=sys.stderr)
        return []


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


def main(lab_id, stage, config_stage=None):
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
        # 단계 하나가 두 가지를 결정하고 있었다 — **어떤 장비가 있는가**와
        # **어디까지 설정하는가**. 교육생이 직접 만들게 하려면 그 둘을 갈라야 한다.
        #   M4 의 초기 구성 = r3 는 있고(장비는 m4), OSPF 는 없다(설정은 m3).
        # 안 주면 지금까지와 똑같이 동작한다.
        "config_stage": config_stage or stage,
        "stages": L.STAGES,
        "ansible_user": "lab",
        "ansible_python_interpreter": "/usr/bin/python3",
        # 랩 노드 authorized_keys = 운영/관리자 키(site.yml) + 이 랩 교육생 키(콘솔 DB)
        "admin_ssh_keys": _valid_admin_keys(),
        "trainee_ssh_keys": _lab_keys(lab_id),
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

    cs = config_stage or stage
    print(f"generated {base}  (nodes={len(nodes)}, stage={stage}"
          + (f", config={cs}" if cs != stage else "") + ")")


if __name__ == "__main__":
    a = sys.argv
    main(int(a[a.index("--lab") + 1]) if "--lab" in a else 1,
         a[a.index("--stage") + 1] if "--stage" in a else "m10",
         a[a.index("--config-stage") + 1] if "--config-stage" in a else None)
