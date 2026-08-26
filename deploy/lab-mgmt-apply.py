#!/usr/bin/env python3
"""
운영 서버를 랩 관리망에 붙이는 일 중 **root 가 필요한 부분만** 한다.

  /usr/local/sbin/lab-mgmt-apply            netplan 기록 + apply + 확인
  /usr/local/sbin/lab-mgmt-apply --probe    아무것도 하지 않고 권한만 확인
  /usr/local/sbin/lab-mgmt-apply --show     쓰게 될 내용을 보여주기만 한다

왜 이 파일이 따로 있나
  트렁크 NIC 을 붙이는 일은 Proxmox API 로 하므로 콘솔 계정으로 충분하다.
  root 가 필요한 것은 netplan 파일을 쓰고 적용하는 것뿐이다. 그 조각만 떼어
  root 소유 프로그램으로 만들고, 콘솔에는 **인자 없는 실행**만 허용한다.

  저장소(tools/)의 스크립트를 sudo 로 돌리면 안 된다 — 저장소를 쓸 수 있는
  콘솔 계정이 곧 root 가 되기 때문이다. 그래서 이 프로그램은 저장소를 읽지 않는다.
  필요한 값은 전부 root 소유 /etc/my-network-lab/policy.json 에서 온다.

  인터페이스 **이름**도 받지 않는다. 이름을 받으면 "어느 인터페이스에 주소를 쓸지"를
  콘솔이 고르게 되는 셈이다. MAC 으로 직접 찾는다 — 그 MAC 도 policy.json 에 있다.

되돌리기
  sudo rm /etc/netplan/60-lab-mgmt.yaml && sudo netplan apply
"""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

POLICY = "/etc/my-network-lab/policy.json"
MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
IFACE_RE = re.compile(r"^[a-z][a-z0-9]{0,14}$")


def die(msg, hint=""):
    print(f"✘ {msg}", file=sys.stderr)
    for line in hint.splitlines():
        if line:
            print(f"   {line}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"  ✔ {msg}")


def say(msg):
    print(f"  {msg}")


# --------------------------------------------------------------- 정책 읽기
def load_policy():
    p = Path(POLICY)
    if not p.exists():
        die(f"{POLICY} 가 없다",
            "install.sh 가 만든다:  ./install.sh --no-apt")
    st = p.stat()
    # root 소유가 아니면 이 파일을 믿을 수 없다 — 그러면 이 프로그램은 그냥
    # "콘솔 계정이 시키는 대로 하는 root" 가 된다.
    if st.st_uid != 0:
        die(f"{POLICY} 가 root 소유가 아니다 (uid {st.st_uid})",
            "누군가 바꿔치기했을 수 있다. 확인 없이 실행하지 않는다.")
    if st.st_mode & 0o022:
        die(f"{POLICY} 를 root 아닌 사용자가 쓸 수 있다 (mode {st.st_mode & 0o777:o})",
            "chmod 0644 로 고칠 것")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        die(f"{POLICY} 를 읽지 못했다: {e}")


def mgmt_plan(pol):
    """policy.json 의 mgmt 블록을 검증해서 돌려준다.

    root 소유 파일이지만 그래도 형태를 확인한다 — 생성기가 어긋났을 때
    조용히 이상한 주소를 넣는 것보다 여기서 멈추는 편이 낫다.
    """
    m = pol.get("mgmt")
    if not isinstance(m, dict):
        die("policy.json 에 mgmt 항목이 없다",
            "저장소에서 다시 만들 것:\n"
            "  python3 tools/gen-policy.py | sudo tee /etc/my-network-lab/policy.json")

    mac = str(m.get("trunk_mac", "")).lower()
    if not MAC_RE.match(mac):
        die(f"trunk_mac 이 MAC 형식이 아니다: {m.get('trunk_mac')!r}")

    path = str(m.get("netplan", ""))
    # /etc/netplan 밖에는 쓰지 않는다. 여기가 뚫리면 root 가 임의의 파일을 쓴다.
    if not path.startswith("/etc/netplan/") or "/.." in path or not path.endswith(".yaml"):
        die(f"netplan 경로가 허용 범위 밖이다: {path!r}",
            "/etc/netplan/*.yaml 만 허용한다")

    vlans = m.get("vlans")
    if not isinstance(vlans, list) or not vlans:
        die("mgmt.vlans 가 비어 있다")
    out = []
    for v in vlans:
        try:
            iface, vid = str(v["iface"]), int(v["vlan"])
            ip, plen = str(v["ops_ip"]), int(v["prefixlen"])
        except (KeyError, TypeError, ValueError):
            die(f"mgmt.vlans 항목이 온전하지 않다: {v!r}")
        if not IFACE_RE.match(iface):
            die(f"인터페이스 이름이 이상하다: {iface!r}")
        if not 1 <= vid <= 4094:
            die(f"VLAN 번호가 범위를 벗어났다: {vid}")
        try:
            ipaddress.IPv4Interface(f"{ip}/{plen}")
        except ValueError:
            die(f"주소가 이상하다: {ip}/{plen}")
        out.append({"iface": iface, "vlan": vid, "ip": ip, "plen": plen})
    return mac, path, out


# --------------------------------------------------------------- 인터페이스
def iface_for_mac(mac):
    """MAC 으로 인터페이스를 찾는다. VLAN 서브인터페이스는 부모와 MAC 이 같으므로 뺀다."""
    for d in sorted(Path("/sys/class/net").iterdir()):
        if d.name == "lo" or (d / "lower_0").exists():   # lower_* = VLAN/bond 자식
            continue
        try:
            if (d / "address").read_text().strip().lower() == mac:
                return d.name
        except OSError:
            continue
    return None


def build_netplan(trunk, vlans):
    lines = ["# 자동 생성 — /usr/local/sbin/lab-mgmt-apply",
             "# 랩 관리망(OOB). 전 랩 공용 브리지의 VLAN 을 서브인터페이스로 받는다.",
             "# 게이트웨이를 주지 않는다 — 이 인터페이스들은 관리망 안에서만 쓴다.",
             "network:",
             "  version: 2",
             "  ethernets:",
             f"    {trunk}:",
             "      dhcp4: false        # 트렁크 자체는 주소를 갖지 않는다",
             "      dhcp6: false",
             "      optional: true",
             "  vlans:"]
    for v in vlans:
        lines += [f"    {v['iface']}:",
                  f"      id: {v['vlan']}",
                  f"      link: {trunk}",
                  f"      addresses: [{v['ip']}/{v['plen']}]",
                  "      dhcp4: false",
                  "      optional: true"]
    return "\n".join(lines) + "\n"


def main(a):
    if a.probe:
        # 아무것도 읽지 않고 끝낸다. 콘솔이 "이 버튼을 보여도 되는가" 를 물을 때 쓴다.
        return 0
    if os.geteuid() != 0:
        die("root 로 실행해야 한다", "sudo -n /usr/local/sbin/lab-mgmt-apply")

    mac, path, vlans = mgmt_plan(load_policy())

    trunk = iface_for_mac(mac)
    if not trunk:
        die(f"MAC {mac} 인 인터페이스가 이 서버에 없다",
            "트렁크 NIC 이 아직 안 붙었거나 게스트에 나타나지 않았다.\n"
            "  콘솔 [설치] 의 [관리망 연결] 이 이 단계를 먼저 한다.\n"
            "  핫플러그가 막힌 장비라면 한 번 재부팅한 뒤 다시 누를 것.")
    ok(f"트렁크 {trunk} (MAC {mac})")

    body = build_netplan(trunk, vlans)
    if a.show:
        print()
        print(body, end="")
        return 0

    p = Path(path)
    if p.exists():
        # 되돌릴 자리를 남긴다. 원격으로 접속해 있는 서버의 네트워크를 바꾸는 일이다.
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_bytes(p.read_bytes())
        os.chmod(bak, 0o600)
        say(f"기존 파일을 {bak} 로 백업했다")

    # 다른 netplan 파일은 건드리지 않는다. 이 파일 하나만 더한다.
    p.write_text(body, encoding="utf-8")
    os.chmod(path, 0o600)
    ok(f"{path} 기록 (VLAN {len(vlans)}개)")

    r = subprocess.run(["netplan", "apply"], capture_output=True, text=True)
    if r.returncode:
        die(f"netplan apply 실패: {(r.stderr or r.stdout).strip()}",
            f"되돌리려면:  sudo rm {path} && sudo netplan apply")
    ok("netplan apply")

    # 실제로 주소가 붙었는지 본다. netplan 은 문법이 맞으면 조용히 성공한다 —
    # 링크가 안 올라와도 그렇다. "적용했다" 와 "붙었다" 는 다른 말이다.
    got = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                         capture_output=True, text=True).stdout
    bad = [v for v in vlans if f"{v['ip']}/" not in got]
    for v in vlans:
        (say if v in bad else ok)(
            f"{v['iface']:<8s} {v['ip']}" + ("  없음" if v in bad else ""))
    if bad:
        print(f"\n{len(bad)}개가 올라오지 않았다. 되돌리려면:\n"
              f"  sudo rm {path} && sudo netplan apply", file=sys.stderr)
        return 1
    print("\n관리망 연결 완료. 랩을 만들고 지워도 여기는 건드리지 않는다.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="관리망 netplan 적용 (root 전용)")
    ap.add_argument("--probe", action="store_true", help="권한 확인만 (아무것도 하지 않는다)")
    ap.add_argument("--show", action="store_true", help="쓰게 될 내용만 보여준다")
    sys.exit(main(ap.parse_args()))
