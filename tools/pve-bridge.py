#!/usr/bin/env python3
"""
Proxmox 에 브리지가 실제로 있는지, 무엇이 물려 있는지 물어본다.

왜 필요한가
    `terraform destroy` 가 0 으로 끝나는 것과 브리지가 실제로 사라지는 것은
    **다른 사건**이다. tfstate 가 비어도 호스트에는 남아 있을 수 있다.
    Proxmox 의 네트워크 변경은 /etc/network/interfaces.new 에 먼저 쌓이고,
    웹의 [Apply Configuration] 을 눌러야 반영된다.
    걷어내기 스크립트가 종료 코드만 보고 "제거됨" 을 찍어서, 관리자가
    다 지워진 줄 알고 재설치에 들어가는 일이 실제로 있었다 (docs/TODO.md 1).

usage
    python3 tools/pve-bridge.py --check vmbr9      있으면 1, 없으면 0, 못 물어보면 2
    python3 tools/pve-bridge.py --attached vmbr9   그 브리지에 물린 VM 을 나열

브리지를 지우지는 않는다. 이 프로젝트는 Proxmox 호스트 설정을 스스로 바꾸지 않는다.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import pve                     # noqa: E402

CANT_ASK = 2


def ifaces(cfg):
    return pve._api(cfg, f"/api2/json/nodes/{cfg['node']}/network") or []


def attached(cfg, bridge):
    """이 브리지에 NIC 을 걸고 있는 VM 목록. [(vmid, name, [netN...])]"""
    out = []
    for vm in pve._api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu") or []:
        vmid = vm.get("vmid")
        if vmid is None:
            continue
        try:
            conf = pve._api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu/{vmid}/config") or {}
        except Exception:                                  # noqa: BLE001
            continue                                       # 한 대를 못 읽어도 나머지는 본다
        # 값은 "virtio=52:54:..,bridge=vmbr9,tag=3001" 꼴이다.
        # 부분 문자열로 찾으면 vmbr9 를 찾다가 vmbr91 에 걸린다 — 쉼표로 끊어서 본다.
        nics = [k for k, v in conf.items()
                if k.startswith("net") and k[3:].isdigit()
                and f"bridge={bridge}" in str(v).split(",")]
        if nics:
            out.append((vmid, vm.get("name") or "", sorted(nics)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="Proxmox 브리지 상태 확인")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", metavar="BRIDGE", help="있으면 1, 없으면 0 으로 끝난다")
    g.add_argument("--attached", metavar="BRIDGE", help="물려 있는 VM 을 나열한다")
    a = ap.parse_args()
    bridge = a.check or a.attached

    cfg = pve.config()
    if not (cfg.get("token_id") and cfg.get("token_secret")):
        print("API 토큰이 없다 — Proxmox 에 물어볼 수 없다", file=sys.stderr)
        return CANT_ASK
    try:
        found = next((i for i in ifaces(cfg) if i.get("iface") == bridge), None)
    except Exception as e:                                 # noqa: BLE001
        print(f"Proxmox 에 물어보지 못했다: {e}", file=sys.stderr)
        return CANT_ASK

    if a.attached:
        try:
            rows = attached(cfg, bridge)
        except Exception as e:                             # noqa: BLE001
            print(f"VM 목록을 읽지 못했다: {e}", file=sys.stderr)
            return CANT_ASK
        for vmid, name, nics in rows:
            print(f"{vmid}\t{name}\t{','.join(nics)}")
        return 1 if rows else 0

    if found:
        print(f"{bridge} 가 아직 있다"
              + (f" (포트: {found['bridge_ports']})" if found.get("bridge_ports") else "")
              + (f" ({found['cidr']})" if found.get("cidr") else ""))
        return 1
    print(f"{bridge} 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
