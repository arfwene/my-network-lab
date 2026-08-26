#!/usr/bin/env python3
"""
운영 서버를 랩 관리망에 연결한다 — **1회, 한 명령.**

  python3 tools/setup-mgmt-net.py            실제로 붙인다
  python3 tools/setup-mgmt-net.py --dry-run  무엇을 할지만 보여준다
  python3 tools/setup-mgmt-net.py --vmid 9100

하는 일
  ① 이 서버가 Proxmox 위의 어느 VM 인지 찾는다 (내 NIC 의 MAC ↔ VM 설정 대조)
  ② 관리망 브리지(VLAN 트렁크) NIC 이 없으면 API 로 붙인다 — 핫플러그, 재부팅 없음
  ③ 게스트에 나타난 그 인터페이스 위에 랩별 VLAN 서브인터페이스를 만든다
  ④ 주소가 실제로 붙었는지 확인한다

왜 이걸 자동으로 못 했었나
  ⓶ 는 **자기가 도는 VM** 의 설정을 바꾸는 일이고, ⓷ 은 root 가 필요하다.
  랩마다 반복된다면 위험하지만, 관리망은 전 랩 공용이라 이건 **1회 작업**이다.
  그래서 여기서 한 번에 끝낸다. 랩을 만들고 지울 때는 아무것도 하지 않는다.

root 로 실행하지 않는다. netplan 을 쓰는 부분에서만 sudo 를 쓴다 —
root 로 돌면 var/ 안의 파일이 root 소유가 되어 콘솔이 자기 DB 를 못 고친다.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import labdesign as L      # noqa: E402

NETPLAN = "/etc/netplan/60-lab-mgmt.yaml"
# root 가 필요한 부분(netplan 기록·apply)만 떼어 둔 프로그램.
# install.sh 가 설치하면 콘솔 버튼도 이 경로를 부른다 — 화면과 셸이 같은 길을 쓴다.
MGMT_HELPER = "/usr/local/sbin/lab-mgmt-apply"


def helper_ready():
    """비밀번호 없이 헬퍼를 부를 수 있는가.

    `sudo -n -l` 로 묻지 않는다 — 그건 권한 목록을 달라는 요청이라
    verifypw 기본값(all) 아래에서 비밀번호를 요구한다. 실제로 한 번 부른다.
    """
    if not Path(MGMT_HELPER).exists():
        return False
    try:
        return subprocess.run(["sudo", "-n", MGMT_HELPER, "--probe"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
G, Y, R, B, N = "\033[32m", "\033[33m", "\033[31m", "\033[34m", "\033[0m"


def say(m):   print(f"  {m}")
def ok(m):    print(f"  {G}✔{N} {m}")
def warn(m):  print(f"  {Y}!{N} {m}")
def step(m):  print(f"\n{B}▸ {m}{N}")
def die(m, hint=""):
    print(f"\n{R}✘ {m}{N}", file=sys.stderr)
    if hint:
        for line in hint.splitlines():
            print(f"   {line}", file=sys.stderr)
    sys.exit(1)


def local_macs():
    """이 서버의 인터페이스 → MAC (소문자). root 권한이 필요 없다."""
    out = {}
    for p in sorted(Path("/sys/class/net").iterdir()):
        if p.name == "lo" or (p / "device").exists() is False and p.name != "lo":
            pass
        try:
            out[p.name] = (p / "address").read_text().strip().lower()
        except OSError:
            continue
    return out


def iface_for_mac(mac):
    for name, m in local_macs().items():
        if m == mac.lower():
            return name
    return None


# ===================================================== Proxmox
def api(cfg, path, method="GET", data=None):
    import pve                                          # noqa: PLC0415
    import urllib.error, urllib.parse, urllib.request   # noqa: PLC0415
    url = pve.endpoint(cfg).rstrip("/") + path
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": "my-network-lab-setup"})
    req.add_header("Authorization",
                   f"PVEAPIToken={cfg['token_id']}={cfg['token_secret']}")
    with urllib.request.urlopen(req, timeout=15, context=pve._ctx(cfg)) as r:
        raw = r.read().decode("utf-8", "replace")
    return (json.loads(raw).get("data") if raw.strip().startswith("{") else None)


def find_my_vmid(cfg, node):
    """내 NIC 의 MAC 과 일치하는 VM 을 찾는다.

    product_uuid 는 root 만 읽을 수 있어서 MAC 으로 맞춘다 —
    어차피 net0(사무실 LAN)은 이미 붙어 있고 그 MAC 은 누구나 읽는다.
    """
    mine = set(local_macs().values())
    vms = api(cfg, "/api2/json/cluster/resources?type=vm") or []
    for v in vms:
        if v.get("node") != node or v.get("template"):
            continue
        try:
            conf = api(cfg, f"/api2/json/nodes/{node}/qemu/{v['vmid']}/config") or {}
        except Exception:                               # noqa: BLE001
            continue
        for k, val in conf.items():
            if not re.fullmatch(r"net\d+", k):
                continue
            m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", str(val))
            if m and m.group(1).lower() in mine:
                return int(v["vmid"]), conf
    return None, None


def free_net_slot(conf):
    used = {int(k[3:]) for k in conf if re.fullmatch(r"net\d+", k)}
    for i in range(1, 32):
        if i not in used:
            return i
    die("net0~net31 이 전부 차 있다. 쓰지 않는 NIC 을 먼저 뗄 것")


def existing_trunk(conf, bridge):
    """이미 이 브리지에 붙은 NIC 이 있으면 (슬롯, MAC)."""
    for k, val in conf.items():
        if not re.fullmatch(r"net\d+", k):
            continue
        if f"bridge={bridge}" not in str(val):
            continue
        # 태그가 있으면 트렁크가 아니다 — 특정 VLAN 전용 포트다
        if re.search(r"\btag=\d+", str(val)):
            continue
        m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", str(val))
        return int(k[3:]), (m.group(1).lower() if m else None)
    return None, None


# ===================================================== netplan
def build_netplan(trunk_iface, labs):
    lines = ["# 자동 생성 — python3 tools/setup-mgmt-net.py",
             "# 랩 관리망(OOB). 전 랩 공용 브리지의 VLAN 을 서브인터페이스로 받는다.",
             "# 게이트웨이를 주지 않는다 — 이 인터페이스들은 관리망 안에서만 쓴다.",
             "network:",
             "  version: 2",
             "  ethernets:",
             f"    {trunk_iface}:",
             "      dhcp4: false        # 트렁크 자체는 주소를 갖지 않는다",
             "      dhcp6: false",
             "      optional: true",
             "  vlans:"]
    for m in labs:
        lines += [f"    {m['iface']}:",
                  f"      id: {m['vlan']}",
                  f"      link: {trunk_iface}",
                  f"      addresses: [{m['ops_ip']}/{m['prefixlen']}]",
                  "      dhcp4: false",
                  "      optional: true"]
    return "\n".join(lines) + "\n"


def sudo_write(path, content, dry):
    if dry:
        say(f"(dry-run) {path} 에 {len(content.splitlines())}줄 기록")
        return
    p = subprocess.run(["sudo", "tee", path], input=content,
                       capture_output=True, text=True)
    if p.returncode:
        die(f"{path} 를 쓰지 못했다: {p.stderr.strip()}")
    subprocess.run(["sudo", "chmod", "600", path], check=False)


# ===================================================== main
def main(a):
    import pve                                          # noqa: PLC0415
    cfg = pve.config()
    bridge = L.mgmt_bridge_name()
    labs = L.mgmt_labs(a.labs)

    print("=" * 74)
    print(" 운영 서버 관리망 연결 (1회)")
    print("=" * 74)

    if os.geteuid() == 0:
        die("root 로 실행하지 말 것",
            "일반 계정으로 실행하면 netplan 부분에서만 sudo 를 쓴다.\n"
            "root 로 돌면 var/ 안의 파일이 root 소유가 되어 콘솔이 자기 DB 를 못 고친다.")
    if not (cfg["token_id"] and cfg["token_secret"]):
        die("Proxmox API 토큰이 없다",
            "콘솔 [관리자 → 연결 설정] 에 넣거나\n"
            "export PROXMOX_VE_API_TOKEN='terraform@pve!lab=...'")

    # --- ① 브리지 확인 -------------------------------------------------
    step(f"관리망 브리지 {bridge}")
    ifaces = api(cfg, f"/api2/json/nodes/{cfg['node']}/network") or []
    br = next((i for i in ifaces if i.get("iface") == bridge), None)
    if not br:
        die(f"{bridge} 가 Proxmox 에 없다",
            "콘솔 [관리자 → 설치] 의 [관리망 브리지 만들기] 를 먼저 누를 것 "
            "(셸에서라면  make mgmt)")
    if not br.get("bridge_vlan_aware"):
        die(f"{bridge} 가 VLAN-aware 가 아니다",
            "이 상태로는 랩별 VLAN 이 갈리지 않는다. "
            "콘솔 [설치] 에서 [관리망 브리지 만들기] 를 다시 누를 것")
    ok(f"{bridge} 있음 (VLAN-aware)")

    # --- ② 내 VM 찾기 --------------------------------------------------
    step("이 서버가 어느 VM 인가")
    if a.vmid:
        vmid = a.vmid
        conf = api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu/{vmid}/config") or {}
        ok(f"VMID {vmid} (직접 지정) · {conf.get('name', '?')}")
    else:
        vmid, conf = find_my_vmid(cfg, cfg["node"])
        if not vmid:
            die("이 서버에 해당하는 VM 을 찾지 못했다",
                "Proxmox 위의 VM 이 아니거나(물리 서버) 다른 노드에 있다.\n"
                "  · 물리 서버라면 이 스크립트를 쓸 수 없다 —\n"
                "    config/site.local.yml 의 access.jump_host.proxy_via_proxmox: true 로\n"
                "    Proxmox 호스트를 경유하도록 할 것 (docs/DEPLOY.md 3절)\n"
                "  · VM 인데 못 찾았다면:  --vmid <번호> 로 직접 지정")
        ok(f"VMID {vmid} · {conf.get('name', '?')}")

    # --- ③ 트렁크 NIC --------------------------------------------------
    step("트렁크 NIC")
    slot, mac = existing_trunk(conf, bridge)
    # 손으로 붙일 때 GUI 에서 VLAN Tag 를 채우기 쉽다. 태그가 있으면 그 포트는
    # 그 VLAN 전용이라 다른 랩의 관리망이 들어오지 않는다 — 트렁크가 아니다.
    tagged = [k for k, v in conf.items()
              if re.fullmatch(r"net\d+", k) and f"bridge={bridge}" in str(v)
              and re.search(r"\btag=\d+", str(v))]
    if tagged and slot is None:
        die(f"{bridge} 에 붙은 NIC({', '.join(tagged)})에 VLAN 태그가 있다",
            "태그가 있으면 그 VLAN 하나만 들어온다 — 트렁크가 아니다.\n"
            "  Proxmox 에서 그 NIC 의 'VLAN Tag' 를 **비우고** 다시 실행할 것.\n"
            "  (태그 없는 포트가 곧 트렁크이고, 그래야 랩 1~9 가 모두 들어온다)")
    if slot is not None:
        ok(f"net{slot} 이 이미 {bridge} 에 붙어 있다 (MAC {mac})")
        if not mac:
            die(f"net{slot} 의 MAC 을 읽지 못했다",
                "Proxmox 에서 그 NIC 을 지우고 다시 실행하면 스크립트가 붙여 준다")
    else:
        slot = free_net_slot(conf)
        mac = L.ops_trunk_mac()
        spec = f"virtio={mac},bridge={bridge}"
        say(f"net{slot} 에 {bridge} 트렁크를 붙인다 (태그 없음 = 모든 VLAN)")
        if a.dry_run:
            say(f"(dry-run) PUT /nodes/{cfg['node']}/qemu/{vmid}/config  net{slot}={spec}")
        else:
            try:
                api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu/{vmid}/config",
                    method="PUT", data={f"net{slot}": spec})
            except Exception as e:                      # noqa: BLE001
                die(f"NIC 을 붙이지 못했다: {type(e).__name__}: {e}",
                    "토큰에 VM.Config.Network 권한이 필요하다.\n"
                    "  ./infra/proxmox-setup.sh --show 로 확인할 것")
            ok(f"net{slot} 추가 (MAC {mac})")

    # --- ④ 게스트에서 인터페이스 찾기 -----------------------------------
    step("게스트에 나타났는가")
    iface = None
    if a.dry_run:
        iface = "<새 인터페이스>"
        say("(dry-run) 핫플러그 결과를 기다리지 않는다")
    else:
        for _ in range(30):
            iface = iface_for_mac(mac) if mac else None
            if iface:
                break
            time.sleep(1)
        if not iface:
            die("새 NIC 이 게스트에 나타나지 않았다",
                "핫플러그가 막혀 있을 수 있다. VM 을 한 번 재부팅한 뒤 다시 실행할 것:\n"
                "  sudo reboot   →   python3 tools/setup-mgmt-net.py")
        ok(f"{iface} (MAC {mac})")

    # --- ⑤ netplan -----------------------------------------------------
    step(f"VLAN 서브인터페이스 {len(labs)}개")
    for m in labs:
        say(f"{m['iface']:<8s} VLAN {m['vlan']}  {m['ops_ip']}/{m['prefixlen']}  (lab{m['lab_id']})")
    # netplan 부터는 root 다. 헬퍼가 깔려 있으면 그쪽에 넘긴다 —
    # 헬퍼는 인터페이스를 MAC 으로 **자기가** 찾고 주소도 root 소유 정책에서 읽으므로,
    # 여기서 무엇을 넘기든 root 가 하는 일은 달라지지 않는다.
    if helper_ready():
        if a.dry_run:
            say(f"(dry-run) sudo -n {MGMT_HELPER} --show")
            subprocess.run(["sudo", "-n", MGMT_HELPER, "--show"], check=False)
            print("dry-run 이므로 아무것도 바꾸지 않았다.")
            return 0
        say(f"root 헬퍼에 넘긴다: {MGMT_HELPER}")
        r = subprocess.run(["sudo", "-n", MGMT_HELPER])
        if r.returncode:
            die("관리망 적용에 실패했다",
                f"되돌리려면:  sudo rm {NETPLAN} && sudo netplan apply")
        print(f"{G}관리망 연결 완료.{N} 이후 랩을 만들고 지워도 여기는 건드리지 않는다.")
        return 0

    # 헬퍼가 없는 서버(예전 설치)는 예전 길로 간다.
    body = build_netplan(iface, labs)
    if Path(NETPLAN).exists() and not a.dry_run:
        subprocess.run(["sudo", "cp", NETPLAN, NETPLAN + ".bak"], check=False)
        warn(f"기존 파일을 {NETPLAN}.bak 로 백업했다")
    sudo_write(NETPLAN, body, a.dry_run)
    if a.dry_run:
        print()
        print(body)
        print("dry-run 이므로 아무것도 바꾸지 않았다.")
        return 0
    ok(f"{NETPLAN} 기록")

    # netplan apply 는 기존 인터페이스를 건드리지 않는다 (별도 파일에 추가만 한다).
    # 그래도 원격 접속 중이라면 되돌릴 방법이 있어야 한다.
    p = subprocess.run(["sudo", "netplan", "apply"], capture_output=True, text=True)
    if p.returncode:
        die(f"netplan apply 실패: {p.stderr.strip()}",
            f"되돌리려면:  sudo rm {NETPLAN} && sudo netplan apply")
    ok("netplan apply")

    # --- ⑥ 확인 --------------------------------------------------------
    step("주소가 붙었는가")
    time.sleep(2)
    got = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                         capture_output=True, text=True).stdout
    bad = []
    for m in labs:
        if f"{m['ops_ip']}/" in got:
            ok(f"{m['iface']:<8s} {m['ops_ip']}")
        else:
            bad.append(m)
            warn(f"{m['iface']:<8s} {m['ops_ip']} 없음")
    print("-" * 74)
    if bad:
        print(f"{Y}{len(bad)}개가 올라오지 않았다.{N} `ip -br addr` 로 확인하고, "
              f"필요하면 되돌릴 것:\n  sudo rm {NETPLAN} && sudo netplan apply")
        return 1
    print(f"{G}관리망 연결 완료.{N} 이후 랩을 만들고 지워도 여기는 건드리지 않는다.")
    print("  다음:  make doctor  →  make gen LAB=1  →  make deploy LAB=1")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="운영 서버 관리망 연결 (1회)")
    ap.add_argument("--vmid", type=int, help="운영 서버 VM 의 VMID (자동 탐지 실패 시)")
    ap.add_argument("--labs", type=int, default=None, help="준비할 랩 수 (기본: 최대치)")
    ap.add_argument("--dry-run", action="store_true", help="무엇을 할지만 보여준다")
    sys.exit(main(ap.parse_args()))
