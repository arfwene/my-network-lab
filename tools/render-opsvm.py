#!/usr/bin/env python3
"""
운영 서버(랩 운영 VM)를 관리망에 연결하는 절차 생성.

usage:  python3 tools/render-opsvm.py [--labs 9] [--vmid 9100] [--net 1]
출력:   dist/ops-server.md

관리망은 **전 랩 공용 VLAN-aware 브리지 하나**다. 그래서 운영 서버에 붙이는 NIC 은
랩 수와 무관하게 **하나**(트렁크)뿐이고, 랩은 그 위의 VLAN 서브인터페이스로 갈린다.
랩을 만들거나 지울 때 Proxmox 도 VM 하드웨어도 건드리지 않는다.

주소를 문서에 적지 않는다 — config/site.yml 에서 계산한다.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L


def main(labs, vmid, net):
    ms = L.mgmt_labs(labs)
    br = L.mgmt_bridge_name()
    n = len(ms)
    plen = L.IPAM["management"]["subnet_prefix"]
    office = L.IPAM["access"]["office_lan"]
    maxlab = L.IPAM["labs"]["id_range"][1]
    vm = f"{vmid}" if vmid else "<운영서버 VMID>"

    netplan = "\n".join(
        f"    {m['iface']}:\n"
        f"      id: {m['vlan']}\n"
        f"      link: <net{net} 인터페이스>\n"
        f"      addresses: [{m['ops_ip']}/{plen}]"
        for m in ms)

    table = "\n".join(
        f"| lab{m['lab_id']} | {m['vlan']} | `{m['iface']}` | `{m['ops_ip']}/{plen}` | `{m['cidr']}` |"
        for m in ms)

    checks = "\n".join(f"ping -c1 -W1 {m['gateway']}    # 랩{m['lab_id']} — Proxmox 호스트"
                       for m in ms)

    body = f"""<!-- 자동 생성 파일. 직접 수정하지 말 것.
     생성: python3 tools/render-opsvm.py
     원본: config/site.yml (+ site.local.yml), design/ipam.yml, design/topology.yml -->

# 운영 서버를 관리망에 연결하기 — **1회 작업**

> 이 문서의 작업은 처음 한 번만 한다.
> 그 뒤로는 랩을 만들거나 지워도 **Proxmox 호스트도 운영 서버의 VM 설정도 건드리지 않는다.**

## 왜 한 번으로 끝나는가

관리망은 랩마다 브리지를 두지 않는다. **브리지 하나(`{br}`)를 랩별 VLAN 으로 나눈다.**

```
{br}  (VLAN-aware, 물리 포트 없음)
  ├ 랩1 노드 net0   tag {ms[0]['vlan']}     ← Terraform 이 랩 만들 때 붙인다
  ├ 랩2 노드 net0   tag {ms[1]['vlan'] if n > 1 else '-'}
  │   ...
  └ 운영 서버 net{net}   태그 없음(트렁크)  ← **딱 하나. 영구.**
```

랩을 만들면 그 랩 노드들이 자기 태그를 달고 이 브리지에 들어온다.
랩을 지우면 그 VM 들이 사라질 뿐이다. **운영 서버 쪽은 아무 변화가 없다.**

랩 링크 브리지(`vmbr{{N}}101` …)에 운영 서버를 붙일 일은 없다.
랩 서비스망은 격리가 목적이고, 운영 서버는 관리망으로만 노드에 닿는다.

### 격리는 어떻게 유지되나

태깅을 **브리지가** 한다. 랩 노드의 게스트는 untagged 프레임만 보므로 다른 VLAN 을
주입할 수 없다 — 브리지를 랩마다 나눈 것과 격리 수준이 같다.
그리고 이 브리지에는 물리 NIC 이 없으므로 이 L2 는 호스트 밖으로 나가지 않는다.

---

## 1. 관리망 브리지 만들기 (1회)

```bash
make mgmt LABS={maxlab}
```

랩 수와 상관없이 브리지는 **하나**다. `LABS` 는 문서·검사에 쓸 VLAN 범위를 정할 뿐이니
처음부터 최대치({maxlab})로 두면 나중에 랩을 늘릴 때도 다시 할 일이 없다.

> `vlan_aware` 가 켜져 있어야 태그가 동작한다. 꺼져 있으면 **전 랩 관리망이 한 L2 로 합쳐지고**
> 통신은 되므로 눈치채기 어렵다. 배포 전 검사가 이 상태를 오류로 잡는다.

## 2. 운영 서버 VM 에 트렁크 NIC 붙이기 (1회)

```bash
qm set {vm} -net{net} virtio,bridge={br}
```

**태그를 주지 않는다.** 태그 없는 포트가 곧 트렁크이고, 그래야 모든 랩의 VLAN 이 들어온다.

`net0` 은 사무실 LAN({office}) 쪽으로 이미 붙어 있다고 본다 —
교육생 브라우저와 Proxmox API 가 그리로 들어온다.
`-net{net}` 이 이미 쓰이고 있으면 `--net <빈 번호>` 로 다시 생성할 것.

## 3. 운영 서버 안에서 VLAN 서브인터페이스 만들기 (1회)

먼저 새 NIC 의 이름을 확인한다 (게스트에서의 이름은 환경마다 다르다).

```bash
ip -br link
```

`/etc/netplan/60-lab-mgmt.yaml`:

```yaml
network:
  version: 2
  ethernets:
    <net{net} 인터페이스>:
      dhcp4: false          # 트렁크 자체는 주소를 갖지 않는다
  vlans:
{netplan}
```

```bash
sudo chmod 600 /etc/netplan/60-lab-mgmt.yaml
sudo netplan apply
```

**게이트웨이(`routes`/`gateway4`)를 주지 않는다.** 이 인터페이스들은 관리망 안에서만 쓴다 —
기본 경로가 여러 개가 되면 사무실로 나가는 트래픽이 어디로 나갈지 흔들린다.

### 이 랩의 배치

| 랩 | VLAN | 운영 서버 인터페이스 | 운영 서버 주소 | 관리망 |
|---|---|---|---|---|
{table}

## 4. 확인

```bash
{checks}
```

```bash
make doctor          # "관리망 브리지" · "이 서버의 관리망 주소" 가 초록인지 본다
```

Proxmox 호스트에도 주소를 주려면 호스트 쪽에 VLAN 서브인터페이스를 만든다
(`{br}.{ms[0]['vlan']}` 에 `{ms[0]['gateway']}/{plen}`). **선택 사항이다** — 랩 노드와의 통신에는
필요 없고, 경로를 확인할 방법이 하나 늘 뿐이다. 주소를 준다면 `dist/host-guard.nft` 를
함께 검토할 것 (랩에서 하이퍼바이저로 오는 신규 연결을 막는 규칙).

---

## 랩을 늘리거나 줄일 때

**아무것도 하지 않는다.** `make deploy LAB=7` 로 랩을 만들면 그 노드들이 VLAN {L.mgmt_vlan(7)} 을
달고 기존 브리지에 들어온다. 운영 서버에 `mgmt7` 이 이미 있으면 바로 통한다.

`LABS={maxlab}` 로 만들어 두지 않았다면 늘어난 랩의 서브인터페이스만 추가한다:

```bash
python3 tools/render-opsvm.py --labs {maxlab} --vmid {vm}   # 문서 다시 생성
```

## 랩을 지울 때

```bash
cd infra/terraform/envs/lab3 && terraform destroy
```

VM {len(L.TOPO['nodes'])}대와 랩 링크 브리지 {len(L.TOPO['bridges'])}개가 사라진다.
관리망 브리지 `{br}` 와 운영 서버의 `{L.mgmt_iface(3)}` 는 **그대로 남는다** — 다른 랩이 쓰고 있고,
같은 랩을 다시 만들 때 그대로 이어 쓴다. 남아 있어도 트래픽이 없으므로 비용이 없다.
"""
    out = L.ROOT / "dist/ops-server.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"generated {out}  ({br} 트렁크 net{net} · 랩 {n}개 VLAN "
          f"{ms[0]['vlan']}~{ms[-1]['vlan']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", type=int, default=None)
    ap.add_argument("--vmid", default=None, help="운영 서버 VM 의 VMID")
    ap.add_argument("--net", type=int, default=1, help="트렁크 NIC 의 netN 번호")
    a = ap.parse_args()
    main(a.labs, a.vmid, a.net)
