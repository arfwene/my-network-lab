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
import assess, auth, db, docs, exam, jobs, passwords, pve, sshkeys, state, topology_svg  # noqa: E402

db.init()   # 스키마 생성 + (있다면) 예전 YAML 계정 이관
pve.sync()  # DB 에 저장된 Proxmox 접속 정보를 var/runtime.yml 로 다시 내보낸다

runner = jobs.Runner()
# 실행 관문에 시험 잠금을 꽂는다. 규칙은 exam.py 가 갖고 있고, 물어보는 자리는 여기 한 곳뿐이다.
runner.guard = exam.gate


@asynccontextmanager
async def lifespan(_app):
    """마감 스위퍼를 띄운다.

    콘솔이 꺼져 있는 동안 지난 마감도 기동 직후 첫 주기에서 확정된다 —
    마감이 프로세스 생존에 의존하면 "껐다 켜서 시간을 벌었다"가 가능해진다.
    """
    task = asyncio.create_task(exam.sweeper(runner))
    try:
        yield
    finally:
        task.cancel()


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
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
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
    """로그인 → 비밀번호 변경 강제 → 최초 연결 설정 확인 → 권한 확인 순.

    Proxmox 접속 정보 확인을 비밀번호 변경 바로 다음에 두는 이유:
    이 값이 틀리면 어떤 버튼도 동작하지 않는다. 교육생이 먼저 부딪히기 전에
    관리자가 한 번 보고 넘어가게 한다.
    """
    u = current_user(request)
    if not u:
        return None, RedirectResponse("/login", status_code=303)
    if u.get("must_change_password"):
        return None, RedirectResponse("/password", status_code=303)
    if not skip_setup and auth.can(u, "user.manage") and not pve.confirmed():
        return None, RedirectResponse("/admin/settings?setup=1", status_code=303)
    if cap and not auth.can(u, cap):
        return None, HTMLResponse("권한이 없다.", status_code=403)
    return u, None


def pick_lab(user, requested=None):
    labs = auth.allowed_labs(user)
    if not labs:
        return None
    if requested is not None and int(requested) in labs:
        return int(requested)
    return labs[0]


def base_ctx(request, user, lab_id):
    st = state.load(lab_id)
    mods = docs.modules()
    is_admin = auth.can(user, "lab.all")
    unlocked = assess.unlocked_modules(user["username"], is_admin)
    return {
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
        errors.append("새 비밀번호가 서로 다르다")
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


def _sshkey_ctx(request, user, errors=(), saved=""):
    lab_id = pick_lab(user)
    raw = (db.get_user(user["username"]) or {}).get("ssh_key") or ""
    node = L.TOPO["nodes"][0]["name"]
    A = L.IPAM["access"]
    return {"request": request, "user": user, "site_name": L.SITE["site"]["name"],
            "health": pve.last(), "pve": pve.public(),
            "lab_id": lab_id,
            "lab_label": f"lab {lab_id}" if lab_id else "미배정",
            "current": sshkeys.describe(raw) if raw else None,
            "key_at": (db.get_user(user["username"]) or {}).get("ssh_key_at"),
            "errors": list(errors), "saved": saved,
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
            "pve_url": L.IPAM["access"]["proxmox"]["api_endpoint"],
            "vm_name": L.vm_name(lab_id or 1, node)}


@app.get("/sshkey", response_class=HTMLResponse)
async def sshkey_form(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    return tpl.TemplateResponse(request, "sshkey.html", _sshkey_ctx(request, user))


@app.post("/sshkey", response_class=HTMLResponse)
async def sshkey_save(request: Request, key: str = Form(""), remove: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/password", status_code=303)
    if remove:
        db.set_ssh_key(user["username"], "")
        return tpl.TemplateResponse(
            request, "sshkey.html",
            _sshkey_ctx(request, user, saved="키를 지웠다. 다음 설정 적용 때 노드에서도 사라진다."))
    try:
        normalized = sshkeys.normalize(key)
    except sshkeys.Invalid as e:
        return tpl.TemplateResponse(request, "sshkey.html",
                                    _sshkey_ctx(request, user, errors=[str(e)]),
                                    status_code=400)
    db.set_ssh_key(user["username"], normalized)
    fp = sshkeys.fingerprint(normalized)
    return tpl.TemplateResponse(
        request, "sshkey.html",
        _sshkey_ctx(request, user, saved=f"저장했다 ({fp}). "
                                         f"[지금 랩에 반영] 을 누르거나 다음 [설정 적용] 때 들어간다."))


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
        return HTMLResponse("배정된 랩이 없다.", status_code=403)
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
    """지금 단계 그대로 설정만 다시 올린다 — 키 배포는 common 역할에 들어 있다."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    lab_id = pick_lab(user)
    if not lab_id or lab_id not in auth.allowed_labs(user):
        return RedirectResponse("/sshkey", status_code=303)
    stage = state.load(lab_id).get("stage") or L.STAGES[0]
    try:
        await runner.submit(lab_id, "apply", stage, None, user.get("username"),
                            on_done=lambda j: state.record(
                                j.lab_id, j.action, j.stage, j.status == "ok", j.scenario, j.id))
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
        return HTMLResponse("배정된 랩이 없다. 교육 담당자에게 문의할 것.", status_code=403)
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
        return "<p>문서가 없다.</p>"
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
        return HTMLResponse('<div class="notice">해설은 관리자만 볼 수 있다.</div>',
                            status_code=403)
    is_admin = auth.can(user, "lab.all")
    # 교재 → 과제 → 퀴즈. 화면에서 막는 것만으로는 부족하다(URL 로 건너뛸 수 있다).
    if not db.tab_allowed(user["username"], module_id, kind, is_admin):
        need = "교재" if kind == "tasks" else "과제"
        return HTMLResponse(
            f'<div class="notice"><b>{need}</b> 를 먼저 볼 것. '
            f'왼쪽에서 순서대로 진행한다 — 교재 → 과제 → 퀴즈·검증.</div>',
            status_code=403)
    db.mark_tab_seen(user["username"], module_id, kind)
    ctx = base_ctx(request, user, lab_id)
    if not ctx["unlocked"].get(module_id, False):
        return HTMLResponse(
            '<div class="notice">앞 모듈을 통과해야 열린다. '
            '앞 모듈에서 <b>제출하고 검증</b> 을 눌러 통과할 것.</div>', status_code=403)
    ctx["module"] = module
    ctx["kind"] = kind
    ctx["module_html"] = "" if kind == "quiz" else _module_html(module, lab_id, user, kind)
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
    }


# ------------------------------------------------------------------ 제출·검증
@app.post("/m/{module_id}/submit")
async def submit(request: Request, module_id: str):
    user, redir = require(request)
    if redir:
        return JSONResponse({"error": "로그인이 필요하다"}, status_code=401)
    module = docs.get(module_id)
    if not module:
        return JSONResponse({"error": "없는 모듈"}, status_code=404)
    lab_id = pick_lab(user, None)
    form = await request.form()

    # 서술형을 먼저 저장한다 — 진도 계산이 제출 여부를 보기 때문이다.
    written_res = None
    if assess.has_written(module):
        wans = {k[2:]: form.get(k) for k in form.keys() if k.startswith("w_")}
        written_res = assess.submit_written(user["username"], lab_id, module, wans)

    quiz_res = None
    if assess.has_quiz(module):
        answers = {}
        for k in form.keys():
            if k.startswith("q_"):
                answers[k[2:]] = form.getlist(k)
        quiz_res = assess.grade_quiz(module, lab_id, answers)
    assess.sync_progress(user["username"], lab_id, module, quiz=quiz_res)

    if not assess.has_checks(module):
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
            return HTMLResponse("본인 이력만 볼 수 있다.", status_code=403)
        target = who
    return tpl.TemplateResponse(request, "history.html", {
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


# ------------------------------------------------------------------ 실행
@app.post("/action")
async def action(request: Request, lab: int = Form(...), action: str = Form(...),
                 stage: str = Form(...), scenario: str = Form(""),
                 module: str = Form("")):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "로그인이 필요하다"}, status_code=401)
    if user.get("must_change_password"):
        return JSONResponse({"error": "비밀번호를 먼저 변경할 것"}, status_code=403)
    cap = auth.ACTION_CAP.get(action)
    if not cap or not auth.can(user, cap):
        return JSONResponse({"error": f"'{action}' 권한이 없다"}, status_code=403)
    if lab not in auth.allowed_labs(user):
        return JSONResponse({"error": "이 랩에 대한 권한이 없다"}, status_code=403)
    try:
        job = await runner.submit(lab, action, stage, scenario or None,
                                  user.get("username"),
                                  on_done=lambda j: state.record(
                                      j.lab_id, j.action, j.stage, j.status == "ok",
                                      j.scenario, j.id),
                                  module=module or None)
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

    def sse(line):
        return f"data: {json.dumps(line, ensure_ascii=False)}\n\n"

    async def gen():
        # 시험 문제(주입한 시나리오)는 실행 로그에 그대로 찍힌다.
        # 응시자에게는 진행 사실만 알리고 내용은 보내지 않는다 — 관리자는 그대로 본다.
        if job.secret and not reveal:
            yield sse("$ 시험 준비 중 — 랩을 초기화하고 장애를 주입한다")
            yield sse("   (무엇을 주입했는지는 보이지 않는다)")
            while job.status not in ("ok", "failed"):
                await asyncio.sleep(0.5)
            yield sse("== 준비 완료. 지금부터 시간이 간다." if job.status == "ok"
                      else "!! 준비 실패 — 관리자에게 알릴 것")
        else:
            async for line in runner.stream(job_id):
                yield sse(line)
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
        return HTMLResponse("부록이 아직 없다. <code>make appendix</code> 를 먼저 실행할 것.",
                            status_code=404)
    cur = docs.appendix_get(doc) or docs_all[0]
    text = docs.render_appendix(cur["id"], lab_id)
    return tpl.TemplateResponse(request, "appendix.html", {
        "request": request, "user": user, "lab_id": lab_id,
        "docs": docs_all, "doc": cur,
        "doc_html": docs.to_html(text) if text else "<p>문서를 찾을 수 없다.</p>",
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
        return JSONResponse({"error": "로그인이 필요하다"}, status_code=401)
    if lab not in auth.allowed_labs(user):
        return JSONResponse({"error": "이 랩에 대한 권한이 없다"}, status_code=403)
    if not auth.can(user, "lab.break"):
        return JSONResponse({"error": "시험을 시작할 권한이 없다"}, status_code=403)

    live = exam.current(lab)
    if live and exam.phase(live) in ("open", "overtime"):
        return JSONResponse({"error": "시험이 이미 진행 중이다"}, status_code=409)
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
        return JSONResponse({"error": "로그인이 필요하다"}, status_code=401)
    lab_id = pick_lab(user, lab)
    if lab_id is None:
        return JSONResponse({"error": "배정된 랩이 없다"}, status_code=403)
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
        note = "즉시 마감 — 곧 검사가 돌고 성적이 확정된다"
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
        note = "취소 — 성적도 잠금도 남지 않는다"
    else:
        return RedirectResponse("/admin/exams?msg=알 수 없는 작업", status_code=303)
    return RedirectResponse(f"/admin/exams?msg={ex['username']} · lab{ex['lab_id']} {note}",
                            status_code=303)


# ------------------------------------------------------------------ 계정 관리
# ------------------------------------------------------------------ 설치
#  "설치하고 GUI 열면 그냥 쓸 수 있어야 한다" — 맞는 말이다.
#  남은 준비 작업을 화면 하나에 모으고, 콘솔이 스스로 할 수 있는 것은 버튼으로 만든다.
#  root 나 다른 호스트가 필요한 것만 명령으로 보여 준다 (복사 버튼과 함께).
SETUP_BUTTONS = [
    {"action": "setup-mgmt", "label": "관리망 브리지 만들기",
     "what": "Proxmox 에 VLAN 브리지 1개를 만든다. 전 랩 공용이라 최초 1회면 된다.",
     "need": "Proxmox 연결"},
    {"action": "setup-access", "label": "교육생 접속 파일 만들기",
     "what": "점프 계정 스크립트와 Proxmox 콘솔 계정 스크립트를 dist/ 에 만든다. "
             "만들기만 하고 적용하지 않는다 — 적용은 아래 root 절차다.",
     "need": ""},
    {"action": "setup-docs", "label": "문서 생성",
     "what": "교재·부록·랩 지도·접속 안내를 dist/ 에 파일로 뽑는다. "
             "웹 화면은 이것 없이도 나온다 — 인쇄·배포용이다.",
     "need": ""},
]


def _setup_manual():
    """콘솔이 대신 할 수 없는 절차. 왜 못 하는지까지 같이 적는다."""
    root = str(L.ROOT)
    node = L.SITE["access"]["proxmox"].get("node_name", "<노드>")
    return [
        {"title": "Proxmox 권한·API 토큰",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": "Proxmox 자신의 계정을 만드는 일이라 API 로는 할 수 없다. 최초 1회.",
         "cmd": "./infra/proxmox-setup.sh"},
        {"title": "골든 템플릿 (VMID 9000)",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": "디스크 이미지를 내려받아 가공한다. 최초 1회.",
         "cmd": "./infra/template/build-golden-template.sh --storage local-lvm"},
        {"title": "이 서버를 관리망에 연결",
         "where": "이 운영 서버, sudo",
         "why": "netplan 을 쓰는 데 root 가 필요하다. 콘솔은 root 로 돌지 않는다. 최초 1회.",
         "cmd": f"cd {root} && make mgmt-net"},
        {"title": "점프 계정 적용",
         "where": "이 운영 서버, sudo",
         "why": "OS 계정과 sshd 설정을 건드린다. 교육생이 늘 때마다.",
         "cmd": (f"cd {root} && sudo ./dist/jump-access.sh\n"
                 "sudo cp dist/jump-access.conf /etc/ssh/sshd_config.d/60-lab-jump.conf\n"
                 "sudo sshd -t && sudo systemctl reload ssh")},
        {"title": "Proxmox 콘솔 계정 적용",
         "where": f"Proxmox 호스트({node}) 에서 root",
         "why": "pveum 은 Proxmox 호스트에만 있다. 교육생이 늘 때마다.",
         "cmd": "./console-access.sh          # dist/ 에서 복사해 온 파일"},
    ]


@app.get("/admin/setup", response_class=HTMLResponse)
async def admin_setup(request: Request, lab: int = 1):
    # skip_setup: 이 화면은 **연결 설정을 확인하기 전에도** 열려야 한다.
    # 여기가 무엇이 안 됐는지 알려 주는 곳인데, 안 됐다는 이유로 튕기면 순환이다.
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return redir
    # make doctor 와 **같은 검사**를 부른다. 화면과 CLI 가 다른 말을 하면 안 된다.
    # 소켓을 쓰므로 이벤트 루프를 막지 않게 스레드로 돌린다.
    checks = await asyncio.to_thread(preflight.collect, lab)
    n = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for c in checks:
        if c["status"] in n:
            n[c["status"]] += 1
    return tpl.TemplateResponse(request, "setup.html", {
        "user": user, "site_name": L.SITE["site"]["name"],
        "health": pve.last(), "pending": db.count_pending(),
        "checks": checks, "counts": n, "lab": lab,
        "buttons": SETUP_BUTTONS, "manual": _setup_manual(),
        "busy": runner.busy(jobs.SETUP_LAB),
    })


@app.post("/admin/setup/{action}")
async def admin_setup_run(request: Request, action: str):
    user = current_user(request)
    if not user or not auth.can(user, "user.manage"):
        return JSONResponse({"error": "권한이 없다"}, status_code=403)
    if action not in jobs.SETUP_ACTIONS:
        return JSONResponse({"error": f"모르는 작업: {action}"}, status_code=400)
    try:
        job = await runner.submit(jobs.SETUP_LAB, action, "m10", None,
                                  user.get("username"))
    except jobs.NotReady as e:
        return JSONResponse({"error": e.message, "health": e.health}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"job_id": job.id})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, msg: str = "", err: str = ""):
    user, redir = require(request, "user.manage")
    if redir:
        return redir
    lo, _ = L.SITE["labs"]["id_range"]
    return tpl.TemplateResponse(request, "admin.html", {
        "user": user, "users": db.list_users(), "msg": msg, "err": err,
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
    if not username or not username.replace("-", "").replace("_", "").isalnum():
        return RedirectResponse("/admin?err=아이디는 영문·숫자·-·_ 만 쓸 수 있다", status_code=303)
    if db.get_user(username):
        return RedirectResponse(f"/admin?err=이미 있는 계정: {username}", status_code=303)
    if role not in ("admin", "user"):
        return RedirectResponse("/admin?err=알 수 없는 계정 종류", status_code=303)
    lab = int(lab_id) if lab_id else None
    if role == "user" and lab is None:
        return RedirectResponse("/admin?err=사용자 계정은 랩을 배정해야 한다", status_code=303)
    pw = password or passwords.generate()
    errs = passwords.validate(pw, username)
    if errs:
        return RedirectResponse("/admin?err=" + " / ".join(errs), status_code=303)
    db.add_user(username, auth.hash_password(pw), role=role, lab_id=lab,
                name=name or username, must_change=True)
    return RedirectResponse(
        f"/admin?msg={username} 생성됨. 임시 비밀번호: {pw} (첫 로그인에서 변경해야 한다)",
        status_code=303)


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
        return RedirectResponse("/admin?err=마지막 관리자 계정은 막거나 지울 수 없다", status_code=303)
    if target.lower() == user["username"].lower() and op in ("disable", "delete"):
        return RedirectResponse("/admin?err=자기 계정에는 할 수 없다", status_code=303)

    if op == "disable":
        db.set_disabled(target, True)
        m = f"{target} 차단됨 (진행 중인 세션도 즉시 막힌다)"
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
        m = f"{target} 임시 비밀번호: {pw} (첫 로그인에서 변경해야 한다)"
    elif op == "unlock":
        db.clear_failures(target)
        m = f"{target} 로그인 잠금 해제"
    elif op == "lab":
        if t["role"] != "user":
            return RedirectResponse("/admin?err=관리자에게는 랩을 배정하지 않는다", status_code=303)
        db.set_lab(target, int(lab_id))
        m = f"{target} → lab{lab_id} 재배정 (즉시 반영)"
    else:
        return RedirectResponse("/admin?err=알 수 없는 작업", status_code=303)
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
            "/admin/reviews?msg=재제출을 요청할 때는 무엇을 고쳐야 하는지 적어야 한다",
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
        return JSONResponse({"error": "로그인이 필요하다"}, status_code=401)
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
        "user": user, "pve": pve.public(), "health": pve.last(),
        "setup": bool(setup) or not pve.confirmed(),
        "confirmed": pve.confirmed(), "errors": [], "msg": msg, "err": err,
        "template_vmid": L.SITE["labs"]["template_vmid"],
        "site_name": L.SITE["site"]["name"],
    })


@app.post("/admin/settings", response_class=HTMLResponse)
async def settings_save(request: Request, host: str = Form(...), port: str = Form("8006"),
                        node: str = Form(...), datastore: str = Form(...),
                        token_id: str = Form(""), token_secret: str = Form(""),
                        insecure_tls: str = Form(""), clear_token: str = Form(""),
                        confirm: str = Form(""), force_confirm: str = Form("")):
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return redir
    values = {"host": host.strip(), "port": port.strip() or "8006",
              "node": node.strip(), "datastore": datastore.strip(),
              "token_id": token_id.strip(), "token_secret": token_secret.strip(),
              "insecure_tls": bool(insecure_tls), "clear_token": bool(clear_token)}
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
                    "/admin/settings?msg=" + "점검을 통과하지 못한 채로 확인 처리했다. "
                    "랩 실행 시 다시 막힐 수 있다.", status_code=303)
            errors = ["점검을 통과하지 못했다. 아래 결과를 보고 고치거나, "
                      "[점검 실패해도 확인 처리] 를 체크하고 다시 저장할 것"]
    return tpl.TemplateResponse(request, "settings.html", {
        "user": user, "pve": {**pve.public(), **{k: v for k, v in values.items()
                                                 if k in ("host", "node", "datastore", "token_id")}},
        "health": health, "setup": not pve.confirmed(),
        "confirmed": pve.confirmed(), "errors": errors, "msg": "", "err": "",
        "template_vmid": L.SITE["labs"]["template_vmid"],
        "site_name": L.SITE["site"]["name"],
    }, status_code=400 if errors else 200)


@app.post("/admin/settings/test")
async def settings_test(request: Request):
    """저장하지 않고 지금 값으로만 점검한다."""
    user, redir = require(request, "user.manage", skip_setup=True)
    if redir:
        return JSONResponse({"error": "권한이 없다"}, status_code=403)
    form = await request.form()
    cfg = pve.config()
    probe = {**cfg,
             "host": (form.get("host") or cfg["host"]).strip(),
             "port": int((form.get("port") or cfg["port"]) or 8006),
             "node": (form.get("node") or cfg["node"]).strip(),
             "datastore": (form.get("datastore") or cfg["datastore"]).strip(),
             "token_id": (form.get("token_id") or cfg["token_id"]).strip(),
             "insecure_tls": bool(form.get("insecure_tls"))}
    if (form.get("token_secret") or "").strip():
        probe["token_secret"] = form["token_secret"].strip()
    return JSONResponse(await asyncio.to_thread(pve.check, probe))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "modules": len(docs.modules())}
