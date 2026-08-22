#!/usr/bin/env python3
"""
배포 사전 점검 — "지금 이 서버에서 랩을 띄울 수 있는가".

  python3 tools/preflight.py            (= make doctor)
  python3 tools/preflight.py --lab 3    특정 랩의 관리망까지 확인

한 번에 "안 된다"고만 하면 원인을 찾을 수 없다. 어디까지 갔는지를 남긴다.
검사는 네 덩어리다.

  1) 실행 도구   python · venv · ansible · terraform · 컬렉션
  2) 설정        site.local.yml · validate-site · SSH 키
  3) Proxmox     console/pve.py 의 check() 를 그대로 쓴다 (TCP→TLS→인증→노드→스토리지→템플릿)
  4) 랩 관리망   Ansible 이 노드에 닿을 수 있는 경로가 있는가

종료 코드: 오류가 하나라도 있으면 1. CI·설치 스크립트가 이 값을 본다.
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import labdesign as L      # noqa: E402
import sshkeys            # noqa: E402

VENV = ROOT / "console/.venv"
G, Y, R, B, N = "\033[32m", "\033[33m", "\033[31m", "\033[34m", "\033[0m"
RESULTS = []


def add(status, title, detail="", hint=""):
    RESULTS.append((status, title, detail, hint))


def ok(t, d="", h=""):   add("ok", t, d, h)
def warn(t, d="", h=""):  add("warn", t, d, h)
def err(t, d="", h=""):   add("error", t, d, h)
def skip(t, d="", h=""):  add("skip", t, d, h)


def section(title):
    RESULTS.append(("section", title, "", ""))


def run(argv, timeout=20):
    """(성공?, 첫 줄) — 없는 명령·타임아웃도 예외 없이 돌려준다."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or p.stderr or "").strip().splitlines()
        return p.returncode == 0, (out[0] if out else "")
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"


def run_full(argv, timeout=20):
    """전체 출력이 필요할 때. run() 은 첫 줄만 준다."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False, ""


def venv_bin(name):
    """venv 안의 실행 파일을 우선 찾는다 (jobs.py 와 같은 규칙)."""
    p = VENV / "bin" / name
    return str(p) if p.exists() else shutil.which(name)


def ver_tuple(s):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


# ===================================================== 1. 실행 도구
def check_tools():
    section("실행 도구")

    v = sys.version_info
    if v >= (3, 10):
        ok("Python", f"{v.major}.{v.minor}.{v.micro} · {sys.executable}")
    else:
        err("Python", f"{v.major}.{v.minor} — 3.10 이상이 필요하다",
            "Ubuntu 22.04 이상 / Debian 12 이상을 쓸 것")

    if (VENV / "bin/uvicorn").exists():
        ok("웹 콘솔 venv", str(VENV))
    else:
        err("웹 콘솔 venv", f"{VENV} 가 없다", "`./install.sh` 또는 `make console-setup` 을 실행할 것")

    apb = venv_bin("ansible-playbook")
    if not apb:
        err("ansible-playbook", "없다",
            "`make console-setup` 이 venv 안에 설치한다. 시스템에 깔 필요는 없다")
    else:
        good, line = run([apb, "--version"])
        where = "venv" if str(VENV) in apb else "시스템 PATH"
        if good and ver_tuple(line) >= (2, 16):
            ok("ansible-playbook", f"{line}  [{where}]")
        elif good:
            warn("ansible-playbook", f"{line}  [{where}]", "2.16 이상을 권장한다")
        else:
            err("ansible-playbook", line)

    gal = venv_bin("ansible-galaxy")
    if not gal:
        skip("ansible.posix 컬렉션", "ansible-galaxy 가 없어 건너뛴다")
    else:
        good, _ = run([gal, "collection", "list", "ansible.posix"])
        if good:
            ok("ansible.posix 컬렉션", "설치됨 (roles/common 의 sysctl 모듈이 쓴다)")
        else:
            err("ansible.posix 컬렉션", "없다",
                f"{gal} collection install -r infra/ansible/requirements.yml")

    tf = shutil.which("terraform")
    if not tf:
        err("terraform", "PATH 에 없다",
            "`./install.sh` 가 설치한다. 수동 설치는 docs/DEPLOY.md 참고")
    else:
        good, line = run([tf, "version"])
        if good and ver_tuple(line) >= (1, 6):
            ok("terraform", f"{line}  ({tf})")
        elif good:
            err("terraform", f"{line} — 1.6 이상이 필요하다 ({tf})")
        else:
            err("terraform", line)

    # 랩 노드는 인터넷에 못 나가지만, 이 서버는 프로바이더를 받아야 한다.
    cached = list((Path.home() / ".terraform.d/plugin-cache").rglob("terraform-provider-proxmox*"))
    envs = sorted((ROOT / "infra/terraform/envs").glob("lab*/.terraform/providers"))
    if cached or envs:
        ok("bpg/proxmox 프로바이더", "내려받은 이력이 있다")
    else:
        warn("bpg/proxmox 프로바이더", "아직 없다 — 첫 `terraform init` 때 인터넷에서 받는다",
             "이 서버가 registry.terraform.io 로 나갈 수 있어야 한다")


# ===================================================== 2. 설정
def check_config(lab_id_for_keys=1):
    section("설정")

    local = ROOT / "config/site.local.yml"
    if local.exists():
        ok("site.local.yml", str(local))
    else:
        warn("site.local.yml", "없다 — site.yml 의 공개용 기본값(문서 전용 대역)으로 돈다",
             "cp config/site.local.yml.example config/site.local.yml 후 사내 값을 채울 것")

    good, _ = run([sys.executable, str(ROOT / "tools/validate-site.py")], timeout=60)
    if good:
        ok("site 검사", "오류 없음 (`make check` 로 전체 결과를 볼 수 있다)")
    else:
        err("site 검사", "오류가 있다", "`make check` 를 실행해 내용을 확인할 것")

    raw = L.IPAM["access"].get("ssh_public_keys") or []
    good, bad = [], []
    for k in raw:
        try:
            sshkeys.normalize(k)
            good.append(k)
        except Exception:                              # noqa: BLE001
            bad.append(k)
    if not good:
        err("운영 서버 SSH 공개키", f"쓸 수 있는 키가 없다 (등록 {len(raw)}개)",
            "site.local.yml 의 access.ssh_public_keys 에 이 서버의 공개키를 넣을 것 "
            f"({Path.home()}/.ssh/id_ed25519.pub). 없으면 Ansible 이 노드에 접속하지 못하고, "
            "설정 적용이 잠금 사고 방지를 위해 중단된다")
    elif bad:
        warn("운영 서버 SSH 공개키", f"{len(good)}개 사용 · {len(bad)}개는 형식이 아니라 제외된다",
             "예시값이 남아 있는지 확인할 것")
    else:
        ok("운영 서버 SSH 공개키", f"{len(good)}개")

    # 교육생 키는 여기가 아니라 콘솔 DB 에 있다. 랩별로 몇 개인지만 알려준다.
    if (L.ROOT / "var/console.db").exists():
        try:
            import db                                  # noqa: PLC0415
            n = len(db.lab_keys(lab_id_for_keys))
            ok(f"랩 {lab_id_for_keys} 교육생 등록 키", f"{n}개"
               + ("" if n else " — 교육생이 콘솔 [접속 키] 에서 등록하면 늘어난다"))
        except Exception:                              # noqa: BLE001
            pass

    priv = [p for p in ("id_ed25519", "id_rsa", "id_ecdsa")
            if (Path.home() / ".ssh" / p).exists()]
    if priv:
        ok("이 서버의 SSH 개인키", ", ".join(f"~/.ssh/{p}" for p in priv))
    else:
        warn("이 서버의 SSH 개인키", "없다",
             "Ansible 이 랩 노드에 붙을 때 쓴다. `ssh-keygen -t ed25519` 후 "
             "그 공개키를 site.local.yml 의 ssh_public_keys 에 넣을 것")


# ===================================================== 3. Proxmox
def check_proxmox():
    section("Proxmox 연결")
    if not (L.ROOT / "var/console.db").exists():
        warn("연결 설정", "아직 없다 (var/console.db 미생성)",
             "웹 콘솔을 띄우고 [관리자 → 연결 설정] 에서 주소와 API 토큰을 넣을 것")
        return
    try:
        import pve                                    # noqa: PLC0415
        res = pve.check()
    except Exception as e:                            # noqa: BLE001
        err("연결 점검", f"{type(e).__name__}: {e}")
        return
    for c in res.get("checks", []):
        add({"ok": "ok", "warn": "warn", "error": "error"}.get(c["status"], "skip"),
            f"Proxmox · {c['title']}", c.get("detail", ""), c.get("hint", ""))


# ===================================================== 4. 랩 관리망
def check_mgmt(lab_id):
    section(f"랩 {lab_id} 관리망 도달성")
    gw = L.mgmt_gateway(lab_id)
    cidr = L.mgmt_cidr(lab_id)
    br = L.mgmt_bridge_name()
    m = next(x for x in L.mgmt_labs(lab_id) if x["lab_id"] == lab_id)
    ops, vlan, ifname = m["ops_ip"], m["vlan"], m["iface"]

    # 관리망 브리지는 랩이 아니라 envs/mgmt 가 한 번만 만든다. 그게 됐는지부터 본다.
    if not (L.ROOT / "infra/terraform/envs/mgmt/terraform.tfstate").exists():
        warn("관리망 브리지", f"{br} 를 만든 기록이 없다",
             "`make mgmt LABS=9` 를 먼저 실행할 것 (최초 1회). "
             "브리지가 없으면 랩 VM 이 기동하지 못한다 — 절차: dist/ops-server.md")
    else:
        ok("관리망 브리지", f"{br} (전 랩 공용) · 이 랩 = VLAN {vlan}")

    # 이 서버가 그 VLAN 에 발을 걸치고 있는가 = 서브인터페이스에 주소가 있는가
    good, out = run_full(["ip", "-o", "-4", "addr", "show"], timeout=5)
    line = next((l for l in out.splitlines() if f"{ops}/" in l), "") if good else ""
    if line:
        dev = line.split()[1]
        if dev == ifname:
            ok("이 서버의 관리망 주소", f"{ops} on {dev} (VLAN {vlan})")
        else:
            # 주소는 맞는데 인터페이스 이름이 다르다 — 문서대로 만들지 않았다는 뜻이다.
            # 통신은 될 수 있으나 랩을 늘릴 때 헷갈린다.
            warn("이 서버의 관리망 주소", f"{ops} on {dev} — 문서 기준 이름은 {ifname}",
                 "dist/ops-server.md 의 netplan 을 그대로 쓰면 이름이 랩 번호와 맞는다")
    else:
        warn("이 서버의 관리망 주소", f"{ops} ({ifname}, VLAN {vlan}) 가 없다",
             f"운영 서버에 {br} 트렁크 NIC 을 붙이고 VLAN 서브인터페이스를 만들 것 (1회). "
             "명령은 dist/ops-server.md 에 그대로 들어 있다")

    # 이 서버에서 관리망으로 나가는 경로가 있는가. 없으면 Ansible 은 한 대도 못 만진다.
    good, line = run(["ip", "route", "get", gw], timeout=5)
    if not good:
        warn("경로", f"{cidr} 로 가는 경로를 확인하지 못했다", line)
    elif " dev lo " in f" {line} ":
        err("경로", f"{gw} 가 루프백으로 잡힌다", "관리망 주소가 이 서버 자신과 겹친다")
    else:
        dev = re.search(r" dev (\S+)", line)
        via = re.search(r" via (\S+)", line)
        detail = f"{cidr} → {dev.group(1) if dev else '?'}" + (f" via {via.group(1)}" if via else " (직결)")
        if via:
            warn("경로", detail,
                 f"직결이 아니다. 중간 장비가 포워딩해 줘야 한다 — "
                 f"이 서버를 {br} 트렁크에 물리고 VLAN {vlan} 서브인터페이스를 두는 편이 확실하다")
        else:
            ok("경로", detail)

    # 노드가 아직 없으면 닫혀 있는 게 정상이다. 그래서 오류로 올리지 않는다.
    nodes = [L.node_config(lab_id, n, "m10") for n in L.TOPO["nodes"]]
    live = []
    for c in nodes:
        try:
            with socket.create_connection((c["mgmt_ip"], 22), timeout=1.0):
                live.append(c["node"])
        except OSError:
            pass
    if not live:
        skip("노드 SSH", f"{len(nodes)}대 중 응답 0 — 아직 배포 전이라면 정상이다",
             "배포 뒤에도 0이면 관리망 경로 문제다")
    elif len(live) == len(nodes):
        ok("노드 SSH", f"{len(live)}/{len(nodes)}대 22번 열림")
    else:
        warn("노드 SSH", f"{len(live)}/{len(nodes)}대만 응답 ({', '.join(live)})",
             "일부 VM 이 꺼져 있거나 cloud-init 이 아직 끝나지 않았다")


# ===================================================== 5. 콘솔 운영
def check_runtime():
    section("콘솔 운영")
    var = L.ROOT / "var"
    if not var.exists():
        skip("var/", "아직 없다 — 콘솔 첫 기동 때 만들어진다")
    else:
        mode = var.stat().st_mode & 0o777
        (ok if mode == 0o700 else warn)("var/ 권한", f"{mode:04o}",
                                        "" if mode == 0o700 else "chmod 700 var")
        dbf = var / "console.db"
        if dbf.exists():
            m = dbf.stat().st_mode & 0o777
            (ok if m == 0o600 else err)("console.db 권한", f"{m:04o}",
                                        "" if m == 0o600 else
                                        "API 토큰이 들어 있다. chmod 600 var/console.db")

    port = int(os.environ.get("PORT", 8080))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            warn(f"{port} 포트", "이미 무언가 듣고 있다",
                 "콘솔이 이미 떠 있다면 정상. 아니면 PORT=... 로 바꿀 것")
    except OSError:
        ok(f"{port} 포트", "비어 있다")


# ===================================================== 출력
def report():
    print("=" * 74)
    print(" 배포 사전 점검")
    print("=" * 74)
    n = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    mark = {"ok": f"{G}✔{N}", "warn": f"{Y}!{N}", "error": f"{R}✘{N}", "skip": f"{B}·{N}"}
    for status, title, detail, hint in RESULTS:
        if status == "section":
            print(f"\n {B}{title}{N}")
            continue
        n[status] += 1
        print(f"  {mark[status]} {title}" + (f"  —  {detail}" if detail else ""))
        if hint and status in ("warn", "error"):
            print(f"      → {hint}")
    print("-" * 74)
    print(f"  통과 {n['ok']} · 경고 {n['warn']} · 오류 {n['error']} · 건너뜀 {n['skip']}")
    if n["error"]:
        print(f"\n{R}오류를 해결하기 전에는 배포하지 말 것.{N} 절차는 docs/DEPLOY.md 에 있다.")
    elif n["warn"]:
        print("\n경고는 배포를 막지 않는다. 다만 무엇을 뜻하는지는 확인하고 넘어갈 것.")
    else:
        print("\n준비됐다.  다음:  make gen LAB=1  →  make deploy LAB=1")
    return 1 if n["error"] else 0


def main():
    ap = argparse.ArgumentParser(description="배포 사전 점검")
    ap.add_argument("--lab", type=int, default=1)
    ap.add_argument("--skip-proxmox", action="store_true", help="네트워크 검사를 건너뛴다")
    a = ap.parse_args()

    check_tools()
    check_config(a.lab)
    if a.skip_proxmox:
        section("Proxmox 연결")
        skip("전체", "--skip-proxmox")
    else:
        check_proxmox()
        check_mgmt(a.lab)
    check_runtime()
    return report()


if __name__ == "__main__":
    sys.exit(main())
