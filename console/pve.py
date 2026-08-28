"""
Proxmox 연결 설정 · 상태 점검.

이 랩은 두 가지로 돌 수 있다.
  · Proxmox 호스트 위에서 직접        → 기본값 localhost
  · 다른 서버(사무실 서버·노트북)에서  → 원격/사내망의 Proxmox 에 붙는다

어느 쪽이든 "어디에 붙을 것인가"는 관리자가 웹에서 한 번 입력하고,
그 값 하나를 Terraform · Ansible · 문서 생성기가 함께 본다.

저장 위치
  var/console.db  settings 테이블   ← 원본. API 토큰까지 여기에만 둔다
  var/runtime.yml                   ← 파생. 토큰은 빼고 쓴다. CLI 도구가 읽는다

토큰을 파일로 내보내지 않는 이유: gen-tfvars 가 만드는 tfvars 는 생성물이라
언제든 다시 만들어지고 여러 곳으로 복사된다. 비밀은 실행 시 환경변수로만 넘긴다.
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L      # noqa: E402
import db                  # noqa: E402

PREFIX = "pve."
TIMEOUT = 4.0                  # 한 번의 API 호출 상한
CACHE_TTL = 30                 # 헤더 아이콘이 매번 Proxmox 를 두드리지 않게 한다

_cache = {"result": None, "at": 0.0}


# ============================================================ 설정 값
def _site_defaults():
    p = dict((L.SITE.get("access") or {}).get("proxmox") or {})
    host, port = _split_endpoint(p.get("api_endpoint") or "")
    return {
        "host": host or p.get("host_ip") or "127.0.0.1",
        "port": port or 8006,
        "node": p.get("node_name") or "pve",
        "datastore": p.get("datastore") or "local-lvm",
        "insecure_tls": bool(p.get("insecure_tls", True)),
        "token_id": "",
        "token_secret": "",
        # 이 서버가 준비할 랩 개수. 관리망 VLAN 개수와 랩 목록이 여기서 나온다.
        "lab_count": int((L.SITE.get("labs") or {}).get("default_count") or 1),
    }


def _split_endpoint(url):
    if not url:
        return "", 0
    try:
        u = urlsplit(url if "://" in url else f"https://{url}")
        return u.hostname or "", u.port or 8006
    except ValueError:
        return "", 0


def _env_token():
    """셸에 export 해 둔 토큰. `사용자@영역!이름=비밀값` 형식이다.

    이 환경변수는 원래 **내보내는** 경로였다 (Terraform 에게 넘기는 값).
    그런데 콘솔을 띄우기 전에 CLI 만으로 배포하는 경우가 있고, 그때 관리자는
    당연히 export 로 해결된다고 본다. 실제로 Terraform 은 되는데 상태 점검만
    "토큰 없음" 이라고 하면 원인을 찾을 수 없다. 그래서 읽는 경로로도 인정한다.
    """
    raw = (os.environ.get("PROXMOX_VE_API_TOKEN") or "").strip()
    if "=" not in raw:
        return "", ""
    tid, secret = raw.split("=", 1)
    tid, secret = tid.strip(), secret.strip()
    # 형식이 아니면 조용히 무시한다 — 엉뚱한 값으로 401 을 맞는 것보다 낫다.
    if "@" not in tid or "!" not in tid.split("@", 1)[1] or not secret:
        return "", ""
    return tid, secret


def config():
    """Proxmox 접속 값.

    우선순위: DB(콘솔에서 저장) → 환경변수 → site.yml 기본값.
    비밀값은 어느 쪽이든 파일로 나가지 않는다.
    """
    d = _site_defaults()
    out = {}
    for k, v in d.items():
        raw = db.get_setting(PREFIX + k)
        if raw is None:
            out[k] = v
        elif isinstance(v, bool):
            out[k] = raw == "1"
        elif isinstance(v, int):
            out[k] = int(raw or v)
        else:
            out[k] = raw
    if not (out["token_id"] and out["token_secret"]):
        tid, secret = _env_token()
        if tid:
            out["token_id"], out["token_secret"] = tid, secret
            out["token_from_env"] = True
    return out


def endpoint(cfg=None):
    c = cfg or config()
    return f"https://{c['host']}:{c['port']}/"


def public(cfg=None):
    """화면·API 로 내보낼 형태. 토큰 비밀값은 있는지 여부만 알려준다."""
    c = dict(cfg or config())
    c["token_secret_set"] = bool(c.pop("token_secret", ""))
    c["endpoint"] = endpoint(c)
    c["confirmed_at"] = db.get_setting(PREFIX + "confirmed_at") or ""
    c["confirmed_by"] = db.get_setting(PREFIX + "confirmed_by") or ""
    c["is_local"] = c["host"] in ("127.0.0.1", "localhost", "::1")
    c["token_from_env"] = bool(c.pop("token_from_env", False))
    return c


def validate(v):
    """입력값 검사. 위반 목록을 돌려준다(비어 있으면 통과)."""
    errs = []
    host = (v.get("host") or "").strip()
    if not host:
        errs.append("Proxmox 주소를 입력할 것")
    elif any(ch in host for ch in " /\\@?#"):
        errs.append("주소에는 IP 또는 호스트명만 쓴다 (https:// · 포트 · 경로 제외)")
    try:
        port = int(v.get("port") or 8006)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("포트는 1~65535 사이의 숫자여야 한다")
    if not (v.get("node") or "").strip():
        errs.append("노드 이름을 입력할 것 (Proxmox 에서 hostname -s)")
    if not (v.get("datastore") or "").strip():
        errs.append("데이터스토어를 입력할 것 (예: local-lvm)")
    tid = (v.get("token_id") or "").strip()
    if tid and not ("@" in tid and "!" in tid.split("@", 1)[1]):
        errs.append("토큰 ID 형식은 사용자@영역!토큰이름 이다 (예: terraform@pve!lab)")
    if "lab_count" in v:
        lo, hi = L.SITE["labs"]["id_range"]
        try:
            n = int(v.get("lab_count"))
        except (TypeError, ValueError):
            n = -1
        if not lo <= n <= hi:
            errs.append(f"랩 개수는 {lo}~{hi} 사이의 숫자여야 한다 "
                        f"(주소 설계가 그 범위까지만 겹치지 않게 잡혀 있다)")
    return errs


def save(values, username):
    """검증 → DB 저장 → var/runtime.yml 내보내기 → labdesign 다시 읽기."""
    errs = validate(values)
    if errs:
        return errs
    cur = config()
    keep_secret = cur["token_secret"]
    with db.connect() as con:
        for k in ("host", "node", "datastore", "token_id"):
            db.set_setting(PREFIX + k, str(values.get(k, "")).strip(), con=con)
        db.set_setting(PREFIX + "port", str(int(values.get("port") or 8006)), con=con)
        if "lab_count" in values:
            db.set_setting(PREFIX + "lab_count", str(int(values["lab_count"])), con=con)
        db.set_setting(PREFIX + "insecure_tls", "1" if values.get("insecure_tls") else "0", con=con)
        # 비워서 제출하면 기존 토큰을 유지한다 (화면에 다시 뿌리지 않으므로)
        secret = values.get("token_secret")
        if secret is not None and secret != "":
            db.set_setting(PREFIX + "token_secret", secret.strip(), con=con)
        elif values.get("clear_token"):
            db.set_setting(PREFIX + "token_secret", "", con=con)
        else:
            db.set_setting(PREFIX + "token_secret", keep_secret, con=con)
        db.set_setting(PREFIX + "updated_at", _now(), con=con)
        db.set_setting(PREFIX + "updated_by", username or "", con=con)
    export()
    _cache.update(result=None, at=0.0)
    return []


def export():
    """DB 값을 var/runtime.yml 로 내보낸다 (CLI 도구·Terraform 이 읽는 경로)."""
    c = config()
    body = (
        "# 자동 생성 — 웹 콘솔 [관리자 → 연결 설정] 에서 저장한 값이다.\n"
        "# 손으로 고치지 말 것. 다음 저장 때 덮어쓴다.\n"
        "# config/site.yml → config/site.local.yml → 이 파일 순으로 병합된다.\n"
        "# API 토큰은 여기 쓰지 않는다 (var/console.db 에 두고 실행 시 환경변수로 넘긴다).\n"
        "access:\n"
        "  proxmox:\n"
        f"    host_ip:      {c['host']}\n"
        f"    api_endpoint: \"https://{c['host']}:{c['port']}/\"\n"
        f"    node_name:    {c['node']}\n"
        f"    datastore:    {c['datastore']}\n"
        f"    insecure_tls: {'true' if c['insecure_tls'] else 'false'}\n"
        "labs:\n"
        f"  default_count: {int(c['lab_count'])}\n"
    )
    L.RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    L.RUNTIME.write_text(body, encoding="utf-8")
    try:
        L.RUNTIME.chmod(0o600)
    except OSError:
        pass
    L.reload()               # 떠 있는 프로세스가 옛 주소를 계속 쓰지 않도록
    return L.RUNTIME


def sync():
    """기동 시 호출. 관리자가 저장해 둔 값이 있으면 var/runtime.yml 을 다시 만든다.

    저장한 적이 없으면 아무것도 쓰지 않는다 — site.local.yml 을 그대로 두기 위해서다.
    (한 번이라도 내보내면 그 뒤 site.local.yml 을 고쳐도 덮여 버린다)
    """
    if db.get_setting(PREFIX + "host") is None:
        return None
    return export()


def env():
    """작업 실행 시 넘길 환경변수. 비밀은 전부 여기로만 흐른다 (파일로 안 나간다)."""
    c = config()
    e = {"PROXMOX_VE_ENDPOINT": endpoint(c),
         "PROXMOX_VE_INSECURE": "true" if c["insecure_tls"] else "false"}
    if c["token_id"] and c["token_secret"]:
        e["PROXMOX_VE_API_TOKEN"] = f"{c['token_id']}={c['token_secret']}"
    # 랩 노드 콘솔 비밀번호. cloud-init 이 VM 을 만들 때 넣는다.
    # 없으면 만들어 둔다 — 없는 채로 배포되면 콘솔로 들어갈 방법이 사라진다.
    e["TF_VAR_lab_password"] = db.lab_console_password()
    return e


# ============================================================ 확인(컨펌)
def confirmed():
    return bool(db.get_setting(PREFIX + "confirmed_at"))


def mark_confirmed(username, forced=False):
    with db.connect() as con:
        db.set_setting(PREFIX + "confirmed_at", _now(), con=con)
        db.set_setting(PREFIX + "confirmed_by", username or "", con=con)
        db.set_setting(PREFIX + "confirmed_forced", "1" if forced else "0", con=con)


def _now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================ 상태 점검
class Check:
    """점검 한 항목.

    fix 는 '이 문제는 콘솔이 스스로 고칠 수 있다' 는 표시다.
    {"action": 작업이름, "label": 버튼글씨, "confirm": 누르기 전 물어볼 말}
    화면은 이걸 보고 버튼을 만든다 — 관리자가 무엇을 실행할지 문서에서
    찾아내지 않아도 되게 하려는 것이다.
    """

    def __init__(self, cid, title):
        self.id, self.title = cid, title
        self.status, self.detail, self.hint = "skip", "", ""
        self.fix = None

    def set(self, status, detail="", hint="", fix=None):
        self.status, self.detail, self.hint = status, detail, hint
        self.fix = fix
        return self

    def as_dict(self):
        d = {"id": self.id, "title": self.title, "status": self.status,
             "detail": self.detail, "hint": self.hint}
        if self.fix:
            d["fix"] = self.fix
        return d


def _fix_mgmt(labs):
    return {"action": "setup-mgmt", "label": "지금 만들기",
            "confirm": f"Proxmox 에 관리망 브리지 {L.mgmt_bridge_name()} 를 만든다 "
                       f"(랩 {labs}개 · VLAN {L.mgmt_vlan(1)}~{L.mgmt_vlan(labs)}).\n"
                       f"전 랩 공용이라 최초 1회면 된다. 기존 랩 VM 은 건드리지 않는다.\n\n"
                       f"진행할까?"}


def _ctx(cfg):
    if cfg["insecure_tls"]:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    return ssl.create_default_context()


def _api(cfg, path, envelope=False):
    """GET /api2/json/... → 파싱된 data. 실패는 예외로 올린다."""
    req = urllib.request.Request(endpoint(cfg).rstrip("/") + path,
                                 headers={"User-Agent": "my-network-lab-console"})
    if cfg["token_id"] and cfg["token_secret"]:
        req.add_header("Authorization",
                       f"PVEAPIToken={cfg['token_id']}={cfg['token_secret']}")
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx(cfg)) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
    return body if envelope else body.get("data")


def check(cfg=None):
    """Proxmox 까지 실제로 닿는지 단계별로 확인한다.

    한 번에 '안 된다'고만 하면 원인을 찾을 수 없다. 어디까지 갔는지를 남긴다:
    TCP → TLS → 인증 → 노드 → 데이터스토어 → 템플릿.
    """
    cfg = cfg or config()
    t0 = time.time()
    cs = []

    c_cfg = Check("config", "설정 값")
    cs.append(c_cfg)
    errs = validate(cfg)
    missing_token = not (cfg["token_id"] and cfg["token_secret"])
    if errs:
        c_cfg.set("error", " / ".join(errs), "관리자 → 연결 설정 에서 고칠 것")
        return _wrap(cfg, cs, t0)
    if missing_token:
        c_cfg.set("error", f"{endpoint(cfg)} · 노드 {cfg['node']} · API 토큰 없음",
                  "Proxmox 에서 API 토큰을 만들어 [관리자 → 연결 설정] 에 넣을 것 "
                  "(./infra/proxmox-setup.sh 가 만들어 준다). "
                  "이 셸에서만 쓰려면 export PROXMOX_VE_API_TOKEN='사용자@pve!이름=비밀값' "
                  "— 형식이 어긋나면 무시되니 작은따옴표로 감싸고 = 앞뒤를 확인할 것")
    else:
        src = " · 토큰: 환경변수" if cfg.get("token_from_env") else ""
        c_cfg.set("ok", f"{endpoint(cfg)} · 노드 {cfg['node']} · 스토리지 {cfg['datastore']}{src}")

    # --- TCP ---------------------------------------------------------
    c_tcp = Check("tcp", "TCP 연결")
    cs.append(c_tcp)
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=TIMEOUT):
            pass
        c_tcp.set("ok", f"{cfg['host']}:{cfg['port']} 열림")
    except socket.gaierror as e:
        c_tcp.set("error", f"이름을 풀 수 없다: {e}",
                  "호스트명 대신 IP 를 넣거나 DNS 를 확인할 것")
        return _wrap(cfg, cs, t0)
    except (socket.timeout, TimeoutError):
        c_tcp.set("error", f"{cfg['host']}:{cfg['port']} 응답 없음 (제한시간 {TIMEOUT:g}초)",
                  "방화벽 또는 경로 문제. 콘솔 서버에서 "
                  f"`nc -vz {cfg['host']} {cfg['port']}` 로 확인할 것")
        return _wrap(cfg, cs, t0)
    except OSError as e:
        c_tcp.set("error", f"연결 거부: {e}",
                  "Proxmox 가 떠 있는지, 포트가 8006 이 맞는지 확인할 것")
        return _wrap(cfg, cs, t0)

    # --- TLS ---------------------------------------------------------
    c_tls = Check("tls", "TLS 인증서")
    cs.append(c_tls)
    try:
        strict = ssl.create_default_context()
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=TIMEOUT) as s:
            with strict.wrap_socket(s, server_hostname=cfg["host"]):
                pass
        c_tls.set("ok", "정상 인증서")
    except ssl.SSLCertVerificationError as e:
        if cfg["insecure_tls"]:
            # Proxmox 기본 설치가 이 상태다. 여기서 노란불을 켜면 늘 켜져 있게 되고,
            # 그러면 진짜 경고를 아무도 보지 않는다.
            c_tls.set("ok", f"자체 서명 인증서 — 검증을 건너뛴다 ({e.verify_message or e.reason})",
                      "운영 환경이라면 정식 인증서를 권장한다")
        else:
            c_tls.set("error", f"인증서 검증 실패: {e.verify_message or e.reason}",
                      "자체 서명 인증서라면 [인증서 검증 건너뛰기] 를 켤 것")
            return _wrap(cfg, cs, t0)
    except Exception as e:                       # noqa: BLE001
        c_tls.set("warn", f"확인하지 못했다: {type(e).__name__}: {e}")

    if missing_token:
        return _wrap(cfg, cs, t0)

    # --- 인증 --------------------------------------------------------
    c_auth = Check("auth", "API 인증")
    cs.append(c_auth)
    try:
        v = _api(cfg, "/api2/json/version") or {}
        c_auth.set("ok", f"Proxmox VE {v.get('version', '?')} (API {v.get('release', '?')})")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            c_auth.set("error", f"토큰이 거부됐다 (HTTP {e.code})",
                       "토큰 ID·비밀값을 다시 확인하고, 해당 토큰에 권한이 부여됐는지 "
                       "(Datacenter → Permissions) 볼 것. 'Privilege Separation' 을 켰다면 "
                       "토큰 자체에도 역할을 줘야 한다")
        else:
            c_auth.set("error", f"HTTP {e.code} {e.reason}")
        return _wrap(cfg, cs, t0)
    except Exception as e:                       # noqa: BLE001
        c_auth.set("error", f"{type(e).__name__}: {e}")
        return _wrap(cfg, cs, t0)

    # --- 노드 --------------------------------------------------------
    c_node = Check("node", "노드")
    cs.append(c_node)
    try:
        nodes = _api(cfg, "/api2/json/nodes") or []
        names = [n.get("node") for n in nodes]
        me = next((n for n in nodes if n.get("node") == cfg["node"]), None)
        if not me:
            only = names[0] if len(names) == 1 else None
            where = (f"var/runtime.yml (콘솔 [연결 설정] 이 저장한 값 — 이게 "
                     f"site.local.yml 을 덮는다)" if L.RUNTIME.exists()
                     else "config/site.local.yml 의 access.proxmox.node_name")
            fix = (f"'{only}' 로 고칠 것. " if only else "위 목록 중 하나로 고칠 것. ")
            c_node.set("error",
                       f"'{cfg['node']}' 노드가 없다. 이 Proxmox 의 노드: "
                       f"{', '.join(names) or '없음'}",
                       fix + f"지금 이 값을 정하는 곳: {where}. "
                       + ("콘솔 [관리자 → 연결 설정] 에서 바꾸는 것이 확실하다 "
                          "(파일을 고쳐도 runtime.yml 이 덮는다)."
                          if L.RUNTIME.exists() else
                          "고친 뒤 `make gen` 으로 생성물을 다시 만들 것.")
                       + " 노드 이름은 Proxmox 호스트에서 `hostname -s` 로 확인한다")
        elif me.get("status") != "online":
            c_node.set("error", f"'{cfg['node']}' 상태: {me.get('status')}")
        else:
            free = me.get("maxmem", 0) - me.get("mem", 0)
            c_node.set("ok", f"{cfg['node']} online · 여유 메모리 {free / 1024**3:.1f} GiB")
            if free < 8 * 1024**3:
                c_node.set("warn", c_node.detail,
                           "랩 하나에 약 9GiB 가 필요하다. 다른 VM 을 정리하거나 랩 수를 줄일 것")
    except Exception as e:                       # noqa: BLE001
        c_node.set("error", f"{type(e).__name__}: {e}")

    # --- 데이터스토어 -------------------------------------------------
    #  노드 이름이 틀렸다면 아래 검사는 전부 같은 이유로 실패한다. 원인을 하나로 보여준다.
    c_ds = Check("storage", "데이터스토어")
    cs.append(c_ds)
    if c_node.status == "error":
        c_ds.set("skip", "노드를 확인하지 못해 건너뛴다")
        cs.append(Check("template", f"템플릿 VM {L.SITE['labs']['template_vmid']}")
                  .set("skip", "노드를 확인하지 못해 건너뛴다"))
        return _wrap(cfg, cs, t0)
    try:
        st = _api(cfg, f"/api2/json/nodes/{cfg['node']}/storage") or []
        mine = next((s for s in st if s.get("storage") == cfg["datastore"]), None)
        if not mine:
            c_ds.set("error",
                     f"'{cfg['datastore']}' 가 없다. 사용 가능: {', '.join(s.get('storage', '') for s in st)}",
                     "연결 설정의 데이터스토어를 고칠 것")
        elif not mine.get("active"):
            c_ds.set("error", f"'{cfg['datastore']}' 가 비활성 상태다")
        else:
            avail = mine.get("avail", 0)
            c_ds.set("ok", f"{cfg['datastore']} · 여유 {avail / 1024**3:.0f} GiB")
            if avail < 20 * 1024**3:
                c_ds.set("warn", c_ds.detail, "linked clone 이라도 여유 공간이 부족하다")
    except Exception as e:                       # noqa: BLE001
        c_ds.set("error", f"{type(e).__name__}: {e}")

    # --- 템플릿 ------------------------------------------------------
    tid = L.SITE["labs"]["template_vmid"]
    c_tpl = Check("template", f"템플릿 VM {tid}")
    cs.append(c_tpl)
    try:
        _api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu/{tid}/status/current")
        c_tpl.set("ok", f"VMID {tid} 있음")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 500):
            c_tpl.set("warn", f"VMID {tid} 를 찾을 수 없다",
                      "랩을 만들기 전에 Proxmox 호스트에서 골든 템플릿을 만들 것: "
                      "./infra/template/build-golden-template.sh "
                      f"--storage {cfg['datastore']}  (연결 자체에는 문제가 없다)")
        else:
            c_tpl.set("warn", f"HTTP {e.code} {e.reason}")
    except Exception as e:                       # noqa: BLE001
        c_tpl.set("warn", f"{type(e).__name__}: {e}")

    # --- 콘솔 서버 자신이 랩 관리망에 닿는가 ------------------------------
    #  Terraform 은 Proxmox API 로 VM 을 만들지만, Ansible 은 각 랩 관리망으로 직접 붙는다.
    #  콘솔을 관리망 밖(사무실 노트북 등)에서 돌리면 [랩 생성]은 되는데
    #  [이 모듈 적용]부터 전부 실패한다. 그 사실을 미리 알려 준다.
    cs.append(_mgmt_reach())

    return _wrap(cfg, cs, t0)


def _local_ipv4s():
    """이 서버가 가진 IPv4 주소들. 못 읽으면 None (검사 자체를 건너뛴다)."""
    import subprocess
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 4 and f[2] == "inet":
            out.append((f[1], f[3]))          # (인터페이스, 주소/prefix)
    return out


def _mgmt_reach():
    import ipaddress
    c = Check("mgmt", "콘솔 서버 → 랩 관리망")
    addrs = _local_ipv4s()
    if addrs is None:
        return c.set("skip", "이 서버의 주소를 읽지 못해 건너뛴다")
    supernet = ipaddress.ip_network(L.SITE["networks"]["management"])
    hits = [(i, a) for i, a in addrs
            if ipaddress.ip_interface(a).network.subnet_of(supernet)
            or ipaddress.ip_interface(a).ip in supernet]
    if hits:
        return c.set("ok", "  ".join(f"{i} {a}" for i, a in hits[:3]))
    return c.set("warn", f"이 서버에 {supernet} 대역 인터페이스가 없다",
                 "랩 생성·삭제(Terraform)는 되지만 모듈 적용·연결 확인·검사(Ansible)는 "
                 "랩 노드에 붙지 못한다. 콘솔은 각 랩 관리망에 발을 걸친 점프 호스트에서 "
                 "돌릴 것 (dist/access.md 8.2)")


def _wrap(cfg, checks, t0):
    ds = [c.as_dict() for c in checks]
    level = "error" if any(c["status"] == "error" for c in ds) else \
            "warn" if any(c["status"] == "warn" for c in ds) else "ok"
    bad = next((c for c in ds if c["status"] == "error"), None) or \
          next((c for c in ds if c["status"] == "warn"), None)
    res = {
        "ok": level != "error",
        "level": level,
        "endpoint": endpoint(cfg),
        "node": cfg["node"],
        "checked_at": _now(),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "checks": ds,
        "confirmed": confirmed(),
        "summary": ("Proxmox 연결 정상" if level == "ok" else
                    f"{bad['title']} — {(bad['detail'] or '').splitlines()[0][:70]}"
                    if bad else "확인 필요"),
    }
    _cache.update(result=res, at=time.time())
    return res


def cached(force=False, ttl=CACHE_TTL):
    if not force and _cache["result"] and (time.time() - _cache["at"]) < ttl:
        return _cache["result"]
    return check()


def lab_vms(lab_id, cfg=None):
    """이 랩의 VM 이 Proxmox 에 몇 대 있는가. (있는 수, 전체 수)

    콘솔 DB 나 tfstate 가 아니라 **Proxmox 에 직접 묻는다.** 진실은 거기 있다 —
    누가 웹 UI 에서 지웠을 수도 있고, tfstate 가 실패한 배포 뒤에 어긋나 있을 수도 있다.
    물어보지 못하면 (None, 전체) 를 돌려준다. '0대' 와 '모른다' 는 다르다.
    """
    want = set(range(*(lambda a, b: (a, b + 1))(*L.vmid_range(lab_id))))
    try:
        cfg = cfg or config()
        rows = _api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu") or []
        have = {int(r["vmid"]) for r in rows if str(r.get("vmid", "")).isdigit()}
        return len(want & have), len(want)
    except Exception:                                       # noqa: BLE001
        return None, len(want)


def last():
    """점검하지 않고 마지막 결과만. (헤더 아이콘 첫 렌더용)"""
    return _cache["result"]


def gate(force=False, lab_id=None):
    """랩 작업을 실행해도 되는가. (허용 여부, 사유, 상태) 를 돌려준다.

    lab_id 를 주면 그 랩의 **배포 전 충돌 검사**까지 한다 (VMID·브리지·대역·템플릿).
    같은 Proxmox 에 회사 운영 VM 이 함께 있다는 전제에서, 남의 자원을 건드리기 전에 멈춘다.
    """
    if not confirmed():
        return False, ("관리자가 Proxmox 연결 정보를 아직 확인하지 않았다. "
                       "관리자 계정으로 [연결 설정] 을 확인할 것"), last()
    res = cached(force=force)
    if not res["ok"]:
        bad = next((c for c in res["checks"] if c["status"] == "error"), None)
        why = f"{bad['title']}: {bad['detail']}" if bad else res["summary"]
        return False, f"Proxmox 에 연결할 수 없어 실행하지 않았다 — {why}", res
    if lab_id is not None:
        pre = preflight(lab_id)
        if not pre["ok"]:
            bad = next((c for c in pre["checks"] if c["status"] == "error"), None)
            why = f"{bad['title']}: {bad['detail']}" if bad else pre["summary"]
            return False, f"같은 Proxmox 의 다른 자원과 부딪힌다 — {why}", pre
    return True, "", res


# ============================================================ 배포 전 충돌 검사
#  이 랩은 회사 Proxmox 위에서 돈다. 같은 호스트에 운영 VM 이 함께 있다는 뜻이다.
#  "우리 것만 건드린다"를 코드가 아니라 **실제 서버 상태를 조회해서** 확인한다.
OWNER_TAG = "my-network-lab"


def _owned(vm, names=None):
    """이 VM 이 우리 랩 것인가.

    보통은 태그로 안다. 그런데 태그는 VM 을 **설정할 때** 붙는다 — 복제는 됐지만
    설정에서 실패하면 태그 없는 VM 이 남는다. 그 상태로 다시 [랩 생성] 을 누르면
    검사가 자기가 만든 13대를 "남의 VM" 이라며 막고, 화면은 vmid_start 를 옮기라고
    한다. 정반대의 안내다.

    이름은 복제할 때 이미 정해진다(lab1-pc1 …). 그래서 이름도 함께 본다.
    """
    tags = (vm.get("tags") or "").replace(",", ";").split(";")
    if OWNER_TAG in [t.strip() for t in tags]:
        return True
    return bool(names) and vm.get("name") in names


def _lab_vm_names(lab_id):
    """이 랩이 만들 VM 이름 전부. 이름 규칙은 site.yml 의 labs.naming.vm_name."""
    try:
        return {L.vm_name(lab_id, n["name"]) for n in L.TOPO["nodes"]}
    except Exception:                            # noqa: BLE001
        return set()


# 이 랩이 실제로 쓰는 권한. 경로별로 무엇이 왜 필요한지 적어 둔다 —
# 403 은 "권한 없음"만 알려주고 무엇이 없는지는 알려주지 않기 때문이다.
REQUIRED_PRIVS = [
    ("/nodes/{node}", "Sys.Modify",              "리눅스 브리지 생성 (랩 배선)"),
    ("/nodes/{node}", "Sys.Audit",               "노드 상태·네트워크 조회"),
    ("/storage/{ds}", "Datastore.AllocateSpace", "VM 디스크·cloud-init 드라이브 생성"),
    ("/storage/{ds}", "Datastore.Audit",         "스토리지 용량 확인"),
    ("/vms",          "VM.Allocate",             "VM 생성"),
    ("/vms",          "VM.Clone",                "골든 템플릿 복제"),
    ("/vms",          "VM.Config.Network",       "NIC·VLAN 태그 설정"),
    ("/vms",          "VM.Config.Cloudinit",     "관리망 주소·SSH 키 주입"),
    # cloud-init 드라이브는 ide2 에 붙고, Proxmox 는 그것을 CDROM 으로 친다.
    # 이 한 줄이 없어서 검사는 "권한 10개 모두 있다" 라고 했는데 배포는
    # VM 13대가 전부 403 (VM.Config.CDROM) 으로 죽었다. 검사가 통과시킨 것을
    # 배포가 거부하면, 검사는 없느니만 못하다.
    ("/vms",          "VM.Config.CDROM",         "cloud-init 드라이브(ide2)"),
    ("/vms",          "VM.Config.Disk",          "디스크 크기·연결"),
    ("/vms",          "VM.Config.CPU",           "vCPU 수"),
    ("/vms",          "VM.Config.Memory",        "메모리 크기"),
    ("/vms",          "VM.Config.Options",       "부팅 순서·게스트 에이전트"),
    ("/vms",          "VM.PowerMgmt",            "VM 기동·정지"),
    ("/vms",          "VM.Audit",                "VM 상태 조회"),
]


def _priv_lookup(perms, path, priv):
    """/access/permissions 응답에서 이 경로에 이 권한이 있는가.

    응답은 실제로 권한이 있는 경로만 담고 상위 경로에서 전파된 것도 펼쳐서 준다.
    다만 '/' 에 준 경우 하위 경로가 안 나올 수 있어 조상 경로도 함께 본다.
    """
    parts = [p for p in path.split("/") if p]
    candidates = ["/"] + ["/" + "/".join(parts[:i + 1]) for i in range(len(parts))]
    for c in candidates:
        if perms.get(c, {}).get(priv):
            return True
    return False


def check_privileges(cfg=None):
    """토큰이 실제로 무엇을 할 수 있는지 Proxmox 에 직접 묻는다.

    지금까지는 `terraform apply` 한복판에서 403 으로 드러났다 — 브리지는 만들다 말고,
    무엇이 없는지도 알려주지 않는다. 여기서 이름을 대고 미리 막는다.
    """
    cfg = cfg or config()
    c = Check("privs", "API 토큰 권한")
    if not (cfg["token_id"] and cfg["token_secret"]):
        return c.set("skip", "토큰이 없어 건너뛴다")
    try:
        perms = _api(cfg, "/api2/json/access/permissions") or {}
    except Exception as e:                             # noqa: BLE001
        return c.set("warn", f"권한 목록을 읽지 못했다: {type(e).__name__}: {e}",
                     "토큰에 최소한 Sys.Audit 이 필요하다")
    missing = [(path.format(node=cfg["node"], ds=cfg["datastore"]), priv, why)
               for path, priv, why in REQUIRED_PRIVS
               if not _priv_lookup(perms, path.format(node=cfg["node"], ds=cfg["datastore"]), priv)]
    if not missing:
        return c.set("ok", f"필요한 권한 {len(REQUIRED_PRIVS)}개 모두 있다")

    lines = "; ".join(f"{p} 의 {v} ({w})" for p, v, w in missing[:4])
    more = f" 외 {len(missing) - 4}개" if len(missing) > 4 else ""
    hint = ("Proxmox 호스트에서 다음을 확인할 것:\n"
            f"  pveum user token permissions {cfg['token_id'].split('!')[0]} "
            f"{cfg['token_id'].split('!')[-1]} --path /nodes/{cfg['node']}\n"
            "  권한이 비어 있으면 대개 토큰의 '권한 분리(privilege separation)' 탓이다 "
            "— 웹 UI 로 만들면 기본으로 켜지고, 그러면 사용자 역할을 물려받지 않는다:\n"
            f"  pveum user token modify {cfg['token_id'].split('!')[0]} "
            f"{cfg['token_id'].split('!')[-1]} --privsep 0\n"
            "  역할 자체가 부족하면: infra/proxmox-setup.sh 를 호스트에서 실행")
    return c.set("error", f"모자란 권한: {lines}{more}", hint)


def preflight(lab_id, cfg=None):
    """랩을 만들기 전에 남의 자원과 부딪히지 않는지 확인한다.

    Terraform 은 이미 있는 VMID·브리지를 만나면 실패하지만, 그때는 이미
    절반쯤 만들어진 뒤다. 그보다 **시작 전에** 무엇과 부딪히는지 이름을 대는 편이 낫다.
    """
    cfg = cfg or config()
    t0 = time.time()
    cs = []

    # --- VMID 구간 -----------------------------------------------------
    lo, hi = L.vmid_range(lab_id)
    c_id = Check("vmid", f"VMID {lo}~{hi}")
    cs.append(c_id)
    existing = 0                                 # 이미 만들어져 있는 대수 (아래 용량 검사가 쓴다)
    try:
        vms = _api(cfg, "/api2/json/cluster/resources?type=vm") or []
        want = _lab_vm_names(lab_id)
        here = [v for v in vms if lo <= int(v.get("vmid", 0)) <= hi]
        clash = [v for v in here if not _owned(v, want)]
        mine = [v for v in here if _owned(v, want)]
        if clash:
            names = ", ".join(f"{v['vmid']}({v.get('name', '?')})" for v in clash[:5])
            c_id.set("error", f"이 구간을 다른 VM 이 쓰고 있다: {names}",
                     "랩과 무관한 VM 이다. config/site.yml 의 labs.vmid_start 를 비어 있는 "
                     "구간으로 옮기거나, 해당 VM 을 옮길 것. 여기서 멈추지 않으면 "
                     "Terraform 이 절반쯤 만들다 실패한다")
        elif mine:
            existing = len(mine)
            untagged = [v for v in mine if not _owned(v)]
            if untagged:
                c_id.set("ok", f"{len(mine)}대가 이미 이 랩 소유로 존재한다 "
                               f"(그중 {len(untagged)}대는 설정이 덜 끝났다 — 다시 적용하면 이어서 끝난다)")
            else:
                c_id.set("ok", f"{len(mine)}대가 이미 이 랩 소유로 존재한다 (재적용)")
        else:
            c_id.set("ok", "비어 있다")
    except Exception as e:                       # noqa: BLE001
        c_id.set("warn", f"확인하지 못했다: {type(e).__name__}: {e}")

    # --- 디스크 여유 ------------------------------------------------------
    #  local-lvm 은 thin pool 이다. 랩 하나는 8GiB 디스크 13장, 즉 104GiB 를
    #  **약속**하지만 실제로는 쓰는 만큼만 차지한다(실측 25GiB 안팎). 그래서
    #  랩을 여러 개 띄우면 약속 합계가 풀 크기를 훌쩍 넘는다.
    #
    #  평소에는 아무 문제가 없다. 문제는 풀이 꽉 차는 순간이다 — 그때 멈추는
    #  것은 방금 만든 랩 하나가 아니라 **그 풀 위의 모든 VM** 이다. 다른
    #  교육생들의 랩까지 한꺼번에 얼어붙는다.
    #
    #  [설치 상태] 화면에도 여유 공간이 나오지만 그건 보여 줄 뿐 막지는 않는다.
    #  자원을 실제로 만드는 것은 여기이므로, 막는 것도 여기서 한다.
    NEED_PER_LAB = 25 * 1024 ** 3                # 랩 하나의 실사용 실측치
    c_sp = Check("space", f"{cfg['datastore']} 여유 공간")
    cs.append(c_sp)
    try:
        total_nodes = len(L.TOPO["nodes"])
        todo = max(total_nodes - existing, 0)
        need = int(NEED_PER_LAB * todo / total_nodes) if total_nodes else 0
        st = _api(cfg, f"/api2/json/nodes/{cfg['node']}/storage") or []
        mine_ds = next((s for s in st if s.get("storage") == cfg["datastore"]), None)
        if not mine_ds:
            c_sp.set("warn", f"'{cfg['datastore']}' 를 찾지 못해 건너뛴다")
        elif not todo:
            c_sp.set("ok", f"여유 {mine_ds.get('avail', 0) / 1024 ** 3:.0f} GiB "
                           f"· 13대가 이미 있어 새로 만들 디스크가 없다")
        else:
            avail = mine_ds.get("avail", 0)
            g = avail / 1024 ** 3
            if avail < need:
                c_sp.set("error",
                         f"여유 {g:.0f} GiB 뿐이다 (이 랩에 {need / 1024 ** 3:.0f} GiB 쯤 필요)",
                         "지금 만들면 thin pool 이 찰 수 있다. 그러면 이 랩만이 아니라 "
                         "**같은 풀 위의 모든 랩이 함께 멈춘다.** 쓰지 않는 랩을 "
                         "[랩 삭제] 로 정리하거나 스토리지를 늘린 뒤 다시 시도할 것")
            elif avail < need * 2:
                c_sp.set("warn", f"여유 {g:.0f} GiB — 이 랩을 만들면 거의 남지 않는다",
                         "thin pool 이 차면 같은 풀 위의 모든 랩이 함께 멈춘다. "
                         "이번은 진행되지만, 다음 랩 전에 정리가 필요하다")
            else:
                c_sp.set("ok", f"여유 {g:.0f} GiB · 새로 만들 {todo}대에 "
                               f"{need / 1024 ** 3:.0f} GiB 쯤 쓴다")
    except Exception as e:                       # noqa: BLE001
        c_sp.set("warn", f"확인하지 못했다: {type(e).__name__}: {e}")

    # --- 템플릿이 정말 템플릿인가 ----------------------------------------
    tid = L.SITE["labs"]["template_vmid"]
    c_t = Check("template-kind", f"템플릿 VM {tid}")
    cs.append(c_t)
    try:
        conf = _api(cfg, f"/api2/json/nodes/{cfg['node']}/qemu/{tid}/config") or {}
        if conf.get("template"):
            c_t.set("ok", f"템플릿 확인 · {conf.get('name', '?')}")
        else:
            c_t.set("error",
                    f"VMID {tid} 는 템플릿이 아니다 (이름: {conf.get('name', '?')})",
                    "이 번호를 그대로 두면 **운영 VM 을 복제**하게 된다. "
                    "config/site.yml 의 labs.template_vmid 를 확인할 것")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 500):
            c_t.set("error", f"VMID {tid} 가 없다",
                    "골든 템플릿을 먼저 만들 것. Proxmox 호스트에서 root 로:\n"
                    "  apt install -y libguestfs-tools\n"
                    "  ./infra/template/build-golden-template.sh --storage "
                    f"{cfg['datastore']}\n"
                    "  (10분쯤 걸린다 — 이미지 다운로드 + 패키지 설치)")
        else:
            c_t.set("warn", f"HTTP {e.code} {e.reason}")
    except Exception as e:                       # noqa: BLE001
        c_t.set("warn", f"확인하지 못했다: {type(e).__name__}: {e}")

    # --- 권한 -----------------------------------------------------------
    #  브리지·VM 을 만들기 전에 "만들 수 있는가"를 먼저 묻는다.
    cs.append(check_privileges(cfg))

    # --- 브리지 이름 · 대역 ---------------------------------------------
    c_mg = Check("mgmt-bridge", f"관리망 브리지 {L.mgmt_bridge_name()}")
    c_br = Check("bridge", "브리지 이름")
    c_net = Check("subnet", "호스트 네트워크 대역")
    cs += [c_mg, c_br, c_net]
    try:
        ifaces = _api(cfg, f"/api2/json/nodes/{cfg['node']}/network") or []
        by_name = {i.get("iface"): i for i in ifaces}

        # 관리망 브리지는 랩 자원이 아니다 — envs/mgmt 가 미리 만들어 둔다.
        # 없으면 VM 은 만들어지지만 **기동하지 못한다**(존재하지 않는 브리지 참조).
        # 그 실패는 Terraform 이 절반쯤 진행한 뒤에 나므로 여기서 먼저 막는다.
        mgb = L.mgmt_bridge_name()
        vlan = L.mgmt_vlan(lab_id)
        i = by_name.get(mgb)
        if not i:
            c_mg.set("error", f"{mgb} 가 없다",
                     "관리망은 전 랩 공용 브리지 하나를 VLAN 으로 나눠 쓴다. "
                     "최초 1회만 만들면 된다 — 아래 버튼으로 지금 만들 수 있다. "
                     "없으면 VM 이 만들어져도 기동하지 못한다",
                     fix=_fix_mgmt(cfg.get("lab_count") or 1))
        elif not (OWNER_TAG in (i.get("comments") or "")) and (
                i.get("bridge_ports") or i.get("cidr")):
            # 우리가 만든 표시가 없는데 포트나 주소가 있다 = 남이 쓰고 있는 브리지다.
            # 그대로 쓰면 사내 트래픽이 랩 관리망과 한 L2 에 들어온다.
            c_mg.set("error",
                     f"{mgb} 가 이미 다른 용도로 쓰이고 있다"
                     + (f" (포트: {i['bridge_ports']})" if i.get("bridge_ports") else "")
                     + (f" ({i['cidr']})" if i.get("cidr") else ""),
                     "이 브리지를 랩 관리망으로 쓰면 사내 트래픽과 한 L2 가 된다. "
                     "config/site.yml 의 labs.naming.mgmt_bridge 를 비어 있는 이름으로 "
                     "바꾼 뒤 [설치] 화면에서 다시 만들 것 — 이건 콘솔이 대신 정할 수 없다")
        elif not i.get("bridge_vlan_aware"):
            # 태그를 브리지가 무시하면 **모든 랩의 관리망이 한 L2 로 합쳐진다.**
            # 통신은 되므로 눈치채기 어렵고, 랩 간 격리만 조용히 사라진다.
            c_mg.set("error", f"{mgb} 가 VLAN-aware 가 아니다",
                     "이 상태로는 랩별 VLAN 태그가 무시되어 전 랩 관리망이 한 L2 로 합쳐진다. "
                     "아래 버튼으로 다시 만들면 켜진다",
                     fix=_fix_mgmt(cfg.get("lab_count") or 1))
        else:
            c_mg.set("ok", f"{mgb} (VLAN-aware) · 이 랩 = VLAN {vlan}")

        want = [b["name"] for b in L.all_bridges(lab_id, "m11")]
        taken = []
        for name in want:
            i = by_name.get(name)
            if not i:
                continue
            # 이미 있는 브리지라도 우리가 만든 것이면 재적용일 뿐이다.
            if OWNER_TAG in (i.get("comments") or "") or f"lab{lab_id} " in (i.get("comments") or ""):
                continue
            taken.append(f"{name}"
                         + (f"(포트: {i['bridge_ports']})" if i.get("bridge_ports") else "")
                         + (f"({i['cidr']})" if i.get("cidr") else ""))
        if taken:
            c_br.set("error", "같은 이름의 브리지가 이미 있다: " + ", ".join(taken),
                     "랩이 그 브리지를 자기 것으로 여기고 지울 수 있다. "
                     "config/site.yml 의 labs.naming.bridge 접두어를 바꿀 것")
        else:
            c_br.set("ok", f"{len(want)}개 모두 사용 가능")

        # 호스트가 이미 쓰는 대역과 랩 대역이 겹치는가
        import ipaddress
        blocks = {"management": L.SITE["networks"]["management"],
                  "lab_block": L.SITE["networks"]["lab_block"],
                  "public_transit": L.SITE["networks"]["public_transit"],
                  "public_service": L.SITE["networks"]["public_service"],
                  "external_net": L.SITE["networks"]["external_net"]}
        hits = []
        for i in ifaces:
            cidr = i.get("cidr")
            if not cidr or i.get("iface") in want or i.get("iface") == mgb:
                continue
            try:
                hn = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            for k, v in blocks.items():
                if hn.overlaps(ipaddress.ip_network(v)):
                    hits.append((k, v, i.get("iface"), cidr))
        mgmt_hit = [h for h in hits if h[0] == "management"]
        if mgmt_hit:
            k, v, ifn, cidr = mgmt_hit[0]
            c_net.set("error", f"관리망 {v} 이 호스트의 {ifn}({cidr}) 과 겹친다",
                      "호스트 라우팅이 충돌해 랩 접속 자체가 불가능해진다. "
                      "config/site.yml 의 networks.management 를 비어 있는 대역으로 옮길 것")
        elif hits:
            k, v, ifn, cidr = hits[0]
            c_net.set("warn", f"{k} {v} 이 호스트의 {ifn}({cidr}) 과 겹친다",
                      "랩 브리지는 격리돼 있어 당장 문제는 없지만, "
                      "호스트나 점프 호스트에서 랩 주소로 접속할 때 헷갈린다")
        else:
            c_net.set("ok", f"호스트 인터페이스 {len(ifaces)}개와 겹치지 않는다")
    except Exception as e:                       # noqa: BLE001
        c_br.set("warn", f"확인하지 못했다: {type(e).__name__}: {e}")
        c_net.set("skip", "브리지 목록을 읽지 못해 건너뛴다")

    # --- 적용 대기 중인 네트워크 변경 -------------------------------------
    #  브리지를 만들면 Proxmox 가 호스트 네트워크를 다시 읽는다(ifreload).
    #  그때 남의 대기 중 변경까지 함께 적용되면 다른 VM 의 통신이 끊길 수 있다.
    c_pend = Check("pending", "호스트의 대기 중 네트워크 변경")
    cs.append(c_pend)
    try:
        env = _api(cfg, f"/api2/json/nodes/{cfg['node']}/network", envelope=True) or {}
        if env.get("changes"):
            c_pend.set("warn", "적용되지 않은 네트워크 변경이 남아 있다",
                       "랩이 브리지를 만들 때 호스트 네트워크가 다시 적용되면서 "
                       "이 변경까지 함께 반영된다. Proxmox 화면에서 먼저 정리할 것")
        else:
            c_pend.set("ok", "없음")
    except Exception as e:                       # noqa: BLE001
        c_pend.set("skip", f"확인하지 못했다: {type(e).__name__}")

    ds = [c.as_dict() for c in cs]
    level = "error" if any(c["status"] == "error" for c in ds) else \
            "warn" if any(c["status"] == "warn" for c in ds) else "ok"
    bad = next((c for c in ds if c["status"] == "error"), None)
    return {
        "ok": level != "error",
        "level": level,
        "kind": "preflight",
        "endpoint": endpoint(cfg),
        "node": cfg["node"],
        "checked_at": _now(),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "checks": ds,
        "confirmed": confirmed(),
        "summary": (f"lab{lab_id} 배포 전 검사 통과" if level == "ok" else
                    f"{bad['title']} — {(bad['detail'] or '').splitlines()[0][:70]}" if bad else
                    f"lab{lab_id} 배포 전 검사 — 확인 필요"),
    }
