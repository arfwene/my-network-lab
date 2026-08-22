#!/usr/bin/env python3
"""
배포 전 검사 — 주소 충돌 · 용량 · 공개 안전성.

  python3 tools/validate-site.py            검사만
  python3 tools/validate-site.py --publish  공개 저장소 유출 검사까지 (커밋/공개 전 필수)

exit code 0 = 통과, 1 = 오류 있음.
"""
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

ERR, WARN, OK = [], [], []


def err(m): ERR.append(m)
def warn(m): WARN.append(m)
def ok(m): OK.append(m)


def net(c):
    return ipaddress.ip_network(c)


# =============================================================================
# 1. 랩 대역끼리 겹치지 않는가
# =============================================================================
N = L.SITE["networks"]
blocks = {
    "lab_block": N["lab_block"],
    "management": N["management"],
    "public_transit": N["public_transit"],
    "public_service": N["public_service"],
    "external_net": N["external_net"],
    "office_lan": L.SITE["access"]["office_lan"],
}
items = [(k, net(v)) for k, v in blocks.items()]
for i, (k1, n1) in enumerate(items):
    for k2, n2 in items[i + 1:]:
        # transit 과 service 는 같은 /24 안에 있어도 서로 겹치지만 않으면 정상
        if n1.overlaps(n2):
            err(f"대역 충돌: {k1} ({n1}) 와 {k2} ({n2}) 가 겹친다")
if not ERR:
    ok(f"랩 대역 {len(items)}개 상호 충돌 없음")

# office_lan 은 특히 중요 — 겹치면 점프 호스트 라우팅이 깨져 접속 자체가 안 된다
office = net(blocks["office_lan"])
for k in ("lab_block", "management"):
    if office.overlaps(net(blocks[k])):
        err(f"치명적: 사무실 대역({office})과 {k}({blocks[k]})가 겹친다. "
            f"점프 호스트의 라우팅이 충돌해 랩 접속이 불가능해진다")

# =============================================================================
# 2. 사용 금지 대역과 겹치지 않는가  (M2 · C8 의 자동화)
# =============================================================================
for f in L.SITE.get("forbidden", []):
    fn = net(f["cidr"])
    sev = f.get("severity", "error")
    for k, v in blocks.items():
        if k == "office_lan":
            continue  # 사무실 대역은 우리가 정하는 게 아니다
        if net(v).overlaps(fn):
            msg = f"{k} ({v}) 가 사용 금지 대역 {f['cidr']} 와 겹친다 — {f['reason']}"
            (err if sev == "error" else warn)(msg)
if not any("사용 금지" in e for e in ERR):
    ok(f"사용 금지 대역 {len(L.SITE.get('forbidden', []))}건 검사 통과")

# =============================================================================
# 3. 용량 — 대역이 실제로 충분한가
# =============================================================================
lab = net(N["lab_block"])
try:
    for k, z in L.RULES["zones"].items():
        L._sub4(lab, z["prefix"], z["index"])
    ok(f"lab_block {lab} 안에 구역 {len(L.RULES['zones'])}개 배분 가능")
except ValueError as e:
    err(f"구역 배분 실패: {e}")

mgmt = net(N["management"])
lo, hi = L.SITE["labs"]["id_range"]
try:
    L.mgmt_net(hi)
    ok(f"management {mgmt} 로 랩 {lo}~{hi} 번 관리망 확보 가능 (/{L.RULES['management']['subnet_prefix']} × {hi})")
except ValueError:
    err(f"management {mgmt} 가 랩 {hi}개를 담기에 부족하다")

svc = net(N["public_service"])
for d in L.RULES["public"]["dnat"]:
    a = svc.network_address + d["offset"]
    if a not in svc:
        err(f"공인 서비스 블록 {svc} 에 offset {d['offset']} ({d['node']}) 가 들어가지 않는다")
transit = net(N["public_transit"])
if transit.num_addresses < 4:
    err(f"public_transit {transit} 는 최소 /30 이어야 한다")
if not ERR:
    ok(f"공인 대역 용량 확인 (transit {transit}, service {svc})")

# =============================================================================
# 4. 실무 권고
# =============================================================================
ula = N["ipv6"]["ula"]
if not re.match(r"^fd[0-9a-f]{2}:", ula):
    err(f"ULA 는 fd00::/8 범위여야 한다: {ula}")
elif re.match(r"^fd00:", ula):
    warn(f"ULA {ula} 의 Global ID 가 무작위가 아니다. "
         f"실무에서는 반드시 난수로 생성해야 조직 간 충돌을 피할 수 있다 "
         f"(교육용 가독성을 위한 의도적 선택이면 무시)")

doc_ranges = [net(c) for c in L.SITE.get("publish_guard", {}).get("documentation_ranges", [])
              if ":" not in c]
for k in ("public_transit", "public_service", "external_net"):
    v = net(blocks[k])
    if not any(v.subnet_of(d) for d in doc_ranges):
        warn(f"{k} ({v}) 가 문서 전용 대역(RFC5737) 밖이다. "
             f"실제 인터넷에서 쓰이는 주소일 수 있으니 확인할 것")

# =============================================================================
# 5. 공개 안전성 — 사내 값이 저장소로 새어 나가는가
# =============================================================================
def access_hosts():
    """접속 주소가 말이 되는가.

    office_ip 는 교육생이 랩에 들어오는 유일한 문이다(gen-ssh-config 의 ProxyJump 대상).
    사무실 대역 밖이거나 랩 대역과 겹치면 아무도 접속하지 못하는데,
    그 사실은 교육생이 처음 ssh 를 칠 때에야 드러난다. 여기서 잡는다.
    """
    A = L.SITE["access"]
    office = A.get("office_lan")
    pairs = [("access.jump_host.office_ip", (A.get("jump_host") or {}).get("office_ip"),
              "랩 운영 서버(점프 호스트)의 사무실 LAN 주소"),
             ("access.proxmox.host_ip", (A.get("proxmox") or {}).get("host_ip"),
              "Proxmox 호스트 주소")]
    try:
        onet = ipaddress.ip_network(office, strict=False) if office else None
    except ValueError:
        err(f"access.office_lan 이 대역이 아니다: {office}")
        return
    clean = True
    for key, val, what in pairs:
        if not val:
            err(f"{key} 가 비어 있다 — {what}")
            clean = False
            continue
        try:
            addr = ipaddress.ip_address(str(val))
        except ValueError:
            err(f"{key} 가 IP 주소가 아니다: {val} (호스트명·URL 이 아니라 주소를 쓸 것)")
            clean = False
            continue
        if onet and addr not in onet:
            warn(f"{key} ({val}) 가 access.office_lan ({office}) 밖이다 — {what}. "
                 f"교육생 PC 에서 여기로 닿을 수 있는지 확인할 것")
            clean = False
        for name in ("management", "lab_block"):
            block = L.SITE["networks"].get(name)
            if block and addr in ipaddress.ip_network(block):
                err(f"{key} ({val}) 가 networks.{name} ({block}) 안에 있다. "
                    f"랩이 쓸 대역이라 충돌한다")
                clean = False
    if clean:
        ok(f"접속 주소 확인 (점프 {(A.get('jump_host') or {}).get('office_ip')} · "
           f"Proxmox {(A.get('proxmox') or {}).get('host_ip')})")


def publish_guard():
    import yaml as _y

    def leaf_strings(obj, out):
        """YAML 의 '값'만 모은다. 주석·키는 보지 않는다 (파일명 등 오탐 방지)."""
        if isinstance(obj, dict):
            for v in obj.values():
                leaf_strings(v, out)
        elif isinstance(obj, list):
            for v in obj:
                leaf_strings(v, out)
        elif isinstance(obj, str):
            out.add(obj.strip())
        return out

    # 사내 전용 값의 출처는 둘이다.
    #   config/site.local.yml  사람이 적은 실제 환경 값
    #   var/runtime.yml        웹 콘솔에서 관리자가 입력한 Proxmox 접속 정보
    # 둘 다 git 제외 대상이지만, 그 '값'이 다른 파일로 새는지는 여기서 잡아야 한다.
    sources = [L.ROOT / "config/site.local.yml", L.RUNTIME]
    sensitive = set()
    for local in sources:
        if not local.exists():
            continue
        vals = leaf_strings(_y.safe_load(local.read_text(encoding="utf-8")) or {}, set())
        for v in vals:
            sensitive |= set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b", v))
            sensitive |= set(re.findall(r"\b(?:[a-z0-9-]+\.)+(?:co\.kr|kr|com|net|io|internal)\b", v))
    sensitive |= set(L.SITE.get("publish_guard", {}).get("forbidden_strings", []))

    # 공개 기본값(site.yml 의 값)은 안전하므로 제외
    safe = set()
    for v in leaf_strings(_y.safe_load((L.ROOT / "config/site.yml").read_text(encoding="utf-8")), set()):
        safe.add(v)
        safe |= set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b", v))
    sensitive -= safe
    sensitive = {s for s in sensitive if len(s) > 6}

    if not sensitive:
        ok("공개 안전성: 검사할 사내 전용 값이 없다 (site.local.yml · var/runtime.yml 미사용)")
        return

    # "공개되면 올라갈 파일" 전체를 검사한다.
    #   --cached  : 이미 추적 중
    #   --others  : 아직 추적 안 됨(신규)  ← 커밋 전이라도 잡아야 한다
    #   --exclude-standard : .gitignore 에 걸린 것은 제외
    files = []
    try:
        r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                           cwd=L.ROOT, capture_output=True, text=True, check=True)
        files = [f for f in r.stdout.splitlines() if f]
    except Exception:
        warn("git 저장소가 아니다 — .gitignore 를 반영하지 못해 전체 파일을 검사한다")
    if not files:
        files = [str(p.relative_to(L.ROOT)) for p in L.ROOT.rglob("*")
                 if p.is_file() and ".git" not in p.parts]
    ok(f"공개 대상 파일 {len(files)}개 검사")

    leaks = []
    for f in files:
        if f in ("config/site.local.yml", "config/site.local.yml.example", "var/runtime.yml"):
            continue
        p = L.ROOT / f
        try:
            body = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for s in sensitive:
            if s in body:
                leaks.append((f, s))

    if leaks:
        for f, s in sorted(set(leaks)):
            err(f"공개 위험: '{s}' 가 {f} 에 들어 있다 "
                f"(site.local.yml / var/runtime.yml 전용 값)")
    else:
        ok(f"공개 안전성: 사내 전용 값 {len(sensitive)}건이 다른 파일로 새지 않았다")

    # .gitignore 확인
    gi = (L.ROOT / ".gitignore")
    if not gi.exists() or "config/site.local.yml" not in gi.read_text(encoding="utf-8"):
        err(".gitignore 에 config/site.local.yml 이 없다 — 사내 값이 커밋될 수 있다")


# 접속 주소는 --publish 여부와 무관하게 늘 본다 — 틀리면 아무도 랩에 못 들어온다.
access_hosts()

if "--publish" in sys.argv:
    publish_guard()

# =============================================================================
print("=" * 74)
print(" site 검사 결과")
print("=" * 74)
for m in OK:
    print(f"  \033[32m✔\033[0m {m}")
for m in WARN:
    print(f"  \033[33m!\033[0m {m}")
for m in ERR:
    print(f"  \033[31m✘\033[0m {m}")
print("-" * 74)
print(f"  통과 {len(OK)} · 경고 {len(WARN)} · 오류 {len(ERR)}")
if ERR:
    print("\n오류를 해결하기 전에는 배포하지 말 것. "
          "사내 값은 config/site.local.yml 또는 웹 콘솔의 연결 설정에만 둔다.")
sys.exit(1 if ERR else 0)
