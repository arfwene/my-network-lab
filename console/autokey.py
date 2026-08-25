"""등록된 접속 키를 점프 계정과 랩 노드에 자동으로 반영한다.

교육생이 첫 로그인에서 키를 넣으면 여기로 요청이 들어온다. 그런데 **바로 돌리지
않는다.** 잠깐 모았다가 한 번에 돌린다.

왜 모으는가
  수업 시작 때 열두 명이 동시에 키를 넣는다. 요청마다 작업을 띄우면 두 번째
  사람부터 Runner 가 "다른 작업이 실행 중이다" 로 거절하고, 그 사람의 키는
  **아무 데도 들어가지 않은 채 조용히 사라진다.**
  모아서 한 번 도는 것이 맞기도 하다 — 헬퍼도 플레이북도 등록된 **전원**의
  키를 한꺼번에 반영하기 때문이다. 열두 번 돌 이유가 없다.

여기서 하지 않는 것
  키가 **바뀌었을 때**는 건드리지 않는다. 그건 교육생이 [지금 랩에 반영] 을
  누른다. 실습이나 시험 도중에 랩 작업이 갑자기 뜨는 것을 원치 않아서다.
"""
import asyncio
import sys

import db
import jobs
import labdesign as L
import state

DEBOUNCE = 6.0      # 몰려 드는 등록을 한 번으로 모으는 시간
COOLDOWN = 45.0     # 한 바퀴 돈 뒤 최소 간격. 되풀이 요청의 바닥이다
POLL = 1.0
MAX_TRIES = 3       # 점프 반영 재시도 한계

_jump = False
_tries = 0
_labs: set[int] = set()
_wake = asyncio.Event()
_running = False
# 마지막 반영이 실패한 이유. 'jump' 또는 랩 번호가 열쇠다.
# 화면이 "자동으로 반영한다"고 말해 놓고 조용히 실패하면 그게 제일 나쁘다.
_fail: dict = {}
# 점프 헬퍼를 쓸 수 있는가. app 이 _jump_apply_ready 를 꽂는다 —
# sudoers 규칙이 없으면 눌러 봐야 비밀번호를 묻다가 실패한다.
ready = None


def _log(msg):
    print(f"[autokey] {msg}", file=sys.stderr)


def request(lab_id):
    """이 랩 교육생의 키를 반영해야 한다고 표시한다. 실행은 워커가 한다."""
    global _jump, _tries
    _jump = True
    _tries = 0
    _fail.pop("jump", None)
    if lab_id:
        _labs.add(int(lab_id))
        _fail.pop(int(lab_id), None)
    _wake.set()


def pending(lab_id=None):
    """아직 반영을 기다리고 있는가 — 화면이 '반영 중' 을 띄우는 근거."""
    if _running:
        return True
    if _jump:
        return True
    return bool(lab_id) and int(lab_id) in _labs


def failures(lab_id=None):
    """마지막 자동 반영이 실패한 것들. 화면이 그대로 보여 준다."""
    out = []
    if _fail.get("jump"):
        out.append(("점프 계정(운영 서버)", _fail["jump"]))
    if lab_id and _fail.get(int(lab_id)):
        out.append(("랩 노드", _fail[int(lab_id)]))
    return out


def clear(lab_id=None, jump=False):
    """손으로 걸어서 해결했으면 실패 기록도 같이 지운다.

    안 지우면 교육생 화면이 "자동 반영이 되지 않았다" 를 계속 붙들고 있다 —
    이미 [지금 랩에 반영] 으로 들어갔는데도.
    """
    if jump:
        _fail.pop("jump", None)
    if lab_id:
        _fail.pop(int(lab_id), None)


def _take():
    global _jump
    jump, labs, _jump = _jump, set(_labs), False
    _labs.clear()
    return jump, labs


async def _wait(runner, job, key):
    while job.status in ("queued", "running"):
        await asyncio.sleep(POLL)
    if job.status == "ok":
        _fail.pop(key, None)
        return True
    _fail[key] = f"작업이 실패했다 (종료 코드 {job.rc})"
    _log(f"{job.action} 실패 (rc={job.rc}) — 교육생은 화면의 버튼으로 다시 걸 수 있다")
    return False


async def _jump_apply(runner):
    global _tries, _jump
    if ready and not ready():
        # 관리자가 install.sh --jump-apply 를 아직 안 했다. 화면이 그 사실을
        # 이미 말하고 있으므로 여기서는 조용히 넘어간다 (매번 로그를 채우지 않는다).
        _fail["jump"] = "콘솔이 아직 점프 계정을 직접 적용할 수 없다 (관리자 설치 필요)"
        return
    # **시작 시각**을 적는다. 헬퍼는 시작할 때의 DB 를 읽으므로, 도는 동안
    # 등록된 키는 반영되지 않았다. 끝난 시각으로 적으면 그 키를 삼킨다.
    started = await asyncio.to_thread(db.now_utc)
    try:
        job = await runner.submit(jobs.SETUP_LAB, "setup-jump-apply", "m10", None, None,
                                  on_done=lambda j: j.status == "ok" and db.mark_jump_applied(started))
    except Exception as e:                                  # noqa: BLE001
        _fail["jump"] = str(e)
        _tries += 1
        if _tries < MAX_TRIES:
            # 대개 다른 설치 작업이 도는 중이다. 몇 번만 다시 해 본다 —
            # 무한히 다시 걸면 사유가 안 풀릴 때 로그와 화면이 영원히 돈다.
            _jump = True
            _wake.set()
        else:
            _log(f"점프 계정 반영을 {MAX_TRIES}번 시도했으나 걸지 못했다 — 그만둔다")
        _log(f"점프 계정 반영을 걸지 못했다: {e}")
        return
    _tries = 0
    await _wait(runner, job, "jump")


async def _lab_keys(runner, lab_id):
    # 지금 랩에 올라가 있는 단계 그대로. keys 는 설정을 건드리지 않지만
    # 인벤토리를 만들 때 단계가 필요하다.
    stage = state.load(lab_id).get("stage") or L.STAGES[0]
    try:
        job = await runner.submit(lab_id, "keys", stage, None, None)
    except Exception as e:                                  # noqa: BLE001
        # 랩이 아직 없거나(pve.gate), 시험 중이거나(exam.gate), 바쁘다.
        # 되풀이해 두드리지 않는다 — 교육생 화면에 사유와 수동 버튼이 남는다.
        _fail[int(lab_id)] = str(e)
        _log(f"lab{lab_id} 키 반영을 걸지 못했다: {e}")
        return
    await _wait(runner, job, int(lab_id))


async def worker(runner):
    global _running
    while True:
        await _wake.wait()
        await asyncio.sleep(DEBOUNCE)       # 이 사이에 들어온 요청까지 같이 처리한다
        _wake.clear()
        jump, labs = _take()
        _running = True
        try:
            if jump:
                await _jump_apply(runner)
            for lab in sorted(labs):
                await _lab_keys(runner, lab)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            _log(f"반영 중 예상 못한 오류(무시하고 계속): {e}")
        finally:
            _running = False
        await asyncio.sleep(COOLDOWN)
