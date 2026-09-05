"""설치 절차를 콘솔이 스스로 끝까지 진행한다.

관리자가 서버에 들어가 `make` 를 치는 일을 버튼으로 옮겼었다. 그런데 버튼은
**누군가 그 화면을 열어 보고 눌러야** 한다 — 교육생이 키를 등록해도, 랩을
하나 더 늘려도, 그 사실을 아는 사람이 화면을 열기 전까지는 아무 일도 일어나지
않는다. 그리고 화면을 안 연 사람에게는 무엇이 밀려 있는지 보이지도 않는다.

그래서 누르는 일 자체를 없앤다. 콘솔이 뜰 때 한 번, 그 뒤로 주기마다
"지금 상태"와 "있어야 할 상태"를 견주어 모자란 것만 스스로 채운다.

무엇을 채우는가 — 순서가 곧 의존 관계다
  ① 접속 파일     dist/console-access.sh 를 지금 랩 수에 맞게 만든다
  ② 관리망 브리지  Proxmox 에 없으면 만든다
  ③ 관리망 연결    이 서버를 그 브리지에 붙인다
  ④ 점프 계정     아직 반영되지 않은 키가 있으면 반영한다
  ⑤ 콘솔 계정 권한  교육생이 자기 랩 VM 을 볼 수 있는지 **재기만** 한다

무엇을 **안** 하는가
  · Proxmox 호스트에 **적용되지 않은 네트워크 변경**이 남아 있으면 ② 를 건너뛴다.
    브리지를 만들면 Proxmox 가 호스트 네트워크를 다시 읽고(ifreload), 그때
    남의 대기 중 변경까지 함께 적용된다 — 관계없는 VM 의 통신이 끊길 수 있다.
    이건 콘솔이 대신 판단할 일이 아니다. 정리한 뒤 누르라고 버튼만 남긴다.
  · Proxmox 연결이 확인되기 전에는 ②③ 을 하지 않는다. 토큰이 아직 없다.
  · root 헬퍼가 없으면 ③④ 를 하지 않는다. 눌러 봐야 비밀번호를 묻다가 실패한다.
  · ⑤ 는 고치지 않는다. 풀에 권한을 거는 것은 root 만 할 수 있다 —
    dist/console-access.sh 가 랩을 늘릴 때 한 번 하는 일이다. 여기서는 빠진 것을
    찾아 관리자 띠에 올린다. 로그인은 되고 화면만 비어 있는 증상이라, 알려 주지
    않으면 교육생이 신고할 때까지 아무도 모른다.

막힌 것은 감추지 않는다. 왜 못 했는지를 [설치] 화면이 그대로 보여 주고,
그 단계에만 버튼이 남는다. "자동으로 한다"고 말해 놓고 조용히 실패하는 것이
가장 나쁘다.
"""
import asyncio
import importlib.util
import sys
import time

import db
import jobs
import labdesign as L
import pve
import state

FIRST = 10.0            # 콘솔이 뜨고 이만큼 뒤에 첫 바퀴 (기동 중에는 건드리지 않는다)
EVERY = 300.0           # 그 뒤 주기
MAX_TRIES = 3           # 한 단계를 이만큼 시도하고 그만둔다 — 안 그러면 영원히 돈다

STEPS = ("access", "mgmt-bridge", "mgmt-net", "jump")
LABEL = {"access": "교육생 접속 파일", "mgmt-bridge": "관리망 브리지",
         "mgmt-net": "이 서버를 관리망에 연결", "jump": "점프 계정"}
# 막힌 단계에 남길 버튼. 없는 단계는 손으로도 할 것이 없다는 뜻이다.
BUTTON = {"access": "setup-access", "mgmt-bridge": "setup-mgmt",
          "mgmt-net": "setup-mgmt-net", "jump": "setup-jump-apply"}

# 교육생 콘솔 계정 권한이 빠진 랩. 콘솔이 고칠 수 없어(root 만 가능) 알리기만 한다.
#   랩 -> 왜. 화면마다 Proxmox 를 두드릴 수는 없으니 여기서 재어 두고 띠가 읽어 간다.
_acl_gap: dict = {}

_done: dict = {}        # 단계 -> 끝낸 시각
_blocked: dict = {}     # 단계 -> 왜 못 했는가
# 앞 단계가 안 끝나서 못 한 것. 사유는 보여 주되 **버튼은 주지 않는다** —
# 눌러 봐야 같은 이유로 실패한다. 앞 단계를 고치면 이쪽은 저절로 풀린다.
_waiting: set = set()
_tries: dict = {}
_running = False
_ran_at = ""

# 헬퍼를 쓸 수 있는지 묻는 방법. app 이 꽂는다 — 판단은 한 곳에만 둔다.
ready_jump = None
ready_mgmt = None


def _log(msg):
    print(f"[setup-auto] {msg}", file=sys.stderr)


def status():
    """[설치] 화면이 그대로 보여 준다."""
    return {
        "running": _running, "ran_at": _ran_at,
        "steps": [{"id": s, "label": LABEL[s], "done": _done.get(s),
                   "blocked": _blocked.get(s), "waiting": s in _waiting,
                   "action": BUTTON[s]}
                  for s in STEPS],
        # 화면이 버튼을 다는 것들. 순서는 절차 순서 그대로다.
        "blocked": {s: _blocked[s] for s in STEPS
                    if _blocked.get(s) and s not in _waiting},
    }


def _mark(step, ok, why="", button=True, tried=False):
    """단계 하나의 결과를 적는다.

    두 가지를 따로 적는다. 섞으면 둘 다 틀린다.
      button — 사람이 지금 누를 수 있는 일인가. 앞 단계를 기다리는 중이거나
               root 헬퍼가 없어서 못 하는 것은 눌러 봐야 같은 이유로 실패한다.
      tried  — **실제로 돌려 보고** 실패했는가. 돌려 보지도 않은 것을 세면,
               잠깐 막혔던 사유가 풀린 뒤에도 자동 진행이 영영 안 돈다.
    """
    _waiting.discard(step)
    if ok:
        _done[step] = time.strftime("%Y-%m-%d %H:%M:%S")
        _blocked.pop(step, None)
        _tries.pop(step, None)
        return
    _blocked[step] = why
    if not button:
        _waiting.add(step)
    if tried:
        _tries[step] = _tries.get(step, 0) + 1


def _spent(step):
    """이 단계를 이미 충분히 시도했는가. 사유가 안 풀리는데 계속 두드리지 않는다."""
    return _tries.get(step, 0) >= MAX_TRIES


# ------------------------------------------------------------------ ① 접속 파일
def _gen_console_access():
    """dist/console-access.sh 를 지금 랩 수에 맞게 만든다.

    작업 큐를 쓰지 않는다 — 파일 하나를 쓰는 일이라 랩 잠금이 필요 없고,
    Proxmox 도 부르지 않는다. 랩마다 계정 비밀번호가 없으면 그 자리에서 만든다:
    **랩 수는 처음부터 알고 있으므로** 누가 버튼을 누를 때까지 기다릴 이유가 없다.
    """
    src = L.ROOT / "tools/gen-console-access.py"
    spec = importlib.util.spec_from_file_location("gen_console_access", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(None, str(L.ROOT / "dist"))


async def _step_access():
    if _spent("access"):
        return
    try:
        await asyncio.to_thread(_gen_console_access)
        _mark("access", True)
    except (Exception, SystemExit) as e:                    # noqa: BLE001
        _mark("access", False, f"{type(e).__name__}: {e}", tried=True)
        _log(f"접속 파일을 만들지 못했다: {e}")


# ------------------------------------------------------------------ 작업 큐 단계
async def _run(runner, step, action, why_busy="다른 설치 작업이 도는 중이다"):
    """설치 작업 하나를 걸고 끝날 때까지 기다린다."""
    try:
        job = await runner.submit(jobs.SETUP_LAB, action, "m11", None, None)
    except jobs.NotReady as e:
        _mark(step, False, getattr(e, "message", None) or str(e), tried=True)
        return False
    except (RuntimeError, ValueError) as e:                 # 잠김 · 잘못된 요청
        _mark(step, False, f"{why_busy} ({e})", tried=True)
        return False
    while job.status in ("queued", "running"):
        await asyncio.sleep(1.0)
    if job.status == "ok":
        _mark(step, True)
        return True
    _mark(step, False, f"작업이 실패했다 (종료 코드 {job.rc})", tried=True)
    _log(f"{action} 실패 (rc={job.rc})")
    return False


# ------------------------------------------------------------------ 한 바퀴
async def once(runner):
    """모자란 것만 채운다. 이미 되어 있는 것은 건드리지 않는다."""
    global _running, _ran_at
    _running = True
    _ran_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        await _step_access()

        # --- Proxmox 가 필요한 두 단계 -------------------------------------
        if not pve.confirmed():
            for s in ("mgmt-bridge", "mgmt-net"):
                _mark(s, False, "Proxmox 연결이 아직 확인되지 않았다", button=False)
        else:
            await _step_mgmt(runner)

        await _step_jump(runner)
        await _step_console_acl()
    finally:
        _running = False


async def _step_mgmt(runner):
    try:
        pf = await asyncio.to_thread(pve.preflight, 1)
    except Exception as e:                                  # noqa: BLE001
        _mark("mgmt-bridge", False, f"Proxmox 를 읽지 못했다: {type(e).__name__}")
        return
    by = {c["id"]: c for c in pf.get("checks", [])}
    bridge = by.get("mgmt-bridge", {})
    pending = by.get("pending", {})

    if bridge.get("status") == "ok":
        _mark("mgmt-bridge", True)
    elif pending.get("status") == "warn":
        # 여기서 멈추는 것이 이 파일의 핵심이다. 위 머리말 참고.
        _mark("mgmt-bridge", False,
              "Proxmox 호스트에 적용되지 않은 네트워크 변경이 남아 있다 — "
              "브리지를 만들면 그 변경까지 함께 적용된다. "
              "Proxmox 화면에서 먼저 정리한 뒤 아래 버튼으로 만들 것")
    elif "이미 다른 용도" in (bridge.get("detail") or ""):
        # 이름이 남의 것과 겹친다. 콘솔이 정할 수 없다 — 사람이 이름을 바꿔야 한다.
        _mark("mgmt-bridge", False, bridge.get("detail") or "브리지 이름이 겹친다")
    elif not _spent("mgmt-bridge"):
        await _run(runner, "mgmt-bridge", "setup-mgmt")

    # 브리지가 있어야 이 서버를 붙일 수 있다.
    if not _done.get("mgmt-bridge"):
        _mark("mgmt-net", False, "관리망 브리지가 먼저다", button=False)
        return
    if pve.mgmt_attached():
        _mark("mgmt-net", True)
    elif ready_mgmt and not ready_mgmt():
        _mark("mgmt-net", False, "콘솔이 netplan 을 직접 쓸 수 없다 — "
              "./install.sh --no-apt 를 한 번 실행할 것", button=False)
    elif not _spent("mgmt-net"):
        await _run(runner, "mgmt-net", "setup-mgmt-net")


async def _step_jump(runner):
    """밀린 키가 있으면 반영한다.

    autokey 가 등록·변경·삭제 **그 순간**에 이미 걸고 있다. 여기는 그것이
    빗나간 자리를 줍는다 — 콘솔이 꺼져 있는 동안 바뀐 키, 다른 작업과 겹쳐
    거절된 요청, 헬퍼를 나중에 설치한 서버. 이 그물이 없으면 그 키들은
    누군가 버튼을 누를 때까지 영영 반영되지 않는다.
    """
    if ready_jump and not ready_jump():
        _mark("jump", False, "콘솔이 점프 계정을 직접 적용할 수 없다 — "
              "./install.sh --no-apt 를 한 번 실행할 것", button=False)
        return
    stale = await asyncio.to_thread(db.jump_stale_users)
    if not stale:
        _mark("jump", True)
        return
    if _spent("jump"):
        return
    started = await asyncio.to_thread(db.now_utc)
    if await _run(runner, "jump", "setup-jump-apply"):
        await asyncio.to_thread(db.mark_jump_applied, started)


async def _step_console_acl():
    """교육생 콘솔 계정이 자기 랩 VM 을 볼 수 있는가.

    권한은 랩 풀(`/pool/labN`)에 걸려 있고 그것을 거는 일은 root 만 할 수 있다 —
    콘솔은 여기서 **고치지 못한다.** 그래서 재기만 하고, 빠진 랩은 관리자 띠에 올린다.
    조용히 두면 교육생이 신고할 때까지 아무도 모른다. 로그인은 되고 화면만 비어 있어서
    "Proxmox 가 이상하다" 로 잘못 짚기 쉬운 증상이다.
    """
    if not pve.confirmed():
        return
    gap = {}
    lo, _hi = L.SITE["labs"]["id_range"]
    for lab in range(lo, L.SITE["labs"]["default_count"] + 1):
        if not await asyncio.to_thread(state.provisioned, lab):
            continue
        ok = await asyncio.to_thread(pve.console_acl, lab)
        if ok is False:
            gap[lab] = (f"lab{lab} 교육생이 Proxmox 콘솔에 로그인해도 "
                        "VM 이 한 대도 보이지 않습니다")
    _acl_gap.clear()
    _acl_gap.update(gap)


def acl_gap():
    """권한이 빠진 랩. {랩: 사유}"""
    return dict(_acl_gap)


async def worker(runner):
    await asyncio.sleep(FIRST)
    while True:
        try:
            await once(runner)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            _log(f"예상 못한 오류(무시하고 계속): {e}")
        await asyncio.sleep(EVERY)
