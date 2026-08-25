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
import time
import concurrent.futures as cf
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


_RUN_CACHE: dict = {}


def run_cached(argv, timeout=20, ttl=300.0):
    """버전 확인처럼 **결과가 잘 안 변하는** 실행. 잠깐 들고 있는다.

    terraform·ansible 버전을 묻는 것만으로 1초 가까이 나간다. 화면을 열 때마다
    그 값이 달라질 일은 없다.
    """
    key = tuple(argv)
    hit = _RUN_CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = run(argv, timeout)
    _RUN_CACHE[key] = (time.time(), val)
    return val


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
        err("Python", f"{v.major}.{v.minor} — 3.10 이상이 필요합니다",
            "Ubuntu 22.04 이상 / Debian 12 이상을 쓸 것")

    if (VENV / "bin/uvicorn").exists():
        ok("웹 콘솔 venv", str(VENV))
    else:
        err("웹 콘솔 venv", f"{VENV} 가 없습니다", "`./install.sh` 또는 `make console-setup` 을 실행해 주세요")

    apb = venv_bin("ansible-playbook")
    if not apb:
        err("ansible-playbook", "없습니다",
            "`make console-setup` 이 venv 안에 설치합니다. 시스템에 깔 필요는 없습니다")
    else:
        good, line = run_cached([apb, "--version"])
        where = "venv" if str(VENV) in apb else "시스템 PATH"
        if good and ver_tuple(line) >= (2, 16):
            ok("ansible-playbook", f"{line}  [{where}]")
        elif good:
            warn("ansible-playbook", f"{line}  [{where}]", "2.16 이상을 권장합니다")
        else:
            err("ansible-playbook", line)

    gal = venv_bin("ansible-galaxy")
    if not gal:
        skip("ansible.posix 컬렉션", "ansible-galaxy 가 없어 건너뜁니다")
    else:
        good, _ = run_cached([gal, "collection", "list", "ansible.posix"])
        if good:
            ok("ansible.posix 컬렉션", "설치됨 (roles/common 의 sysctl 모듈이 씁니다)")
        else:
            err("ansible.posix 컬렉션", "없습니다",
                f"{gal} collection install -r infra/ansible/requirements.yml")

    tf = shutil.which("terraform")
    if not tf:
        err("terraform", "PATH 에 없습니다",
            "`./install.sh` 가 설치합니다. 수동 설치는 docs/DEPLOY.md 참고")
    else:
        good, line = run_cached([tf, "version"])
        if good and ver_tuple(line) >= (1, 6):
            ok("terraform", f"{line}  ({tf})")
        elif good:
            err("terraform", f"{line} — 1.6 이상이 필요합니다 ({tf})")
        else:
            err("terraform", line)

    # 랩 노드는 인터넷에 못 나가지만, 이 서버는 프로바이더를 받아야 한다.
    cached = list((Path.home() / ".terraform.d/plugin-cache").rglob("terraform-provider-proxmox*"))
    envs = sorted((ROOT / "infra/terraform/envs").glob("lab*/.terraform/providers"))
    if cached or envs:
        ok("bpg/proxmox 프로바이더", "내려받은 이력이 있습니다")
    else:
        warn("bpg/proxmox 프로바이더", "아직 없습니다 — 첫 `terraform init` 때 인터넷에서 받습니다",
             "이 서버가 registry.terraform.io 로 나갈 수 있어야 합니다")


# ===================================================== 2. 설정
def check_config(lab_id_for_keys=1):
    section("설정")

    local = ROOT / "config/site.local.yml"
    if local.exists():
        ok("site.local.yml", str(local))
    else:
        warn("site.local.yml", "없습니다 — site.yml 의 공개용 기본값(문서 전용 대역)으로 돕니다",
             "cp config/site.local.yml.example config/site.local.yml 후 사내 값을 채울 것")

    # 접속 값은 세 곳에서 올 수 있고 뒤가 이긴다. 어디를 고쳐야 하는지 헷갈리기 쉽다 —
    # 콘솔에서 한 번 저장하면 runtime.yml 이 생기고, 그 뒤로는 파일을 고쳐도 덮인다.
    pxm = L.IPAM["access"]["proxmox"]
    if L.RUNTIME.exists():
        warn("접속 값 출처", f"var/runtime.yml 이 이긴다 · 노드 {pxm['node_name']} · "
                             f"{pxm['api_endpoint']}",
             "콘솔 [연결 설정] 이 저장한 값입니다. 여기를 고쳐야 반영됩니다 — "
             "config/site.local.yml 을 고쳐도 이 파일이 덮습니다. "
             "파일 쪽으로 되돌리려면 var/runtime.yml 을 지울 것")
    else:
        ok("접속 값 출처", f"config/site.local.yml · 노드 {pxm['node_name']} · "
                           f"{pxm['api_endpoint']}")

    good, _ = run_cached([sys.executable, str(ROOT / "tools/validate-site.py")], timeout=60)
    if good:
        ok("site 검사", "오류 없음 (`make check` 로 전체 결과를 볼 수 있습니다)")
    else:
        err("site 검사", "오류가 있습니다", "`make check` 를 실행해 내용을 확인해 주세요")

    raw = L.IPAM["access"].get("ssh_public_keys") or []
    good, bad = [], []
    for k in raw:
        try:
            sshkeys.normalize(k)
            good.append(k)
        except Exception:                              # noqa: BLE001
            bad.append(k)
    if not good:
        err("운영 서버 SSH 공개키", f"쓸 수 있는 키가 없습니다 (등록 {len(raw)}개)",
            "site.local.yml 의 access.ssh_public_keys 에 이 서버의 공개키를 넣어 주세요 "
            f"({Path.home()}/.ssh/id_ed25519.pub). 없으면 Ansible 이 노드에 접속하지 못하고, "
            "설정 적용이 잠금 사고 방지를 위해 중단됩니다")
    elif bad:
        warn("운영 서버 SSH 공개키", f"{len(good)}개 사용 · {len(bad)}개는 형식이 아니라 제외됩니다",
             "예시값이 남아 있는지 확인해 주세요")
    else:
        ok("운영 서버 SSH 공개키", f"{len(good)}개")

    # 교육생 키는 여기가 아니라 콘솔 DB 에 있다. 랩별로 몇 개인지만 알려준다.
    if (L.ROOT / "var/console.db").exists():
        try:
            import db                                  # noqa: PLC0415
            n = len(db.lab_keys(lab_id_for_keys))
            ok(f"랩 {lab_id_for_keys} 교육생 등록 키", f"{n}개"
               + ("" if n else " — 교육생이 콘솔 [접속 키] 에서 등록하면 늘어납니다"))
        except Exception:                              # noqa: BLE001
            pass

    priv = [p for p in ("id_ed25519", "id_rsa", "id_ecdsa")
            if (Path.home() / ".ssh" / p).exists()]
    if priv:
        ok("이 서버의 SSH 개인키", ", ".join(f"~/.ssh/{p}" for p in priv))
    else:
        warn("이 서버의 SSH 개인키", "없습니다",
             "Ansible 이 랩 노드에 붙을 때 씁니다. `ssh-keygen -t ed25519` 후 "
             "그 공개키를 site.local.yml 의 ssh_public_keys 에 넣어 주세요")


# ===================================================== 3. Proxmox
def check_proxmox(lab_id_for_pre=1, fresh=True):
    section("Proxmox 연결")
    if not (L.ROOT / "var/console.db").exists():
        warn("연결 설정", "아직 없습니다 (var/console.db 미생성)",
             "웹 콘솔을 띄우고 [관리자 → 연결 설정] 에서 주소와 API 토큰을 넣어 주세요")
        return
    try:
        import pve                                    # noqa: PLC0415
        # 화면에서 부를 때는 방금 잰 값을 다시 쓴다. Proxmox 왕복은 4초쯤 걸리는데
        # [설치] 화면을 열 때마다 그걸 새로 재면, 헤더 칩이 이미 보여 주고 있는
        # 같은 값을 위해 사람을 4초 기다리게 하는 것이다. make doctor 는 새로 잰다.
        res = pve.check() if fresh else pve.cached()
    except Exception as e:                            # noqa: BLE001
        err("연결 점검", f"{type(e).__name__}: {e}")
        return
    for c in res.get("checks", []):
        add({"ok": "ok", "warn": "warn", "error": "error"}.get(c["status"], "skip"),
            f"Proxmox · {c['title']}", c.get("detail", ""), c.get("hint", ""))

    if res.get("level") == "error":
        return

    # 권한은 연결이 되어도 따로 막힌다 — 403 은 terraform 한복판에서 터진다.
    c = pve.check_privileges().as_dict()
    add({"ok": "ok", "warn": "warn", "error": "error"}.get(c["status"], "skip"),
        f"Proxmox · {c['title']}", c.get("detail", ""), c.get("hint", ""))

    # 배포 전 검사 — 콘솔이 [랩 생성] 때 보는 것과 **같은 검사**를 여기서도 본다.
    #   VMID 충돌 · 템플릿이 정말 템플릿인지 · 관리망 브리지 존재와 vlan_aware ·
    #   호스트 대역 겹침. 파일이 아니라 Proxmox 에 물어본 결과다.
    try:
        pre = pve.preflight(lab_id_for_pre)
    except Exception as e:                            # noqa: BLE001
        warn("배포 전 검사", f"확인하지 못했다: {type(e).__name__}: {e}")
        return
    for c in pre.get("checks", []):
        add({"ok": "ok", "warn": "warn", "error": "error"}.get(c["status"], "skip"),
            f"배포 전 · {c['title']}", c.get("detail", ""), c.get("hint", ""))


# ===================================================== 4. 랩 관리망
def check_mgmt(lab_id):
    section(f"랩 {lab_id} 관리망 도달성")
    gw = L.mgmt_gateway(lab_id)
    cidr = L.mgmt_cidr(lab_id)
    br = L.mgmt_bridge_name()
    m = next(x for x in L.mgmt_labs(lab_id) if x["lab_id"] == lab_id)
    ops, vlan, ifname = m["ops_ip"], m["vlan"], m["iface"]

    # 관리망 브리지가 **실제로 있는지**는 위 "배포 전 · 관리망 브리지" 가 Proxmox 에
    # 물어서 판정한다. 여기서는 apply 를 시도한 적이 있는지만 본다 —
    # tfstate 는 실패한 apply 도 만들기 때문에 존재만으로는 아무것도 보장하지 않는다.
    if not (L.ROOT / "infra/terraform/envs/mgmt/terraform.tfstate").exists():
        warn("관리망 생성 시도", f"{br} 를 만든 기록이 없습니다",
             "아래 [관리망 브리지 만들기] 버튼 한 번이면 됩니다 (최초 1회). "
             "랩 개수는 [연결 설정] 에서 정합니다")
    else:
        skip("관리망 생성 시도", f"기록 있음 — 실제 존재 여부는 위 '배포 전' 항목을 볼 것")

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
                 "dist/ops-server.md 의 netplan 을 그대로 쓰면 이름이 랩 번호와 맞습니다")
    else:
        warn("이 서버의 관리망 주소", f"{ops} ({ifname}, VLAN {vlan}) 가 없습니다",
             "한 명령으로 끝납니다 (1회):  make mgmt-net\n"
             "  이 서버가 어느 VM 인지 찾아 트렁크 NIC 을 붙이고 VLAN 까지 설정합니다.\n"
             "  먼저 볼 것:  make mgmt-net-dry   (무엇을 할지만 보여줍니다)")

    # 이 서버에서 관리망으로 나가는 경로가 있는가. 없으면 Ansible 은 한 대도 못 만진다.
    good, line = run(["ip", "route", "get", gw], timeout=5)
    if not good:
        warn("경로", f"{cidr} 로 가는 경로를 확인하지 못했습니다", line)
    elif " dev lo " in f" {line} ":
        err("경로", f"{gw} 가 루프백으로 잡힙니다", "관리망 주소가 이 서버 자신과 겹칩니다")
    else:
        dev = re.search(r" dev (\S+)", line)
        via = re.search(r" via (\S+)", line)
        detail = f"{cidr} → {dev.group(1) if dev else '?'}" + (f" via {via.group(1)}" if via else " (직결)")
        if via:
            warn("경로", detail,
                 f"직결이 아닙니다. 중간 장비가 포워딩해 줘야 합니다 — "
                 f"`make mgmt-net` 으로 {br} 트렁크에 물리는 편이 확실합니다")
        else:
            ok("경로", detail)

    # 노드가 아직 없으면 닫혀 있는 게 정상이다. 그래서 오류로 올리지 않는다.
    nodes = [L.node_config(lab_id, n, "m10") for n in L.TOPO["nodes"]]

    # 13대를 **동시에** 두드린다. 하나씩 돌면 랩이 없을 때 13초를 통째로 기다린다 —
    # 그게 [설치] 화면이 느린 이유의 대부분이었다. 기다리는 시간은 소켓이 쓰지,
    # CPU 가 쓰는 게 아니므로 스레드로 겹쳐 두면 제일 느린 하나만큼만 걸린다.
    def alive(c):
        try:
            with socket.create_connection((c["mgmt_ip"], 22), timeout=1.0):
                return c["node"]
        except OSError:
            return None

    with cf.ThreadPoolExecutor(max_workers=min(16, len(nodes) or 1)) as ex:
        live = [n for n in ex.map(alive, nodes) if n]
    if not live:
        skip("노드 SSH", f"{len(nodes)}대 중 응답 0 — 아직 배포 전이라면 정상입니다",
             "배포 뒤에도 0이면 관리망 경로 문제입니다")
    elif len(live) == len(nodes):
        ok("노드 SSH", f"{len(live)}/{len(nodes)}대 22번 열림")
    else:
        warn("노드 SSH", f"{len(live)}/{len(nodes)}대만 응답 ({', '.join(live)})",
             "일부 VM 이 꺼져 있거나 cloud-init 이 아직 끝나지 않았습니다")


# ===================================================== 5. 콘솔 운영
def _uname(uid):
    try:
        import pwd                                     # noqa: PLC0415
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError):
        return f"uid {uid}"


def _check_schema(dbf):
    """DB 에 있어야 할 테이블이 다 있는가.

    sqlite3 명령이 깔려 있지 않은 서버가 많다. 콘솔이 쓰는 코드로 그냥 물어본다 —
    확인하려고 패키지를 더 깔게 만들지 않는다.
    """
    try:
        import db                                      # noqa: PLC0415
        if db.DB_PATH.resolve() != dbf.resolve():
            err("DB 경로", f"콘솔은 {db.DB_PATH} 를 봅니다",
                f"검사한 파일({dbf})과 다릅니다 — 둘 중 하나가 엉뚱한 곳입니다")
            return
        with db.connect() as con:
            gone = db.missing_tables(con)
    except Exception as e:                             # noqa: BLE001
        warn("DB 스키마", f"확인하지 못했다: {e}")
        return
    if gone:
        err("DB 스키마", f"없는 테이블: {', '.join(gone)}",
            "콘솔을 다시 시작하면 스스로 만듭니다. 그래도 남으면 위의 소유자·권한을 볼 것: "
            f"sudo systemctl restart my-network-lab")
    else:
        ok("DB 스키마", f"테이블 {len(db.EXPECTED_TABLES)}개 모두 있습니다")


def check_runtime():
    section("콘솔 운영")
    var = L.ROOT / "var"
    if not var.exists():
        skip("var/", "아직 없습니다 — 콘솔 첫 기동 때 만들어집니다")
    else:
        mode = var.stat().st_mode & 0o777
        (ok if mode == 0o700 else warn)("var/ 권한", f"{mode:04o}",
                                        "" if mode == 0o700 else "chmod 700 var")
        dbf = var / "console.db"
        if dbf.exists():
            st = dbf.stat()
            m = st.st_mode & 0o777
            (ok if m == 0o600 else err)("console.db 권한", f"{m:04o}",
                                        "" if m == 0o600 else
                                        "API 토큰이 들어 있습니다. chmod 600 var/console.db")
            # 소유자가 다르면 콘솔은 이 파일을 고칠 수 없다.
            # 다른 계정(대개 sudo)이 먼저 만들어 두면 콘솔은 뜨긴 뜨는데
            # 스키마가 반쪽인 채로 돈다. 겉으로는 멀쩡해 보인다.
            if st.st_uid != os.geteuid():
                err("console.db 소유자", _uname(st.st_uid),
                    f"콘솔을 돌리는 계정({_uname(os.geteuid())})이 아닙니다. "
                    f"sudo chown {_uname(os.geteuid())} {dbf}")
            else:
                ok("console.db 소유자", _uname(st.st_uid))
            _check_schema(dbf)

    # CLI(make deploy/mgmt)도 토큰이 필요하다. 콘솔에만 있으면 CLI 가 멈춘다.
    tok = bool(os.environ.get("PROXMOX_VE_API_TOKEN"))
    if not tok and (L.ROOT / "var/console.db").exists():
        try:
            import pve                                 # noqa: PLC0415
            tok = bool(pve.config().get("token_secret"))
        except Exception:                              # noqa: BLE001
            pass
    if tok:
        ok("CLI 자격 증명", "make deploy/mgmt 가 토큰을 찾을 수 있습니다")
    else:
        warn("CLI 자격 증명", "API 토큰을 찾지 못했습니다",
             "콘솔 [관리자 → 연결 설정] 에 넣거나, 이 셸에서만 "
             "export PROXMOX_VE_API_TOKEN='terraform@pve!lab=...' "
             "— 없으면 terraform 이 credentials 오류로 멈춥니다")

    check_students()

    port = int(os.environ.get("PORT", 8080))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            warn(f"{port} 포트", "이미 무언가 듣고 있습니다",
                 "콘솔이 이미 떠 있다면 정상. 아니면 PORT=... 로 바꿀 것")
    except OSError:
        ok(f"{port} 포트", "비어 있습니다")


def check_students():
    """등록된 교육생이 실제로 들어올 수 있는 상태인가.

    콘솔에 계정을 만드는 것과 **운영 서버에 점프 계정을 만드는 것은 다른 일**이다.
    둘째를 빠뜨리면 교육생이 `ssh pc1` 을 쳤을 때 있지도 않은 비밀번호를 묻는다.
    그때서야 알게 되면 이미 수업 중이다. 여기서 먼저 잡는다.
    """
    if not (L.ROOT / "var/console.db").exists():
        return
    try:
        import db                                       # noqa: PLC0415
        with db.connect() as con:
            rows = con.execute(
                "SELECT username, ssh_key FROM users "
                " WHERE role='user' AND disabled=0 ORDER BY username").fetchall()
    except Exception as e:                              # noqa: BLE001
        warn("교육생 계정", f"확인하지 못했다: {e}")
        return
    if not rows:
        skip("교육생 계정", "아직 등록된 교육생이 없습니다")
        return

    import pwd                                          # noqa: PLC0415
    no_jump, no_key = [], []
    for r in rows:
        try:
            pwd.getpwnam(r["username"])
        except KeyError:
            no_jump.append(r["username"])
        if not r["ssh_key"]:
            no_key.append(r["username"])

    if no_jump:
        warn("교육생 점프 계정", f"{len(no_jump)}/{len(rows)} 명이 없다: "
             + ", ".join(no_jump[:6]) + ("…" if len(no_jump) > 6 else ""),
             "이 사람들은 ssh 로 랩에 못 들어갑니다 (비밀번호를 묻고 끝납니다).\n"
             + ("콘솔 [관리자 → 설치] 의 [점프 계정 적용] 버튼을 누를 것"
                if Path("/usr/local/sbin/lab-access-apply").exists() else
                "콘솔 [관리자 → 설치] 에서 '교육생 접속 파일 만들기' 를 누른 뒤\n"
                "  sudo ./dist/jump-access.sh\n"
                "이 일을 버튼으로 바꾸려면 한 번만:  ./install.sh --jump-apply --no-apt"))
    else:
        ok("교육생 점프 계정", f"{len(rows)}명 모두 있습니다")

    if no_key:
        # 키는 본인이 등록하는 것이라 관리자 잘못이 아니다 — 그래서 오류가 아니다.
        skip("교육생 SSH 키", f"{len(no_key)}명 미등록: "
             + ", ".join(no_key[:6]) + ("…" if len(no_key) > 6 else "")
             + " — 본인이 콘솔 [접속 키] 에서 등록합니다")
    else:
        ok("교육생 SSH 키", f"{len(rows)}명 모두 등록")

    # 계정이 있는 것과 **지금 키가 거기 들어 있는 것**은 다르다.
    # 키를 바꾼 뒤 [지금 랩에 반영] 만 누르면 랩 노드에는 들어가지만 점프 호스트는
    # 옛 키 그대로다. 그러면 ssh 가 첫 홉에서 막히는데 아무도 이유를 모른다.
    stale = [u["username"] for u in db.jump_stale_users()]
    if stale:
        warn("점프 계정 키 반영", f"{len(stale)}명이 밀려 있다: "
             + ", ".join(stale[:6]) + ("…" if len(stale) > 6 else ""),
             "이 사람들의 키가 운영 서버 점프 계정에 아직 안 들어갔습니다 — "
             "ssh 가 **첫 홉에서** 막힙니다.\n"
             "  콘솔 [관리자 → 설치] 의 [점프 계정 적용] 을 누를 것 "
             "(키를 바꿀 때마다 필요합니다)")
    elif db.jump_applied_at():
        ok("점프 계정 키 반영", f"마지막 적용 {db.jump_applied_at()} UTC — 밀린 사람 없습니다")


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
            # 안내가 여러 줄일 수 있다 (권한 검사처럼 명령을 그대로 주는 경우).
            # 이어지는 줄도 같은 자리에서 시작해야 읽힌다.
            first, *rest = hint.splitlines()
            print(f"      → {first}")
            for line in rest:
                print(f"        {line}")
    print("-" * 74)
    print(f"  통과 {n['ok']} · 경고 {n['warn']} · 오류 {n['error']} · 건너뜀 {n['skip']}")
    if n["error"]:
        print(f"\n{R}오류를 해결하기 전에는 배포하지 마세요.{N}"
              "\n같은 목록을 웹 콘솔 [관리자 → 설치] 에서도 볼 수 있고, 고칠 수 있는 것은 거기서 버튼입니다."
              "\n배경 설명은 docs/DEPLOY.md.")
    elif n["warn"]:
        print("\n경고는 배포를 막지 않습니다. 다만 무엇을 뜻하는지는 확인하고 넘어갈 것.")
    else:
        print("\n준비됐습니다.  웹 콘솔에서 [랩 생성] 을 누르면 됩니다."
              "\n              (터미널로 하려면:  make deploy LAB=1  →  make config LAB=1 STAGE=m1)")
    return 1 if n["error"] else 0


def collect(lab_id=1, skip_proxmox=False, fresh=True):
    """검사를 돌리고 결과 목록을 돌려준다 — 웹 콘솔의 [설치] 화면이 쓴다.

    CLI 와 화면이 **같은 검사**를 봐야 한다. 화면용으로 따로 만들면
    "make doctor 는 초록인데 화면은 빨강" 같은 상황이 생기고,
    그때 어느 쪽을 믿어야 하는지 아무도 모른다.
    """
    RESULTS.clear()
    check_tools()
    check_config(lab_id)
    if skip_proxmox:
        section("Proxmox 연결")
        skip("전체", "건너뜀")
    else:
        check_proxmox(lab_id, fresh=fresh)
        check_mgmt(lab_id)
    check_runtime()
    return [{"status": st, "title": t, "detail": d, "hint": h}
            for st, t, d, h in RESULTS]


def main():
    ap = argparse.ArgumentParser(description="배포 사전 점검")
    ap.add_argument("--lab", type=int, default=1)
    ap.add_argument("--skip-proxmox", action="store_true", help="네트워크 검사를 건너뜁니다")
    a = ap.parse_args()

    check_tools()
    check_config(a.lab)
    if a.skip_proxmox:
        section("Proxmox 연결")
        skip("전체", "--skip-proxmox")
    else:
        check_proxmox(a.lab)
        check_mgmt(a.lab)
    check_runtime()
    return report()


if __name__ == "__main__":
    sys.exit(main())
