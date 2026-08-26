"""
my-network-lab 웹 콘솔 (v1)

  · 모듈 개요·목표·실습 내용을 브라우저에서 읽고
  · 버튼으로 그 모듈을 랩에 적용·검증·초기화하고
  · 토폴로지를 그 단계 기준으로 확인한다.

설계 의도: CLI 를 **대체하지 않고 감싼다.** 학습 자체는 여전히 터미널에서 한다.

실행 위치는 Proxmox 호스트일 수도, 별도의 서버일 수도 있다. 어느 Proxmox 에 붙을지는
[관리자 → 연결 설정] 에서 정하고(기본값 localhost), 그 상태가 정상일 때만 작업이 나간다.
어느 쪽이든 사무실 LAN 에서만 접근 허용할 것.
"""
import asyncio
import html
import secrets
import time
import subprocess
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from markupsafe import Markup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

import labdesign as L          # noqa: E402
import preflight              # noqa: E402  (tools/ — make doctor 와 같은 검사)
import assess, auth, autokey, db, docs, exam, jobs, passwords, pve, sshkeys, state, topology_svg  # noqa: E402

db.init()   # 스키마 생성 + (있다면) 예전 YAML 계정 이관
pve.sync()  # DB 에 저장된 Proxmox 접속 정보를 var/runtime.yml 로 다시 내보낸다

runner = jobs.Runner()
# 실행 관문에 시험 잠금을 꽂는다. 규칙은 exam.py 가 갖고 있고, 물어보는 자리는 여기 한 곳뿐이다.
runner.guard = exam.gate


@asynccontextmanager
async def lifespan(_app):
    """배경 작업을 띄운다 — 시험 마감 스위퍼와 접속 키 자동 반영.

    콘솔이 꺼져 있는 동안 지난 마감도 기동 직후 첫 주기에서 확정된다 —
    마감이 프로세스 생존에 의존하면 "껐다 켜서 시간을 벌었다"가 가능해진다.
    """
    # 점프 헬퍼를 쓸 수 있는지 묻는 방법을 autokey 에 꽂는다. 판단은 한 곳에만 둔다.
    autokey.ready = _jump_apply_ready
    tasks = [asyncio.create_task(exam.sweeper(runner)),
             asyncio.create_task(autokey.worker(runner))]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="my-network-lab console", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=auth.session_secret(),
                   session_cookie="labconsole", same_site="lax")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=str(HERE / "templates"))


def _labtext(value):
    """교재에서 온 여러 줄 텍스트를 안전하게 HTML 로.

    퀴즈 지문에는 `<NO-CARRIER,BROADCAST,MULTICAST,UP>` 같은 문자열이 그대로 나온다.
    이스케이프하지 않고 |safe 로 내보내면 브라우저가 이것을 **태그로 읽어 통째로 삼킨다** —
    플래그를 읽는 문제인데 정작 플래그가 화면에서 사라진다.
    그래서 ① 먼저 이스케이프하고 ② 백틱만 <code> 로 살리고 ③ 줄바꿈을 <br> 로 바꾼다.
    """
    text = html.escape(str(value or ""))
    # 코드 조각을 먼저 빼 둔다. 굵게 표시를 그 안에서도 찾으면
    # `a**b**c` 같은 문자열이 코드가 아니라 강조로 읽힌다.
    spans = []

    def _stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _stash, text)
    # 교재와 같은 표기를 퀴즈에서도 쓴다. 안 하면 화면에 별 두 개가 그대로 보인다.
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{spans[int(m.group(1))]}</code>", text)
    return Markup(text.replace("\n", "<br>"))


tpl.env.filters["labtext"] = _labtext


# ------------------------------------------------------------------ 헬퍼
def current_user(request):
    """세션에는 username 만 있다. 권한은 매 요청 DB 에서 다시 읽는다.

    쿠키에 role/lab_id 를 넣어두면 계정을 막거나 랩 배정을 바꿔도
    이미 로그인한 세션에는 반영되지 않는다(권한 회수 불가).
    """
    sess = request.session.get("user") or {}
    u = auth.load_user(sess.get("username"))
    if not u:
        request.session.clear()
    return u


def require(request, cap=None, skip_setup=False):
    """로그인 → 비밀번호 변경 → 접속 키 등록 → 최초 연결 설정 확인 → 권한 확인 순.

    Proxmox 접속 정보 확인을 비밀번호 변경 바로 다음에 두는 이유:
    이 값이 틀리면 어떤 버튼도 동작하지 않는다. 교육생이 먼저 부딪히기 전에
    관리자가 한 번 보고 넘어가게 한다.
    """
    u = current_user(request)
    if not u:
        return None, RedirectResponse("/login", status_code=303)
    if u.get("must_change_password"):
        return None, RedirectResponse("/password", status_code=303)
    # "첫 로그인" 이 아니라 **끝났는가**로 판단한다. 비밀번호만 바꾸고 나갔다가
    # 다시 들어오는 사람이 있고, 그 사람에게 절차가 끝난 척하면 안 된다.
    #   · 랩이 만들어졌는가 — state 에 적어 둔 마지막 값 (진실은 Proxmox 이고,
    #     마법사가 들어갈 때 다시 물어 이 값을 고친다. 요청마다 API 를 두드리지 않는다)
    #   · 내 접속 키가 등록됐는가 — u 는 요청마다 DB 에서 다시 읽은 값이다
    # 가둬 두지는 않는다. [나중에 하기] 로 넘어갈 수 있고, 그 표시는 세션에만 남아
    # 다음 로그인 때 다시 묻는다.
    if (not skip_setup and u.get("role") == "user"
            and not request.session.get("onboard_later")
            and _onboard_step(u) is not None):
        return None, RedirectResponse("/onboard", status_code=303)
    if not skip_setup and auth.can(u, "user.manage") and not pve.confirmed():
        return None, RedirectResponse("/admin/settings?setup=1", status_code=303)
    if cap and not auth.can(u, cap):
        return None, HTMLResponse("권한이 없습니다.", status_code=403)
    return u, None


def _onboard_step(user):
    """이 사람에게 아직 남은 준비 단계. 없으면 None.

    관문과 마법사가 **같은 함수**를 본다. 둘이 따로 판단하면 "관문은 보내는데
    마법사는 할 일이 없다" 는 무한 되돌기가 생긴다.
    """
    if auth.can(user, "user.manage"):
        return None                      # 관리자에게는 이 절차가 없다
    lab_id = pick_lab(user)
    if lab_id and state.provisioned(lab_id) is not True:
        return 1                         # None(모름) 도 한 번 확인하러 보낸다
    if not (user or {}).get("ssh_key"):
        return 2
    return None


def pick_lab(user, requested=None):
    labs = auth.allowed_labs(user)
    if not labs:
        return None
    if requested is not None and int(requested) in labs:
        return int(requested)
    return labs[0]


def nav_ctx(user, here, lab_id=None):
    """모든 화면이 같은 헤더를 그리는 데 필요한 것.

    전에는 템플릿마다 헤더를 손으로 썼고, 그래서 화면마다 링크가 달랐다.
    무엇을 보여 줄지는 한 곳에서 정한다.
    """
    is_admin = auth.can(user, "user.manage")
    return {
        "nav": here,
        "nav_admin": is_admin,
        "nav_labs": auth.allowed_labs(user),
        "lab_id": lab_id,
        "pending": db.count_pending() if is_admin else 0,
        "has_ssh_key": bool((user or {}).get("ssh_key")),
        "health": pve.last(),
        "site_name": L.SITE["site"]["name"],
    }


def base_ctx(request, user, lab_id):
    st = state.load(lab_id)
    mods = docs.modules()
    is_admin = auth.can(user, "lab.all")
    unlocked = assess.unlocked_modules(user["username"], is_admin)
    return {
        **nav_ctx(user, "lab", lab_id),
        "request": request, "user": user, "lab_id": lab_id,
        "labs": auth.allowed_labs(user),
        "modules": [{**m, "status": state.module_status(lab_id, m),
                     "unlocked": unlocked.get(m["id"], False),
                     "progress": assess.module_state(user["username"], m, is_admin)}
                    for m in mods],
        "unlocked": unlocked,
        "lab_state": st,
        "busy": runner.busy(lab_id),
        "scenarios": jobs.scenario_ids(),
        "site_name": L.SITE["site"]["name"],
        "health": pve.last(),          # 마지막 점검 결과. 화면은 즉시 뜨고 JS 가 갱신한다
        "pve": pve.public(),
        "pending": db.count_pending() if is_admin else 0,
        # 키를 안 넣으면 노드에 SSH 로 못 들어간다. 헤더에서 눈에 띄게 한다.
        "has_ssh_key": bool((db.get_user(user["username"]) or {}).get("ssh_key")),
        # 시험 상태. 진행 중에는 시나리오가 실려 나가지 않는다 (exam.view 가 걸러낸다).
        "exam": exam.view(lab_id, user),
        "capstone": exam.module_id(),
        # 중간 점검은 **단계마다** 열린다. 지금 보고 있는 모듈이 그 단계면 버튼이 나온다.
        "checkpoints": {c["stage"]: c for c in exam.drill_cfg()["checkpoints"]},
        # 이미 받아 간 힌트. 새로고침했다고 사라지면 다시 받으러 누르게 된다.
        "drill_hints": [h for h in (_drill_hint(st, i)
                                    for i in range(1, int(st.get("hints") or 0) + 1)) if h],
    }


# ------------------------------------------------------------------ 인증
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str = ""):
    return tpl.TemplateResponse(request, "login.html",
                                {"error": error, "site_name": L.SITE["site"]["name"]})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        u = auth.authenticate(username, password)
    except auth.LoginError as e:
        return tpl.TemplateResponse(request, "login.html",
                                    {"error": e.message, "site_name": L.SITE["site"]["name"]},
                                    status_code=401)
    request.session["user"] = {"username": u["username"]}   # 권한은 담지 않는다
    if u.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------- 비밀번호 변경 (강제 포함)
@app.get("/password", response_class=HTMLResponse)
async def password_form(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    return tpl.TemplateResponse(request, "password.html",
                                {"user": u, "errors": [], "forced": u["must_change_password"],
                                 "policy": passwords.policy_text(),
                                 "site_name": L.SITE["site"]["name"]})


@app.post("/password", response_class=HTMLResponse)
async def password_change(request: Request, current: str = Form(""),
                          new1: str = Form(...), new2: str = Form(...)):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    errors = []
    if new1 != new2:
        errors.append("새 비밀번호가 서로 다릅니다")
    else:
        # 최초 비밀번호 변경은 현재 비밀번호를 다시 묻지 않는다 (방금 로그인했다)
        errors = auth.change_password(u["username"], new1, current,
                                      require_current=not u["must_change_password"])
    if errors:
        # 거절되면 같은 화면이 다시 뜬다. 사용자가 오류 문구를 못 보고
        # "안 넘어간다" 고 느끼는 일이 있어, 서버 로그에도 이유를 남긴다.
        print(f"[auth] {u['username']} 비밀번호 변경 거절: {' / '.join(errors)}",
              file=sys.stderr)
        return tpl.TemplateResponse(request, "password.html",
                                    {"user": u, "errors": errors,
                                     "forced": u["must_change_password"],
                                     "policy": passwords.policy_text(),
                                     "site_name": L.SITE["site"]["name"]}, status_code=400)
    print(f"[auth] {u['username']} 비밀번호 변경 완료", file=sys.stderr)
    return RedirectResponse("/", status_code=303)


# ============================================================ SSH 접속 키
#  교육생이 직접 등록한다. site.yml 에 두면 전 랩 전 노드에 박히고, 바꾸려면
#  VM 을 다시 만들어야 한다. 여기 두면 **배정된 랩에만** 들어가고 즉시 반영된다.
def _jump_account_exists(username):
    """운영 서버에 이 교육생의 점프 계정이 있는가.

    tools/gen-jumpaccess.py 가 만드는 계정이다. 관리자가 아직 안 돌렸으면 없다.
    그 상태로 `ssh pc1` 을 하면 sshd 가 계정 열거를 막으려고 비밀번호를 묻는다 —
    있지도 않은 비밀번호를. 무엇이 빠졌는지 여기서 먼저 말해 준다.
    """
    try:
        import pwd                                     # noqa: PLC0415
        pwd.getpwnam(username)
        return True
    except (KeyError, ImportError):
        return False


def _sshkey_ctx(request, user, errors=(), saved="", onboard=False):
    lab_id = pick_lab(user)
    raw = (db.get_user(user["username"]) or {}).get("ssh_key") or ""
    node = L.TOPO["nodes"][0]["name"]
    A = L.IPAM["access"]
    return {**nav_ctx(user, "sshkey", lab_id),
            "request": request, "user": user, "site_name": L.SITE["site"]["name"],
            "health": pve.last(), "pve": pve.public(),
            "lab_id": lab_id,
            "lab_label": f"lab {lab_id}" if lab_id else "미배정",
            "current": sshkeys.describe(raw) if raw else None,
            # 저장은 밀리초까지 하지만(반영 여부 판정에 필요하다) 화면에는 초까지만.
            "key_at": ((db.get_user(user["username"]) or {}).get("ssh_key_at") or "")[:19] or None,
            "errors": list(errors), "saved": saved,
            # 첫 로그인 안내 화면인가 (아직 키가 없어서 여기로 보내진 상태)
            "onboard": bool(onboard),
            # 자동 반영이 걸려 있는가 — 화면이 스스로 새로 고치며 기다린다
            "auto_pending": autokey.pending(lab_id),
            # "자동으로 반영한다" 고 말해 놓고 조용히 실패하는 것이 제일 나쁘다.
            # 실패했으면 사유를 그대로 보여 주고, 손으로 거는 길을 알려 준다.
            "auto_failed": autokey.failures(lab_id),
            "busy": runner.busy(lab_id) if lab_id else True,
            "jump_user": A["jump_host"]["user"], "jump_ip": A["jump_host"]["office_ip"],
            "lab_user": A["lab_user"],
            "example_node": node, "example_ip": L.mgmt_ip(lab_id or 1, node),
            # 콘솔(화면) 접속용. SSH 는 키로만 받지만 콘솔은 키를 못 쓴다.
            # 만들어져 있을 때만 보여준다 — 아직 배포 전이면 굳이 만들지 않는다.
            "console_pw": db.lab_console_password(create=False),
            # 점프 계정이 없으면 ssh 가 조용히 비밀번호를 묻는다(= 영원히 못 들어간다).
            # 콘솔은 운영 서버에서 돌고 있으니 여기서 바로 확인해 알려준다.
            "jump_ready": _jump_account_exists(user["username"]),
            # 계정이 있는 것과 **지금 키가 거기 들어 있는 것**은 다르다.
            "jump_stale": any(u["username"].lower() == user["username"].lower()
                              for u in db.jump_stale_users()),
            "pve_url": L.IPAM["access"]["proxmox"]["api_endpoint"],
            "vm_name": L.vm_name(lab_id or 1, node),
            # Proxmox 로그인 계정 — **자기 랩 것만**. 랩당 1계정이라 여기서 보여 줄 수 있고,
            # 그래서 관리자가 사람마다 비밀번호를 전달할 일이 없다.
            # create=False: 아직 만들지 않았으면 화면 여는 것만으로 만들지 않는다.
            **dict(zip(("pve_user", "pve_pw"),
                       db.lab_pve_account(lab_id, create=False) if lab_id else ("", "")))}


# ------------------------------------------------------------------ 첫 로그인 마법사
def _onboard_ctx(request, user, step, err=""):
    lab_id = pick_lab(user)
    have, total = (None, 0)
    if lab_id:
        # 마법사에 들어올 때만 Proxmox 에 직접 묻는다. 여기가 그 값을 고치는 자리다 —
        # 관문은 이 결과를 적어 둔 것만 보고, 요청마다 API 를 두드리지 않는다.
        have, total = pve.lab_vms(lab_id)
        if have is not None and total:
            state.set_provisioned(lab_id, have >= total)
    return {
        **nav_ctx(user, "onboard", lab_id),
        "request": request, "user": user, "step": step, "err": err,
        "lab_id": lab_id, "have": have, "total": total,
        # 랩이 온전한가 · 아예 없는가 · 물어보지도 못했는가
        "lab_ready": have is not None and total and have >= total,
        "lab_unknown": have is None,
        "busy": runner.busy(lab_id) if lab_id else True,
        "active_job": runner.active.get(lab_id) if lab_id else None,
        "has_key": bool((user or {}).get("ssh_key")),
    }


@app.get("/onboard", response_class=HTMLResponse)
async def onboard(request: Request, step: int = 0):
    """첫 로그인 절차를 한 화면에서 끝낸다 — 랩 준비 → 접속 키 → 끝.

    예전에는 교육생이 로그인한 뒤 무엇을 해야 하는지 화면이 말해 주지 않았고,
    랩은 관리자가 따로 만들어 줘야 했다.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    if auth.can(user, "user.manage"):
        return RedirectResponse("/", status_code=303)     # 관리자는 이 절차가 없다
    # 어느 칸부터 보여 줄지는 **남은 일**이 정한다. 주소로 직접 고르면 그대로 따른다
    # (다시 보고 싶을 수 있다). 아무것도 남지 않았으면 마지막 칸이다.
    want = max(1, min(3, step)) if step else (_onboard_step(user) or 3)
    ctx = await asyncio.to_thread(_onboard_ctx, request, user, want)
    # Proxmox 에 물어보지도 못했다면, 여기서 붙잡아 두면 **영영 나갈 수 없다** —
    # 관문은 '모른다' 를 보고 계속 이리로 되돌리고, 마법사는 계속 알아내지 못한다.
    # 이 세션에서는 더 막지 않는다. 다음 로그인 때 다시 묻는다.
    if ctx["lab_unknown"]:
        request.session["onboard_later"] = True
    return tpl.TemplateResponse(request, "onboard.html", ctx)


@app.post("/onboard/deploy")
async def onboard_deploy(request: Request):
    """랩이 없으면 만든다. 화면이 열릴 때 자동으로 부른다.

    GET 이 아니라 POST 다 — 화면을 새로 고치는 것만으로 VM 27개를 만들면 안 된다.
    같은 랩의 두 번째 사람이 눌러도 Runner 의 랩 단위 잠금이 막고, 그 사람에게는
    이미 도는 작업의 번호를 그대로 돌려준다. 그러면 둘이 같은 진행률을 본다.
    """
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    lab_id = pick_lab(user)
    if not lab_id:
        return JSONResponse({"error": "배정된 랩이 없습니다. 교육 담당자에게 문의해 주세요"},
                            status_code=400)
    running = runner.active.get(lab_id)
    if running:
        return JSONResponse({"job_id": running, "joined": True})
    have, total = await asyncio.to_thread(pve.lab_vms, lab_id)
    if have is not None and total and have >= total:
        return JSONResponse({"ready": True})
    stage = state.load(lab_id).get("stage") or L.STAGES[0]
    try:
        job = await runner.submit(lab_id, "deploy", stage, None, user.get("username"),
                                  on_done=lambda j: state.record(
                                      j.lab_id, j.action, j.stage, j.status == "ok", None, j.id))
        # state.record 가 성공한 deploy 에서 provisioned 를 True 로 적는다.
    except (jobs.Locked, jobs.NotReady, RuntimeError, ValueError) as e:
        return JSONResponse({"error": getattr(e, "message", None) or str(e)}, status_code=409)
    return JSONResponse({"job_id": job.id})


@app.get("/sshkey", response_class=HTMLResponse)
async def sshkey_form(request: Request, onboard: int = 0, later: int = 0,
                      applied: int = 0, changed: int = 0, removed: int = 0):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    if later:
        # 지금은 키를 만들 수 없는 사람도 있다. 교재는 읽게 해 준다.
        # 세션에만 남기므로 다음 로그인 때 다시 묻는다 — 잊고 넘어가지 않게.
        request.session["onboard_later"] = True
        return RedirectResponse("/", status_code=303)
    msg = ""
    if applied:
        msg = ("등록했습니다. 점프 계정과 랩 노드에 자동으로 반영합니다 — 1~2분쯤 걸립니다. "
               "이 화면은 스스로 새로 고쳐지니 기다리면 됩니다.")
    elif changed:
        msg = ("바꿨습니다. 점프 계정과 랩 노드에 자동으로 반영합니다 — "
               "예전 키로는 곧 들어갈 수 없게 됩니다.")
    elif removed:
        msg = "키를 지웠습니다. 점프 계정과 랩 노드에서 자동으로 회수합니다."
    return tpl.TemplateResponse(request, "sshkey.html",
                                _sshkey_ctx(request, user, saved=msg,
                                            onboard=bool(onboard)))


@app.post("/sshkey", response_class=HTMLResponse)
async def sshkey_save(request: Request, key: str = Form(""), remove: str = Form(""),
                      next: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    if remove:
        db.set_ssh_key(user["username"], "")
        # 지운 것도 반영해야 한다 — 헬퍼가 그 계정의 접근을 회수한다.
        autokey.request(pick_lab(user))
        return RedirectResponse("/sshkey?removed=1", status_code=303)
    try:
        normalized = sshkeys.normalize(key)
    except sshkeys.Invalid as e:
        return tpl.TemplateResponse(request, "sshkey.html",
                                    _sshkey_ctx(request, user, errors=[str(e)]),
                                    status_code=400)
    # 처음 넣은 것인지 바꾼 것인지는 안내 문구만 가른다.
    # **반영은 둘 다 자동으로 건다** — 바꿀 때만 손으로 눌러야 했더니,
    # 누르는 것을 잊은 사람이 첫 홉에서 막히고 이유를 알지 못했다.
    first = not (db.get_user(user["username"]) or {}).get("ssh_key")
    db.set_ssh_key(user["username"], normalized)
    autokey.request(pick_lab(user))
    # 화면을 바로 그리지 않고 되돌린다(PRG). 자동 반영 중에는 이 화면이 스스로
    # 새로 고쳐지는데, POST 응답을 새로 고치면 브라우저가 폼을 다시 보낸다.
    # 마법사에서 왔으면 마법사의 다음 칸으로 돌아간다.
    # 값은 우리가 만든 화면 안의 경로만 받는다 — 폼에 실려 오는 주소를 그대로
    # 믿으면 로그인한 사람을 바깥 사이트로 튕겨 보낼 수 있다.
    if next in ("/onboard?step=3",):
        return RedirectResponse(next, status_code=303)
    return RedirectResponse(f"/sshkey?{'applied' if first else 'changed'}=1", status_code=303)


@app.get("/sshkey/config")
async def sshkey_config(request: Request):
    """이 교육생의 ~/.ssh/config 조각을 내려준다.

    전에는 관리자가 `gen-ssh-config.py` 로 만들어 파일로 나눠 줘야 했다.
    교재는 "배포받은 설정" 을 전제하는데 받을 방법이 없었다 —
    교육생이 스스로 받아 가게 한다. 생성기는 CLI 와 같은 것을 쓴다.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    lab_id = pick_lab(user)
    if not lab_id:
        return HTMLResponse("배정된 랩이 없습니다.", status_code=403)
    # 점프 계정 이름 = 콘솔 계정 이름 (tools/gen-jumpaccess.py 가 그렇게 만든다)
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(HERE.parent / "tools/gen-ssh-config.py"),
         "--lab", str(lab_id), "--user", user["username"]],
        capture_output=True, text=True)
    if proc.returncode:
        return HTMLResponse(f"설정을 만들지 못했다: {proc.stderr[:300]}", status_code=500)
    return PlainTextResponse(
        proc.stdout,
        headers={"Content-Disposition":
                 f'attachment; filename="ssh-config-lab{lab_id}"'})


@app.post("/sshkey/apply")
async def sshkey_apply(request: Request):
    """접속 키만 랩 노드에 올린다 (common 역할의 keys 태그).

    예전에는 site.yml 전체를 다시 돌렸다. 키 한 줄 넣자고 13대의 설정을 다시
    올리느라 몇 분이 걸렸고, 교육생은 버튼이 먹통인 줄 알았다."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    lab_id = pick_lab(user)
    if not lab_id or lab_id not in auth.allowed_labs(user):
        return RedirectResponse("/sshkey", status_code=303)
    stage = state.load(lab_id).get("stage") or L.STAGES[0]
    try:
        await runner.submit(lab_id, "keys", stage, None, user.get("username"),
                            on_done=lambda j: (
                                state.record(j.lab_id, j.action, j.stage,
                                             j.status == "ok", j.scenario, j.id),
                                # 손으로 해결했으면 "자동 반영 실패" 안내도 걷는다
                                j.status == "ok" and autokey.clear(lab_id=j.lab_id)))
    except (jobs.Locked, jobs.NotReady, RuntimeError, ValueError) as e:
        msg = getattr(e, "message", None) or str(e)
        return tpl.TemplateResponse(request, "sshkey.html",
                                    _sshkey_ctx(request, user, errors=[msg]), status_code=409)
    # 진행 상황은 메인 화면의 실행 로그에서 본다 — 로그 창을 두 곳에 두지 않는다.
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ------------------------------------------------------------------ 화면
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, lab: int | None = None, m: str | None = None):
    user, redir = require(request)
    if redir:
        return redir
    lab_id = pick_lab(user, lab)
    if lab_id is None:
        return HTMLResponse("배정된 랩이 없습니다. 교육 담당자에게 문의해 주세요.", status_code=403)
    mods = docs.modules()
    ctx = base_ctx(request, user, lab_id)
    if m and ctx["unlocked"].get(m):
        module = docs.get(m)
    else:
        # 열려 있는 모듈 중 마지막 = 지금 해야 할 모듈
        opened = [x for x in mods if ctx["unlocked"].get(x["id"])]
        module = opened[-1] if opened else (mods[0] if mods else None)
    ctx["module"] = module
    if module:
        db.mark_tab_seen(user["username"], module["id"], "README")
    ctx["module_html"] = _module_html(module, lab_id, user) if module else ""
    ctx["topology_svg"] = topology_svg.render(module["stage"]) if module else ""
    ctx["kind"] = "README"
    if module:
        ctx.update(_assess_ctx(user, lab_id, module))
        ctx.update(_tab_ctx(user, module))
    return tpl.TemplateResponse(request, "index.html", ctx)


def _module_html(module, lab_id, user, kind="README"):
    text = docs.render_markdown(module, lab_id, kind)
    if text is None:
        return "<p>문서가 없습니다.</p>"
    return docs.to_html(text, module)


@app.get("/m/{module_id}", response_class=HTMLResponse)
async def module_view(request: Request, module_id: str, lab: int | None = None,
                      kind: str = "README"):
    user, redir = require(request)
    if redir:
        return redir
    lab_id = pick_lab(user, lab)
    module = docs.get(module_id)
    if not module:
        return HTMLResponse("없는 모듈", status_code=404)
    if kind == "answers" and not auth.can_see_answers(user):
        return HTMLResponse('<div class="notice">해설은 관리자만 볼 수 있습니다.</div>',
                            status_code=403)
    is_admin = auth.can(user, "lab.all")
    # 교재 → 과제 → 퀴즈. 화면에서 막는 것만으로는 부족하다(URL 로 건너뛸 수 있다).
    if not db.tab_allowed(user["username"], module_id, kind, is_admin):
        label = {"README": "교재", "quiz": "퀴즈", "tasks": "과제", "verify": "검증"}
        prev = db.TAB_ORDER[max(0, db.TAB_ORDER.index(kind) - 1)] if kind in db.TAB_ORDER else "README"
        return HTMLResponse(
            f'<div class="notice"><b>{label.get(prev, prev)}</b> 를 먼저 보세요. '
            f'위에서 순서대로 진행합니다 — 교재 → 퀴즈 → 과제 → 검증.</div>',
            status_code=403)
    db.mark_tab_seen(user["username"], module_id, kind)
    ctx = base_ctx(request, user, lab_id)
    if not ctx["unlocked"].get(module_id, False):
        return HTMLResponse(
            '<div class="notice">앞 모듈을 통과해야 열립니다. '
            '앞 모듈에서 <b>제출하고 검증</b> 을 눌러 통과해 주세요.</div>', status_code=403)
    ctx["module"] = module
    ctx["kind"] = kind
    # 퀴즈·검증 탭은 마크다운이 아니라 폼이다.
    ctx["module_html"] = "" if kind in ("quiz", "verify") else _module_html(module, lab_id, user, kind)
    ctx.update(_assess_ctx(user, lab_id, module))
    ctx.update(_tab_ctx(user, module))
    return tpl.TemplateResponse(request, "_module.html", ctx)


def _tab_ctx(user, module):
    """탭 잠금 상태. 화면이 왜 잠겼는지 말해 줄 수 있어야 한다."""
    is_admin = auth.can(user, "lab.all")
    return {"tab_allowed": {k: db.tab_allowed(user["username"], module["id"], k, is_admin)
                            for k in db.TAB_ORDER}}


def _assess_ctx(user, lab_id, module):
    mods = docs.modules()
    ids = [m["id"] for m in mods]
    i = ids.index(module["id"])
    return {
        "quiz": assess.quiz_for_client(module, lab_id) if assess.has_quiz(module) else None,
        "has_checks": assess.has_checks(module),
        "written": assess.written_items(module, lab_id),
        "mstate": assess.module_state(user["username"], module, auth.can(user, "lab.all")),
        "last_quiz": db.latest_attempt(user["username"], module["id"], "quiz"),
        "last_checks": db.latest_attempt(user["username"], module["id"], "checks"),
        "next_module": mods[i + 1] if i + 1 < len(mods) else None,
        # 이 모듈을 하기에 랩이 모자란가 · 맞는가 · 앞서 있는가
        "stage_gap": state.stage_gap(lab_id, module["stage"]),
    }


# ------------------------------------------------------------------ 제출·검증
@app.post("/m/{module_id}/submit")
async def submit(request: Request, module_id: str):
    user, redir = require(request)
    if redir:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    module = docs.get(module_id)
    if not module:
        return JSONResponse({"error": "없는 모듈"}, status_code=404)
    lab_id = pick_lab(user, None)
    form = await request.form()
    # 어느 단계에서 낸 것인가. 퀴즈는 개념 확인이라 랩을 건드리지 않는다 —
    # 랩 검사를 같이 돌리면 아직 설정도 안 한 랩에서 검사가 무조건 실패한다.
    phase = (form.get("phase") or "verify").strip()

    # 서술형을 먼저 저장한다 — 진도 계산이 제출 여부를 보기 때문이다.
    written_res = None
    if phase != "quiz" and assess.has_written(module):
        wans = {k[2:]: form.get(k) for k in form.keys() if k.startswith("w_")}
        written_res = assess.submit_written(user["username"], lab_id, module, wans)

    # 퀴즈는 **퀴즈 단계에서만** 채점한다. 검증 폼에는 문항이 없으므로,
    # 여기서 그냥 채점하면 빈 답으로 0점을 매겨 이미 통과한 점수를 덮어쓴다.
    quiz_res = None
    if phase == "quiz" and assess.has_quiz(module):
        answers = {}
        for k in form.keys():
            if k.startswith("q_"):
                answers[k[2:]] = form.getlist(k)
        quiz_res = assess.grade_quiz(module, lab_id, answers)
    assess.sync_progress(user["username"], lab_id, module, quiz=quiz_res)

    if phase == "quiz" or not assess.has_checks(module):
        return JSONResponse({"quiz": quiz_res, "written": written_res, "job_id": None})

    def done(job):
        res = assess.read_checks_result(job.lab_id, job.module)
        if res:
            assess.sync_progress(job.user, job.lab_id, module, checks=res)
        state.record(job.lab_id, "check", job.stage, job.status == "ok", None, job.id)

    try:
        job = await runner.submit(lab_id, "check", module["stage"], None,
                                  user["username"], on_done=done, module=module["id"])
    except jobs.Locked as e:
        # **여기가 인계 보고서가 성립하는 자리다.**
        # 시간이 끝나 랩 검사는 못 돌리지만 퀴즈와 서술 제출은 이미 저장됐다.
        # 오류가 아니라 정상 경로이므로 200 으로 돌려주고 확정본을 보게 한다.
        return JSONResponse({"quiz": quiz_res, "written": written_res, "job_id": None,
                             "locked": True, "note": e.message,
                             "exam": exam.view(lab_id, user)})
    except jobs.NotReady as e:
        # 퀴즈 채점과 서술형 제출은 이미 끝났고 저장됐다. 검사만 못 돌렸음을 분명히 알린다.
        return JSONResponse({"quiz": quiz_res, "written": written_res, "job_id": None,
                             "error": e.message, "health": e.health}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"quiz": quiz_res, "written": written_res, "job_id": None,
                             "error": f"{e} — 끝난 뒤 다시 제출할 것"}, status_code=409)
    return JSONResponse({"quiz": quiz_res, "written": written_res, "job_id": job.id})


@app.get("/m/{module_id}/result", response_class=HTMLResponse)
async def result(request: Request, module_id: str):
    user, redir = require(request)
    if redir:
        return redir
    module = docs.get(module_id)
    lab_id = pick_lab(user, None)
    ctx = base_ctx(request, user, lab_id)
    ctx["module"] = module
    ctx.update(_assess_ctx(user, lab_id, module))
    ctx.update(_tab_ctx(user, module))
    ctx["checks_result"] = assess.read_checks_result(lab_id, module_id)
    return tpl.TemplateResponse(request, "_result.html", ctx)


# ------------------------------------------------------------------ 이력
@app.get("/history", response_class=HTMLResponse)
async def history(request: Request, who: str = "", module: str = ""):
    user, redir = require(request)
    if redir:
        return redir
    target = user["username"]
    if who and who != user["username"]:
        if not auth.can(user, "user.manage"):
            return HTMLResponse("본인 이력만 볼 수 있습니다.", status_code=403)
        target = who
    return tpl.TemplateResponse(request, "history.html", {
        **nav_ctx(user, "history"),
        "user": user, "target": target,
        "attempts": db.list_attempts(target, module or None, 200),
        "progress": db.get_progress(target),
        "modules": {m["id"]: m for m in docs.modules()},
        "all_users": db.list_users() if auth.can(user, "user.manage") else [],
        "submissions": db.latest_submissions(target),
        "health": pve.last(),
        "site_name": L.SITE["site"]["name"],
    })


@app.get("/topology.svg")
async def topology(request: Request, stage: str = "m10"):
    user, redir = require(request)
    if redir:
        return redir
    if stage not in L.STAGES:
        stage = "m10"
    return HTMLResponse(topology_svg.render(stage), media_type="image/svg+xml")


@app.get("/status", response_class=HTMLResponse)
async def status(request: Request, lab: int | None = None):
    user, redir = require(request)
    if redir:
        return redir
    lab_id = pick_lab(user, lab)
    return tpl.TemplateResponse(request, "_status.html", base_ctx(request, user, lab_id))


HINT_LEVELS = {1: ("노림수", "어디를 볼 것인가"),
               2: ("증상", "되는 것과 안 되는 것")}


def _drill_hint(st, level):
    """단계별 힌트 한 개. 정답 줄은 여기 오지 않는다.

    본문은 교재와 같은 규칙으로 그린다 — `**굵게**` 를 그대로 두면
    화면에 별표가 보인다 (교재에서 이미 한 번 겪은 일이다).
    """
    key, title = HINT_LEVELS.get(level, (None, None))
    if not key:
        return None
    texts = [t for t in (jobs.scenario_doc(x).get(key) for x in (st.get("broken") or [])) if t]
    return {"title": title,
            "text": str(_labtext(" / ".join(texts))) if texts
                    else "이 시나리오에는 힌트가 없습니다."}


def _drill_passed(job):
    """중간 점검 판정. run-checks 는 언제나 0 으로 끝나므로 결과 파일을 읽는다."""
    if job.action != "drill-check" or job.status != "ok" or not job.module:
        return False
    res = assess.read_checks_result(job.lab_id, job.module)
    return bool(res and res.get("passed"))


def _job_done(j):
    state.record(j.lab_id, j.action, j.stage, j.status == "ok", j.scenario, j.id)
    # 검사를 전부 통과했다 = 스스로 고쳤다. 정답을 보여 주지 않고 끝낸다.
    if _drill_passed(j):
        state.drill_solved(j.lab_id)


# ------------------------------------------------------------------ 중간 점검
@app.post("/drill/hint")
async def drill_hint(request: Request, lab: int = Form(...)):
    """힌트를 한 단계씩. **정답 줄은 절대 내보내지 않는다.**

    힌트는 시나리오 파일 머리말에서 온다 — 교재와 따로 관리하면 한쪽이 낡는다.
      1단계 노림수 : 어디를 어떻게 볼 것인가 (관점)
      2단계 증상   : 되는 것과 안 되는 것의 대비 (범위)
    """
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    if lab not in auth.allowed_labs(user):
        return JSONResponse({"error": "이 랩에 대한 권한이 없습니다"}, status_code=403)
    st = state.load(lab)
    if not (st.get("blind") and st.get("broken")):
        return JSONResponse({"error": "진행 중인 중간 점검이 없습니다"}, status_code=400)

    level = state.take_hint(lab)
    got = _drill_hint(st, level)
    if not got:
        return JSONResponse({"level": level, "done": True,
                             "text": "힌트는 여기까지입니다. "
                                     "[정답 보고 복구] 를 누르면 무엇이었는지 나옵니다."})
    return JSONResponse({"level": level, "done": False, **got})


# ------------------------------------------------------------------ 실행
@app.post("/action")
async def action(request: Request, lab: int = Form(...), action: str = Form(...),
                 stage: str = Form(...), scenario: str = Form(""),
                 module: str = Form("")):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    if user.get("must_change_password"):
        return JSONResponse({"error": "비밀번호를 먼저 변경해 주세요"}, status_code=403)
    cap = auth.ACTION_CAP.get(action)
    if not cap or not auth.can(user, cap):
        return JSONResponse({"error": f"'{action}' 권한이 없습니다"}, status_code=403)
    if lab not in auth.allowed_labs(user):
        return JSONResponse({"error": "이 랩에 대한 권한이 없습니다"}, status_code=403)
    # 중간 점검: 무엇을 주입할지 **서버가 고른다.** 교육생이 고르면 답을 아는 채로 시작한다.
    # 랩은 혼자 하는 것이라 옆 사람에게 문제를 내 달라고 할 수도 없다.
    secret = False
    if action == "drill":
        cp = exam.checkpoint_for(stage)
        if not cp:
            return JSONResponse({"error": f"{stage.upper()} 단계에는 중간 점검이 없습니다"},
                                status_code=400)
        try:
            scenario = ",".join(exam.drill_pick(cp, jobs.scenario_ids()))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        secret = True          # 로그에 시나리오 이름이 찍힌다. 응시자에게는 가린다.
    if action == "drill-check":
        # 어느 모듈의 검사로 판정할지는 **서버가 정한다.** 화면이 보내는 값을 믿으면
        # 검사 항목이 적은 모듈을 골라 통과시킬 수 있다.
        st0 = state.load(lab)
        if not (st0.get("blind") and st0.get("broken")):
            return JSONResponse({"error": "진행 중인 중간 점검이 없습니다"}, status_code=400)
        cp = exam.checkpoint_for(stage) or next(
            (c for c in exam.drill_cfg()["checkpoints"]), None)
        module = (exam.checkpoint_module(cp) if cp else None) or module
        if not module:
            return JSONResponse({"error": "판정할 모듈을 찾지 못했습니다"}, status_code=400)
        # 검사 출력에는 실패한 항목 이름이 그대로 나온다 — 그게 곧 답이다. 가린다.
        secret = True
    try:
        job = await runner.submit(lab, action, stage, scenario or None,
                                  user.get("username"), on_done=_job_done,
                                  module=module or None, secret=secret)
    except jobs.Locked as e:
        # 423 Locked — 실패가 아니라 "지금은 잠겨 있다"는 뜻이다. 화면이 구분해서 보여준다.
        return JSONResponse({"error": e.message, "locked": True,
                             "exam": exam.view(lab, user)}, status_code=423)
    except jobs.NotReady as e:
        return JSONResponse({"error": e.message, "health": e.health}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"job_id": job.id})


@app.get("/jobs/{job_id}")
async def job_info(request: Request, job_id: str):
    """작업 하나의 상태와 진행률. 온보딩 화면이 몇 초마다 물어본다.

    로그 전체를 받는 스트림과 달리 아주 가볍다 — 진행 막대만 그리면 되는
    화면이 수천 줄을 받을 이유가 없다.
    """
    user = current_user(request)
    job = runner.jobs.get(job_id)
    if not user or not job or job.lab_id not in auth.allowed_labs(user):
        return JSONResponse({"error": "권한 없음"}, status_code=403)
    return JSONResponse(job.as_dict(reveal=False))


@app.get("/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str):
    user = current_user(request)
    job = runner.jobs.get(job_id)
    # 설치 작업(lab 0)은 배정된 랩이 아니다 — 관리자에게만 연다.
    allowed = (job and job.lab_id == jobs.SETUP_LAB and auth.can(user, "user.manage")) \
        or (job and job.lab_id in auth.allowed_labs(user))
    if not user or not allowed:
        return JSONResponse({"error": "권한 없음"}, status_code=403)

    reveal = auth.can(user, "lab.all")

    def sse(lines):
        """한 프레임에 여러 줄을 싣는다.

        전에는 한 줄에 프레임 하나였다. terraform apply 가 수천 줄을 쏟으면
        브라우저가 그만큼 콜백을 돌고 그만큼 DOM 을 건드려 탭이 굳었다.
        묶음 크기는 서버가 정하지 않는다 — 그 순간 쌓인 만큼이라 한가할 때는 한 줄이다.
        """
        if isinstance(lines, str):
            lines = [lines]
        return f"data: {json.dumps(lines, ensure_ascii=False)}\n\n"

    async def gen():
        # 시험 문제(주입한 시나리오)는 실행 로그에 그대로 찍힌다.
        # 응시자에게는 진행 사실만 알리고 내용은 보내지 않는다 — 관리자는 그대로 본다.
        if job.secret and not reveal:
            # 판정만 흘린다. 검사 출력에는 실패한 항목 이름이 그대로 나오는데,
            # 중간 점검에서 그것은 답을 알려 주는 것과 같다.
            if job.action == "drill-check":
                yield sse("$ 검사를 돌립니다 — 어느 항목인지는 알려 드리지 않습니다")
                while job.status not in ("ok", "failed"):
                    await asyncio.sleep(0.5)
                res = assess.read_checks_result(job.lab_id, job.module) or {}
                tot, okc = res.get("total", 0), res.get("ok", 0)
                if job.status != "ok" or not tot:
                    yield sse("!! 검사를 돌리지 못했습니다 — 교육 담당자에게 알려 주세요")
                elif res.get("passed"):
                    yield sse(f"== {okc}/{tot} 통과. 해결했습니다 — 정답을 보지 않고 끝냈습니다.")
                else:
                    yield sse(f"== 아직입니다. {okc}/{tot} 통과.")
                    yield sse("   막혔으면 [힌트] 를 눌러 주세요. 한 단계씩 나옵니다.")
            else:
                drill = job.action == "drill"
                yield sse("$ " + ("중간 점검 준비 중" if drill else "시험 준비 중")
                          + " — 랩을 초기화하고 장애를 주입합니다")
                yield sse("   (무엇을 주입했는지는 보이지 않습니다)")
                while job.status not in ("ok", "failed"):
                    await asyncio.sleep(0.5)
                if job.status != "ok":
                    yield sse("!! 준비 실패 — 교육 담당자에게 알려 주세요")
                elif drill:
                    yield sse("== 준비 완료. 증상부터 확인해 주세요 — 어디까지 되고 어디부터 안 되는가.")
                else:
                    yield sse("== 준비 완료. 지금부터 시간이 갑니다.")
        else:
            async for chunk in runner.stream(job_id):
                if not chunk:
                    # keep-alive. 전에는 빈 줄을 그대로 보냈고 화면은 그것을 버렸다 —
                    # 조용한 구간에 아무것도 안 보여서 멈춘 것처럼 읽혔다.
                    # 이제 "살아 있다 + 얼마나 조용했다" 를 같이 보낸다.
                    el, q = job.quiet()
                    yield ("event: tick\ndata: "
                           + json.dumps({"elapsed": el, "quiet": q}, ensure_ascii=False)
                           + "\n\n")
                else:
                    yield sse(chunk)
        d = json.dumps(job.as_dict(reveal=reveal), ensure_ascii=False)
        yield f"event: done\ndata: {d}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ 부록
@app.get("/labmap", response_class=HTMLResponse)
async def labmap(request: Request):
    """랩 지도. 교재·과제가 상시 참조하라고 하는 문서다."""
    user, redir = require(request)
    if redir:
        return redir
    # 설계 파일에서 그 자리에 만든다 — 관리자가 미리 돌려 둘 것이 없다.
    # 배정된 랩 기준이라 옆 랩 주소가 섞이지 않는다.
    lab_id = pick_lab(user) or 1
    md = docs.lab_map(lab_id)
    return tpl.TemplateResponse(request, "labmap.html", {
        **nav_ctx(user, "labmap", lab_id),
        "request": request, "user": user, "site_name": L.SITE["site"]["name"],
        "health": pve.last(), "pve": pve.public(),
        "lab_id": lab_id,
        "doc_html": Markup(docs.to_html(md)),
    })


@app.get("/appendix", response_class=HTMLResponse)
async def appendix(request: Request, doc: str = "", lab: int | None = None):
    """치트시트 · 플로우차트 · 용어집 · 벤더 CLI 매핑.

    모듈과 달리 **게이팅이 없다.** 부록은 시험 대상이 아니라 곁에 두고 보는 것이다.
    """
    user, redir = require(request)
    if redir:
        return redir
    lab_id = pick_lab(user, lab)
    docs_all = docs.appendix()
    if not docs_all:
        return HTMLResponse("부록이 아직 없습니다. <code>make appendix</code> 를 먼저 실행해 주세요.",
                            status_code=404)
    cur = docs.appendix_get(doc) or docs_all[0]
    text = docs.render_appendix(cur["id"], lab_id)
    return tpl.TemplateResponse(request, "appendix.html", {
        **nav_ctx(user, "appendix", lab_id),
        "request": request, "user": user, "lab_id": lab_id,
        "docs": docs_all, "doc": cur,
        "doc_html": docs.to_html(text) if text else "<p>문서를 찾을 수 없습니다.</p>",
        "health": pve.last(), "site_name": L.SITE["site"]["name"],
    })


# ------------------------------------------------------------------ 시험 세션
@app.post("/exam/start")
async def exam_start(request: Request, lab: int = Form(...), minutes: str = Form("")):
    """캡스톤 시작 — 랩을 초기화하고 **서버가 고른** 장애를 주입한 뒤 시계를 켠다.

    시나리오는 응시자에게 보이지 않는다. 고를 수 있게 하면 답을 아는 채로 시작한다.
    """
    user, redir = require(request)
    if redir:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    if lab not in auth.allowed_labs(user):
        return JSONResponse({"error": "이 랩에 대한 권한이 없습니다"}, status_code=403)
    if not auth.can(user, "lab.break"):
        return JSONResponse({"error": "시험을 시작할 권한이 없습니다"}, status_code=403)

    live = exam.current(lab)
    if live and exam.phase(live) in ("open", "overtime"):
        return JSONResponse({"error": "시험이 이미 진행 중입니다"}, status_code=409)
    why, module = exam.prepare(lab, user["username"])
    if why:
        return JSONResponse({"error": why}, status_code=400)

    picked = exam.pick(jobs.scenario_ids())
    mins = None
    if minutes.strip() and auth.can(user, "lab.all"):
        try:
            mins = max(1, min(600, int(minutes)))
        except ValueError:
            mins = None
    # 성적의 주인은 그 랩의 응시자다. 관리자가 대신 시작해도 관리자 이름으로 남으면 안 된다.
    owner = user["username"]
    if auth.can(user, "lab.all"):
        assigned = next((u["username"] for u in db.list_users()
                         if u.get("lab_id") == lab and u.get("role") == "user"), None)
        owner = (live or {}).get("username") or assigned or owner

    def opened(job):
        # 주입한 목록은 성공·실패와 무관하게 기록한다.
        # 실패한 회차의 잔재를 다음 회차의 fix 단계가 반드시 걷어내야 하기 때문이다.
        st = state.load(job.lab_id)
        st["broken"] = picked
        st["last_job"] = {"action": "exam", "stage": job.stage,
                          "ok": job.status == "ok", "scenario": None, "job_id": job.id}
        state.save(job.lab_id, st)
        if job.status == "ok":
            # 주입이 끝난 **뒤에** 시계를 켠다 — 준비 시간까지 시험 시간에 넣으면 불공정하다.
            db.exam_open(owner, job.lab_id, exam.module_id(), picked,
                         mins if mins is not None else exam.cfg()["minutes"])

    try:
        job = await runner.submit(lab, "exam", module["stage"], ",".join(picked),
                                  user["username"], on_done=opened, secret=True)
    except jobs.Locked as e:
        return JSONResponse({"error": e.message, "locked": True}, status_code=423)
    except jobs.NotReady as e:
        return JSONResponse({"error": e.message, "health": e.health}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"job_id": job.id, "faults": len(picked)})


@app.get("/exam/status")
async def exam_status(request: Request, lab: int | None = None):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    lab_id = pick_lab(user, lab)
    if lab_id is None:
        return JSONResponse({"error": "배정된 랩이 없습니다"}, status_code=403)
    return JSONResponse(exam.view(lab_id, user))


@app.get("/admin/exams", response_class=HTMLResponse)
async def exams_page(request: Request, msg: str = ""):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    rows = db.exam_list(50)
    for r in rows:
        r["phase"] = exam.phase(r)
    return tpl.TemplateResponse(request, "exams.html", {
        **nav_ctx(user, "exams"),
        "request": request, "user": user, "rows": rows, "msg": msg,
        "cfg": exam.cfg(), "pending": db.count_pending(), "health": pve.last(),
        "site_name": L.SITE["site"]["name"],
    })


@app.post("/admin/exams/{exam_id}/{op}")
async def exam_op(request: Request, exam_id: int, op: str, minutes: str = Form("10")):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    ex = db.exam_get(exam_id)
    if not ex:
        return RedirectResponse("/admin/exams?msg=없는 시험", status_code=303)
    if op == "close":
        # 확정은 스위퍼에 맡긴다. 시간 초과와 같은 경로여야 결과가 어긋나지 않는다.
        db.exam_expire(exam_id)
        note = "즉시 마감 — 곧 검사가 돌고 성적이 확정됩니다"
    elif op == "extend":
        try:
            m = max(1, min(600, int(minutes)))
        except ValueError:
            m = 10
        db.exam_extend(exam_id, m)
        note = f"{m}분 연장"
    elif op == "cancel":
        # 취소는 성적도 잠금도 남기지 않는다. 준비 실수를 되돌리는 용도다.
        db.exam_close(exam_id, "cancelled", None)
        note = "취소 — 성적도 잠금도 남지 않습니다"
    else:
        return RedirectResponse("/admin/exams?msg=알 수 없는 작업", status_code=303)
    return RedirectResponse(f"/admin/exams?msg={ex['username']} · lab{ex['lab_id']} {note}",
                            status_code=303)


# ------------------------------------------------------------------ 계정 관리
# ------------------------------------------------------------------ 설치
#  "설치하고 GUI 열면 그냥 쓸 수 있어야 한다" — 맞는 말이다.
#  남은 준비 작업을 화면 하나에 모으고, 콘솔이 스스로 할 수 있는 것은 버튼으로 만든다.
#  root 나 다른 호스트가 필요한 것만 명령으로 보여 준다 (복사 버튼과 함께).
def _setup_buttons(jump_ready):
    b = [{**x, "what": _access_what(jump_ready) if x["action"] == "setup-access" else x["what"]}
         for x in SETUP_BUTTONS]
    if jump_ready:
        b.insert(0, {
            "action": "setup-jump-apply", "label": "점프 계정 적용",
            "what": ("콘솔에 등록된 교육생 키로 운영 서버의 점프 계정을 만들고 "
                     "sshd 제한을 갱신합니다. 교육생을 추가하거나 키를 바꾼 뒤에 누릅니다. "
                     "빠진 사람은 만들고, 콘솔에서 사라진 사람은 접근을 회수합니다."),
            "need": "install.sh 가 설치하는 root 헬퍼"})
    return b


def _access_what(jump_ready):
    """점프 계정 스크립트는 root 헬퍼가 없을 때만 쓰인다.

    헬퍼가 설치돼 있으면 [점프 계정 적용] 버튼이 같은 일을 직접 한다.
    그런데도 "점프 계정 스크립트를 만든다" 라고 적어 두면, 관리자가 dist/ 의
    스크립트를 손으로 돌리는 **두 번째 root 경로**를 만든다 — 두 길이 갈라진다.
    """
    if jump_ready:
        return ("Proxmox 콘솔 계정 스크립트를 dist/ 에 만듭니다. "
                "점프 계정은 위 [점프 계정 적용] 이 직접 처리하므로 스크립트가 필요 없습니다.")
    return ("점프 계정 스크립트와 Proxmox 콘솔 계정 스크립트를 dist/ 에 만듭니다. "
            "만들기만 하고 적용하지 않습니다 — 적용은 아래 root 절차입니다.")


SETUP_BUTTONS = [
    {"action": "setup-mgmt", "label": "관리망 브리지 만들기",
     "what": "Proxmox 에 VLAN 브리지 1개를 만듭니다. 전 랩 공용이라 최초 1회면 됩니다.",
     "need": "Proxmox 연결"},
    {"action": "setup-access", "label": "교육생 접속 파일 만들기",
     "what": "",       # jump_ready 에 따라 _setup_buttons 가 채운다
     "need": ""},
    {"action": "setup-docs", "label": "문서 생성",
     "what": "교재·부록·랩 지도·접속 안내를 dist/ 에 파일로 뽑습니다. "
             "웹 화면은 이것 없이도 나옵니다 — 인쇄·배포용입니다.",
     "need": ""},
]


_JUMP_READY = {"at": 0.0, "val": False}
_JUMP_READY_TTL = 60.0


def _jump_apply_ready(force=False):
    """콘솔이 점프 계정을 직접 적용할 수 있는가.

    **실제로 한 번 실행해 본다.** 예전에는 `sudo -n -l HELPER` 로 물었는데,
    그건 "권한 목록을 보여 달라" 는 요청이라 sudo 의 verifypw 기본값(all) 아래에서는
    비밀번호를 요구한다 — 규칙이 NOPASSWD 로 제대로 걸려 있어도, 그 계정에
    비밀번호가 필요한 다른 sudo 규칙이 하나라도 있으면(예: sudo 그룹) 실패한다.
    그래서 install.sh 는 "설치됐다" 고 하는데 콘솔은 버튼을 영영 안 보여 줬다.

    --probe 는 헬퍼가 아무것도 읽지 않고 바로 끝내는 길이다. 화면을 그릴 때마다
    부르므로 결과를 잠깐 들고 있는다.
    """
    now = time.time()
    if not force and now - _JUMP_READY["at"] < _JUMP_READY_TTL:
        return _JUMP_READY["val"]
    val = False
    if Path(jobs.JUMP_HELPER).exists():
        try:
            r = subprocess.run(["sudo", "-n", jobs.JUMP_HELPER, "--probe"],
                               capture_output=True, text=True, timeout=5)
            val = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            val = False
    _JUMP_READY.update(at=now, val=val)
    return val


def _setup_manual(jump_ready=False):
    """콘솔이 대신 할 수 없는 절차. 왜 못 하는지까지 같이 적는다."""
    root = str(L.ROOT)
    node = L.SITE["access"]["proxmox"].get("node_name", "<노드>")
    return [
        {"title": "Proxmox 권한·API 토큰",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": "Proxmox 자신의 계정을 만드는 일이라 API 로는 할 수 없습니다. 최초 1회.",
         "cmd": "./infra/proxmox-setup.sh"},
        {"title": "골든 템플릿 (VMID 9000)",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": "디스크 이미지를 내려받아 가공합니다. 최초 1회.",
         "cmd": "./infra/template/build-golden-template.sh --storage local-lvm"},
        {"title": "이 서버를 관리망에 연결",
         "where": "이 운영 서버, sudo",
         "why": "netplan 을 쓰는 데 root 가 필요합니다. 콘솔은 root 로 돌지 않습니다. 최초 1회.",
         "cmd": f"cd {root} && make mgmt-net"},
        *([] if jump_ready else [
            {"title": "점프 계정 적용",
             "where": "이 운영 서버, sudo",
             "why": ("OS 계정과 sshd 설정을 건드립니다. 교육생이 늘 때마다 필요합니다. "
                     "아래 한 줄을 한 번 실행해 두면 이 일이 위쪽 버튼으로 바뀝니다 — "
                     f"cd {root} && ./install.sh --no-apt"),
             "cmd": (f"cd {root} && sudo ./dist/jump-access.sh\n"
                     "sudo cp dist/jump-access.conf /etc/ssh/sshd_config.d/60-lab-jump.conf\n"
                     "sudo sshd -t && sudo systemctl reload ssh")}]),
        {"title": "Proxmox 콘솔 계정 적용",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": ("pveum 은 Proxmox 호스트에만 있습니다. 랩당 1계정이라 "
                 "교육생이 늘어도 다시 할 필요는 없습니다 — 랩을 늘릴 때만 합니다. "
                 "비밀번호는 교육생이 [접속 키] 화면에서 직접 봅니다."),
         "cmd": "./console-access.sh          # dist/ 에서 복사해 온 파일"},
    ]


@app.get("/admin/setup", response_class=HTMLResponse)
async def admin_setup(request: Request, lab: int = 1, fresh: int = 0):
    # skip_setup: 이 화면은 **연결 설정을 확인하기 전에도** 열려야 한다.
    # 여기가 무엇이 안 됐는지 알려 주는 곳인데, 안 됐다는 이유로 튕기면 순환이다.
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return redir
    # make doctor 와 **같은 검사**를 부른다. 화면과 CLI 가 다른 말을 하면 안 된다.
    # 소켓을 쓰므로 이벤트 루프를 막지 않게 스레드로 돌린다.
    # 기본은 **다시 재지 않는다.** Proxmox 왕복(4초)과 도구 버전 확인(1초)은
    # 화면을 열 때마다 값이 달라지지 않는데, 그걸 매번 재느라 이 화면이 18초 걸렸다.
    # [다시 검사] 는 ?fresh=1 로 온다.
    checks = await asyncio.to_thread(preflight.collect, lab, False, bool(fresh))
    jump_ready = await asyncio.to_thread(_jump_apply_ready, bool(fresh))
    n = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for c in checks:
        if c["status"] in n:
            n[c["status"]] += 1
    return tpl.TemplateResponse(request, "setup.html", {
        **nav_ctx(user, "setup"),
        "user": user, "site_name": L.SITE["site"]["name"],
        "health": pve.last(), "pending": db.count_pending(),
        "checks": checks, "counts": n, "lab": lab,
        "buttons": _setup_buttons(jump_ready),
        "jump_stale": db.jump_stale_users(),
        "manual": _setup_manual(jump_ready), "jump_ready": jump_ready,
        "busy": runner.busy(jobs.SETUP_LAB),
    })


@app.post("/admin/setup/{action}")
async def admin_setup_run(request: Request, action: str):
    user = current_user(request)
    if not user or not auth.can(user, "user.manage"):
        return JSONResponse({"error": "권한이 없습니다"}, status_code=403)
    if action not in jobs.SETUP_ACTIONS:
        return JSONResponse({"error": f"모르는 작업: {action}"}, status_code=400)
    # 성공했을 때만 시각을 남긴다. 실패한 실행을 "반영했다" 로 기록하면
    # 그 뒤로 아무도 밀린 키를 눈치채지 못한다.
    # 시각은 **시작 시각**을 쓴다 — 헬퍼는 시작할 때의 DB 를 읽으므로,
    # 도는 동안 들어온 키는 아직 반영되지 않았다.
    done = None
    if action == "setup-jump-apply":
        started = await asyncio.to_thread(db.now_utc)
        done = lambda j: j.status == "ok" and (db.mark_jump_applied(started),   # noqa: E731
                                               autokey.clear(jump=True))
    try:
        job = await runner.submit(jobs.SETUP_LAB, action, "m10", None,
                                  user.get("username"), on_done=done)
    except jobs.NotReady as e:
        return JSONResponse({"error": e.message, "health": e.health}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"job_id": job.id})


# ------------------------------------------------------- 임시 비밀번호 한 번 보여주기
#  임시 비밀번호를 리다이렉트 URL 에 실으면 그 값이 브라우저 주소창·방문 기록·
#  uvicorn 접근 로그·중간 프록시 로그에 **평문으로 남는다.** 화면에서 지워도 로그에는 남는다.
#  그래서 URL 에는 뜻 없는 표만 싣고, 값은 이 프로세스의 메모리에 잠깐 둔다.
#    · 디스크에 쓰지 않는다 (var/console.db 에도 남기지 않는다)
#    · 한 번 꺼내면 사라진다 — 새로고침해도 다시 보이지 않는다
#    · 5분이 지나면 사라진다
#  콘솔은 uvicorn 단일 프로세스로 돈다(deploy/my-network-lab.service). 메모리로 충분하다.
_ONCE = {}
_ONCE_TTL = 300.0
_ONCE_MAX = 64


def _once_gc(now=None):
    now = now if now is not None else time.monotonic()
    for k in [k for k, (exp, _) in _ONCE.items() if exp <= now]:
        _ONCE.pop(k, None)


def once_put(payload):
    _once_gc()
    if len(_ONCE) >= _ONCE_MAX:                  # 오래된 것부터 버린다 (무한히 쌓이지 않게)
        for k in sorted(_ONCE, key=lambda k: _ONCE[k][0])[:len(_ONCE) - _ONCE_MAX + 1]:
            _ONCE.pop(k, None)
    tok = secrets.token_urlsafe(16)
    _ONCE[tok] = (time.monotonic() + _ONCE_TTL, payload)
    return tok


def once_take(tok):
    _once_gc()
    v = _ONCE.pop(tok or "", None)
    return v[1] if v else None


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, msg: str = "", err: str = "", once: str = ""):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    lo, _ = L.SITE["labs"]["id_range"]
    return tpl.TemplateResponse(request, "admin.html", {
        **nav_ctx(user, "users"),
        "user": user, "users": db.list_users(), "msg": msg, "err": err,
        "secret": once_take(once),
        "health": pve.last(), "pending": db.count_pending(),
        "labs": list(range(lo, L.SITE["labs"]["default_count"] + 1)),
        "policy": passwords.policy_text(),
        "role_label": auth.ROLE_LABEL,
        "site_name": L.SITE["site"]["name"],
    })


@app.post("/admin/users")
async def admin_create(request: Request, username: str = Form(...), name: str = Form(""),
                       role: str = Form("user"), lab_id: str = Form(""),
                       password: str = Form("")):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    username = username.strip()
    okname, why = auth.valid_username(username)
    if not okname:
        return RedirectResponse(f"/admin?err={why}", status_code=303)
    if db.get_user(username):
        return RedirectResponse(f"/admin?err=이미 있는 계정: {username}", status_code=303)
    if role not in ("admin", "user"):
        return RedirectResponse("/admin?err=알 수 없는 계정 종류", status_code=303)
    lab = int(lab_id) if lab_id else None
    if role == "user" and lab is None:
        return RedirectResponse("/admin?err=사용자 계정은 랩을 배정해야 합니다", status_code=303)
    pw = password or passwords.generate()
    errs = passwords.validate(pw, username)
    if errs:
        return RedirectResponse("/admin?err=" + " / ".join(errs), status_code=303)
    db.add_user(username, auth.hash_password(pw), role=role, lab_id=lab,
                name=name or username, must_change=True)
    tok = once_put({"username": username, "password": pw, "what": "생성됨"})
    return RedirectResponse(f"/admin?once={tok}", status_code=303)


@app.post("/admin/users/{target}/{op}")
async def admin_op(request: Request, target: str, op: str, lab_id: str = Form("")):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    t = db.get_user(target)
    if not t:
        return RedirectResponse("/admin?err=없는 계정", status_code=303)

    # 마지막 관리자를 잠그거나 지우지 못하게 막는다
    if t["role"] == "admin" and op in ("disable", "delete") and db.count_admins(exclude=target) == 0:
        return RedirectResponse("/admin?err=마지막 관리자 계정은 막거나 지울 수 없습니다", status_code=303)
    if target.lower() == user["username"].lower() and op in ("disable", "delete"):
        return RedirectResponse("/admin?err=자기 계정에는 할 수 없습니다", status_code=303)

    if op == "disable":
        db.set_disabled(target, True)
        m = f"{target} 차단됨 (진행 중인 세션도 즉시 막힙니다)"
    elif op == "enable":
        db.set_disabled(target, False)
        m = f"{target} 차단 해제"
    elif op == "delete":
        db.delete_user(target)
        m = f"{target} 삭제됨"
    elif op == "reset":
        pw = passwords.generate()
        db.set_password(target, auth.hash_password(pw), must_change=True)
        db.clear_failures(target)
        tok = once_put({"username": target, "password": pw, "what": "비밀번호 재발급"})
        return RedirectResponse(f"/admin?once={tok}", status_code=303)
    elif op == "unlock":
        db.clear_failures(target)
        m = f"{target} 로그인 잠금 해제"
    elif op == "lab":
        if t["role"] != "user":
            return RedirectResponse("/admin?err=관리자에게는 랩을 배정하지 않습니다", status_code=303)
        db.set_lab(target, int(lab_id))
        m = f"{target} → lab{lab_id} 재배정 (즉시 반영)"
    else:
        return RedirectResponse("/admin?err=알 수 없는 작업", status_code=303)
    # 차단·삭제·랩 이동은 **접근 권한이 바뀐 것**이다. 헬퍼가 없어진 사람의
    # 점프 계정을 회수하고 남은 사람의 키를 다시 쓴다. 안 걸면 차단된 계정이
    # 콘솔에서만 막히고 ssh 로는 그대로 들어온다.
    if op in ("disable", "enable", "delete", "lab"):
        autokey.request(t.get("lab_id"))
        if op == "lab" and lab_id:
            autokey.request(int(lab_id))
    return RedirectResponse(f"/admin?msg={m}", status_code=303)


# ------------------------------------------------------------- 서술형 검토 큐
@app.get("/admin/reviews", response_class=HTMLResponse)
async def reviews(request: Request, module: str = "", msg: str = ""):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    mods = {m["id"]: m for m in docs.modules()}
    rows = []
    for sub in db.pending_submissions(200, module or None):
        m = mods.get(sub["module_id"])
        item = next((i for i in assess.written_items(m, sub["lab_id"])
                     if i["id"] == sub["item_id"]), {}) if m else {}
        rows.append({**sub, "module": m, "item": item})
    return tpl.TemplateResponse(request, "reviews.html", {
        **nav_ctx(user, "reviews"),
        "user": user, "rows": rows, "modules": docs.modules(), "filter": module,
        "msg": msg, "pending": db.count_pending(), "health": pve.last(),
        "site_name": L.SITE["site"]["name"],
    })


@app.post("/admin/reviews/{sub_id}")
async def review_one(request: Request, sub_id: int, op: str = Form(...),
                     feedback: str = Form("")):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    sub = db.get_submission(sub_id)
    if not sub:
        return RedirectResponse("/admin/reviews?msg=없는 제출물", status_code=303)
    if op not in ("approve", "changes"):
        return RedirectResponse("/admin/reviews?msg=알 수 없는 작업", status_code=303)
    if op == "changes" and not feedback.strip():
        return RedirectResponse(
            "/admin/reviews?msg=재제출을 요청할 때는 무엇을 고쳐야 하는지 적어야 합니다",
            status_code=303)
    db.review_submission(sub_id, "approved" if op == "approve" else "changes_requested",
                         feedback.strip(), user["username"])
    # 승인으로 모듈이 통과됐을 수 있다 — 진도를 다시 계산한다.
    module = docs.get(sub["module_id"])
    if module:
        assess.sync_progress(sub["username"], sub["lab_id"], module)
    verb = "승인" if op == "approve" else "재제출 요청"
    return RedirectResponse(f"/admin/reviews?msg={sub['username']} · "
                            f"{sub['module_id'].upper()} {verb} 완료", status_code=303)


# --------------------------------------------------------------- Proxmox 상태
@app.get("/health/pve")
async def health_pve(request: Request, force: int = 0):
    """헤더의 상태 아이콘이 주기적으로 부른다. 로그인한 사람이면 누구나 볼 수 있다.

    교육생에게도 보여주는 이유: '내 잘못인가 랩이 죽은 건가' 를 스스로 구분하게 해야
    한다. 다만 주소·노드명 외의 내부 값은 내보내지 않는다.
    """
    u = current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    res = await asyncio.to_thread(pve.cached, bool(force))
    if not auth.can(u, "user.manage"):
        res = {**res, "checks": [{**c, "hint": ""} for c in res["checks"]]}
    return JSONResponse({**res, "can_fix": auth.can(u, "user.manage")})


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_form(request: Request, setup: int = 0, msg: str = "", err: str = ""):
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return redir
    return tpl.TemplateResponse(request, "settings.html", {
        **nav_ctx(user, "settings"),
        "user": user, "pve": pve.public(), "health": pve.last(),
        "setup": bool(setup) or not pve.confirmed(),
        "confirmed": pve.confirmed(), "errors": [], "msg": msg, "err": err,
        "template_vmid": L.SITE["labs"]["template_vmid"],
        "site_name": L.SITE["site"]["name"],
        "lab_range": L.SITE["labs"]["id_range"],
    })


@app.post("/admin/settings", response_class=HTMLResponse)
async def settings_save(request: Request, host: str = Form(...), port: str = Form("8006"),
                        node: str = Form(...), datastore: str = Form(...),
                        token_id: str = Form(""), token_secret: str = Form(""),
                        insecure_tls: str = Form(""), clear_token: str = Form(""),
                        lab_count: str = Form(""),
                        confirm: str = Form(""), force_confirm: str = Form("")):
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return redir
    values = {"host": host.strip(), "port": port.strip() or "8006",
              "node": node.strip(), "datastore": datastore.strip(),
              "token_id": token_id.strip(), "token_secret": token_secret.strip(),
              "insecure_tls": bool(insecure_tls), "clear_token": bool(clear_token)}
    if lab_count.strip():
        values["lab_count"] = lab_count.strip()
    errors = pve.save(values, user["username"])
    health = None
    if not errors:
        health = await asyncio.to_thread(pve.cached, True)     # 저장했으면 바로 확인한다
        if confirm:
            if health["ok"]:
                pve.mark_confirmed(user["username"])
                # 연결이 되면 다음 관문은 "환경이 준비됐는가" 다. 그 화면으로 바로 보낸다.
                # 여기서 설정 화면에 머물게 하면, 다음에 무엇을 해야 하는지를
                # 관리자가 문서에서 찾아내야 한다 — 그게 배포를 어렵게 만든다.
                return RedirectResponse("/admin/setup", status_code=303)
            if force_confirm:
                pve.mark_confirmed(user["username"], forced=True)
                return RedirectResponse(
                    "/admin/settings?msg=" + "점검을 통과하지 못한 채로 확인 처리했습니다. "
                    "랩 실행 시 다시 막힐 수 있습니다.", status_code=303)
            # 여기 오기 전에 pve.save 가 이미 끝났다. "다시 저장할 것" 이라고만 하면
            # 저장이 안 된 줄로 읽힌다 — 무엇이 됐고 무엇이 안 됐는지 나눠 말한다.
            errors = ["설정은 저장했습니다. 다만 연결 점검을 통과하지 못해 확인 처리는 하지 않았습니다. "
                      "아래 결과를 보고 고친 뒤 다시 저장하거나, "
                      "Proxmox 를 아직 켜지 않았다면 [점검 실패해도 확인 처리] 를 체크해 주세요"]
    return tpl.TemplateResponse(request, "settings.html", {
        **nav_ctx(user, "settings"),
        "user": user, "pve": {**pve.public(), **{k: v for k, v in values.items()
                                                 if k in ("host", "node", "datastore",
                                                          "token_id", "lab_count")}},
        "health": health, "setup": not pve.confirmed(),
        "confirmed": pve.confirmed(), "errors": errors, "msg": "", "err": "",
        "template_vmid": L.SITE["labs"]["template_vmid"],
        "site_name": L.SITE["site"]["name"],
        "lab_range": L.SITE["labs"]["id_range"],
    }, status_code=400 if errors else 200)


@app.post("/admin/settings/test")
async def settings_test(request: Request):
    """저장하지 않고 지금 값으로만 점검한다."""
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return JSONResponse({"error": "권한이 없습니다"}, status_code=403)
    form = await request.form()
    cfg = pve.config()
    probe = {**cfg,
             "host": (form.get("host") or cfg["host"]).strip(),
             "port": int((form.get("port") or cfg["port"]) or 8006),
             "node": (form.get("node") or cfg["node"]).strip(),
             "datastore": (form.get("datastore") or cfg["datastore"]).strip(),
             "token_id": (form.get("token_id") or cfg["token_id"]).strip(),
             "insecure_tls": bool(form.get("insecure_tls")),
             # 저장 전 값으로 점검하므로 랩 개수도 지금 화면의 값을 쓴다 —
             # 그래야 [지금 만들기] 확인창의 VLAN 범위가 화면과 어긋나지 않는다.
             "lab_count": int((form.get("lab_count") or cfg["lab_count"]) or 1)}
    if (form.get("token_secret") or "").strip():
        probe["token_secret"] = form["token_secret"].strip()
    return JSONResponse(await asyncio.to_thread(pve.check, probe))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "modules": len(docs.modules())}
