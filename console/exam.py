"""
시험 세션 — 제한 시간과 그 끝에서의 점수 동결.

두 층으로 되어 있다.

  1층 · 점수 동결
      마감 시각에 서버가 검사를 한 번 돌려 그 결과를 **확정본**으로 못 박는다.
      그 뒤로 랩을 어떻게 고쳐도 성적은 확정본 그대로다.

  2층 · 콘솔 잠금
      마감 뒤에는 그 랩의 실행 계열 작업을 거부한다.
      읽기 전용 확인(verify)만 남긴다 — 인계 보고서를 쓰려면 상태를 봐야 하기 때문이다.

**2층만으로는 잠금이 성립하지 않는다.** 교육생은 점프 호스트를 거쳐 노드에 SSH 로
직접 붙을 수 있고, 그 경로는 콘솔을 지나지 않는다. 게다가 랩 계정은 frr·frrvty
그룹에 들어 있어서 sudo 를 회수해도 vtysh 로 라우터를 고칠 수 있다.
그래서 1층이 본체이고 2층은 사고 방지 장치다 — **막는 대신 소용없게 만든다.**

시각은 전부 SQLite 의 UTC(datetime('now'))로 다룬다. 파이썬에서 만들어 넣으면
콘솔 프로세스의 시간대에 따라 마감이 어긋난다.
"""
import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L      # noqa: E402
import assess              # noqa: E402
import db                  # noqa: E402
import docs                # noqa: E402
import state               # noqa: E402

DEFAULTS = {"module": "m10", "minutes": 45, "faults": 3,
            "sweep_seconds": 20, "pool": []}

# 진행 중에도 허용 — 검사는 곧 제출이다.
OPEN_ALLOWED = {"verify", "check"}
# 마감 뒤에는 읽기 전용 확인만. 상태를 봐야 보고서를 쓸 수 있다.
CLOSED_ALLOWED = {"verify"}

# 마감 검사 하나를 기다려 주는 한계 시간(초).
CHECK_TIMEOUT = 600.0

# 마감 확정을 위해 스위퍼가 검사를 돌리는 동안만 채워지는 랩 번호.
# 이 순간에는 교육생의 요청이 Runner.busy 에서 먼저 걸리므로 끼어들 수 없다.
_closing: set[int] = set()


def cfg():
    c = dict(DEFAULTS)
    c.update((L.SITE.get("console") or {}).get("capstone") or {})
    return c


def module_id():
    return cfg()["module"]


def is_capstone(module):
    return bool(module) and module.get("id") == module_id()


# ---------------------------------------------------------------- 시나리오 선택
def pool(all_ids):
    """시험에 쓸 수 있는 시나리오. site.yml 에서 좁힐 수 있다.

    교육생이 고르면 답을 아는 상태로 시작하게 되므로 **서버가 고른다.**
    """
    want = cfg().get("pool") or []
    if not want:
        return list(all_ids)
    return [s for s in all_ids if s in want]


def _family(sid):
    """m06-03 -> m06. 같은 계층의 장애를 한 회차에 겹쳐 넣지 않기 위한 묶음."""
    return sid.split("-")[0]


def pick(all_ids, n=None):
    """무작위 n 개. **서로 다른 계층에서 하나씩** 뽑는다.

    secrets 를 쓴다 — 시드를 알면 문제가 새기 때문이다.

    계층을 겹치지 않게 하는 이유가 둘이다.
      ① 같은 서비스에 두 장애가 겹치면 서로를 지운다.
         m08-02 가 named 를 멈춘 뒤 m08-01 이 `state: restarted` 로 다시 켜 버린다 —
         주입은 성공했다고 나오는데 실제 장애는 하나뿐인 회차가 된다.
      ② 세 개가 전부 같은 계층이면 캡스톤이 그 계층 하나의 문제로 줄어든다.
         3-step 을 끝까지 밟게 하려면 층이 흩어져 있어야 한다.
    """
    cand = pool(all_ids)
    if not cand:
        raise ValueError("시험에 쓸 시나리오가 없다 — config/site.yml 의 "
                         "console.capstone.pool 을 확인할 것")
    n = max(1, int(n if n is not None else cfg()["faults"]))
    groups = {}
    for s in cand:
        groups.setdefault(_family(s), []).append(s)
    keys = list(groups)
    out = []
    while len(out) < n and keys:
        g = groups[keys.pop(secrets.randbelow(len(keys)))]
        out.append(g[secrets.randbelow(len(g))])
    # 묶음 수보다 많이 요구하면(설정이 과하게 잡혀 있을 때) 남은 것에서 채운다.
    rest = [s for s in cand if s not in out]
    while len(out) < n and rest:
        out.append(rest.pop(secrets.randbelow(len(rest))))
    return sorted(out)


# ------------------------------------------------------------------ 현재 상태
def current(lab_id):
    """그 랩의 가장 최근 세션. 취소된 것은 없는 것으로 본다.

    잠금 판단을 **최신 행 하나**로만 하는 것이 핵심이다.
    새 세션을 시작하면 이전 마감이 자동으로 효력을 잃는다 (재시도 무제한).
    """
    ex = db.exam_latest(lab_id=lab_id, module_id=module_id())
    if not ex or ex["cancelled"]:
        return None
    return ex


def phase(ex):
    if not ex:
        return "none"
    if ex["open"]:
        return "open" if ex["remaining"] > 0 else "overtime"
    return "closed"


def view(lab_id, user=None):
    """화면·JSON 공통 모델. 진행 중에는 시나리오를 절대 싣지 않는다."""
    ex = current(lab_id)
    ph = phase(ex)
    is_admin = bool(user) and user.get("role") == "admin"
    # 마감 뒤에는 무엇이었는지 알려 준다 — 종료 후 정답 공개가 학습의 절반이다.
    reveal = is_admin or ph == "closed"
    return {
        "phase": ph,
        "module": module_id(),
        "minutes": cfg()["minutes"],
        "faults": cfg()["faults"],
        "id": ex["id"] if ex else None,
        "owner": ex["username"] if ex else None,
        "remaining": max(0, ex["remaining"]) if ex else None,
        "deadline_at": ex["deadline_at"] if ex else None,
        "closed_at": ex["closed_at"] if ex else None,
        "closed_by": ex["closed_by"] if ex else None,
        "scenarios": (ex["scenarios"] if ex and reveal else None),
        "frozen": ex["frozen"] if ex and ph == "closed" else None,
        "ok": ex["ok"] if ex else None,
        "total": ex["total"] if ex else None,
        "passed": bool(ex["passed"]) if ex and ex["passed"] is not None else None,
        "hide_faults": bool(ex) and ph in ("open", "overtime") and not is_admin,
        "locked": ph in ("overtime", "closed") and not is_admin,
        # 지금 쓸 수 있는 실행 작업. None 이면 제한 없음.
        # 화면은 이 목록으로 버튼을 잠그고, 진짜 차단은 gate() 가 한다 —
        # 버튼만 잠그면 curl 한 번에 뚫린다.
        "allow": None if (is_admin or ph == "none") else
                 (sorted(OPEN_ALLOWED) if ph == "open" else sorted(CLOSED_ALLOWED)),
    }


# ------------------------------------------------------------------ 2층 · 관문
def gate(lab_id, action, username):
    """Runner.submit 이 부르는 유일한 관문. None 이 아니면 거부 사유다."""
    ex = current(lab_id)
    if not ex:
        return None
    if _closing and lab_id in _closing and action == "check":
        return None                       # 스위퍼가 확정본을 만드는 중이다
    u = db.get_user(username) if username else None
    if u and u.get("role") == "admin":
        return None                       # 관리자는 다음 응시를 준비해야 한다
    ph = phase(ex)
    if ph == "open":
        if action in OPEN_ALLOWED:
            return None
        return (f"시험이 진행 중입니다 — '{action}' 은(는) 쓸 수 없습니다. "
                f"랩은 터미널에서 직접 고쳐 주세요.")
    if action in CLOSED_ALLOWED:
        return None
    if ph == "overtime":
        return "제한 시간이 끝났습니다 — 성적을 확정하는 중입니다. 잠시 뒤 결과가 나옵니다."
    return ("제한 시간이 끝나 성적이 확정됐습니다. 지금 고쳐도 성적은 바뀌지 않습니다. "
            "인계 보고서를 제출해 주세요. 다시 응시하려면 [캡스톤 다시 시작] 을 누릅니다.")


# ------------------------------------------------------------------ 시작·마감
def prepare(lab_id, username):
    """시작 전 확인. 문제가 있으면 사유 문자열, 없으면 (모듈, 시나리오 목록)."""
    module = docs.get(module_id())
    if not module:
        return f"캡스톤 모듈({module_id()})이 없습니다", None
    st = state.load(lab_id)
    if module["stage"] not in (st.get("applied") or []) and st.get("stage") != module["stage"]:
        return (f"먼저 [이 모듈 적용] 으로 랩을 {module['stage']} 단계까지 올려야 합니다. "
                f"시험은 정상 동작하는 랩에서 시작합니다.", None)
    return None, module


def start(lab_id, username, scenarios, minutes=None):
    """세션 행을 먼저 만든다 — 주입 도중 콘솔이 죽어도 시험이 열려 있게."""
    return db.exam_open(username, lab_id, module_id(), scenarios,
                        minutes if minutes is not None else cfg()["minutes"])


def freeze(ex, by):
    """확정. 검사 결과를 읽어 세션에 못 박고 진도에 한 번만 반영한다."""
    module = docs.get(ex["module_id"])
    res = _session_result(ex)
    closed, changed = db.exam_close(ex["id"], by, res)
    if changed and module:
        # 확정본이 곧 성적이다. 검사를 못 돌렸으면(res is None) 진도는 건드리지 않는다.
        if res:
            assess.sync_progress(ex["username"], ex["lab_id"], module, checks=res)
    return closed, changed


def _session_result(ex):
    """**이번 회차에 나온** 검사 결과만 확정본으로 인정한다.

    var/state/checks-lab<N>-<module>.json 은 회차가 바뀌어도 그대로 남는다.
    마감 검사를 돌리지 못했을 때(Proxmox 장애 등) 그 파일을 그냥 읽으면
    **지난 회차의 성적이 이번 회차의 확정본이 된다.** 파일이 이번 세션이 시작된
    뒤에 쓰인 것인지 mtime 으로 확인한다.
    """
    res = assess.read_checks_result(ex["lab_id"], ex["module_id"])
    if res is None:
        return None
    try:
        mtime = assess.checks_result_path(ex["lab_id"], ex["module_id"]).stat().st_mtime
    except OSError:
        return None
    if mtime < ex["started_epoch"]:
        print(f"[exam] lab{ex['lab_id']} 확정: 지난 회차 결과라 쓰지 않는다 "
              f"(검사 파일이 시험 시작보다 오래됐다)", file=sys.stderr)
        return None
    return res


async def sweep_once(runner):
    """마감 시각이 지난 세션을 확정한다. 콘솔이 꺼져 있던 동안의 것도 여기서 잡힌다."""
    done = []
    for ex in db.exam_overdue():
        lab = ex["lab_id"]
        if runner.busy(lab):
            continue                       # 다음 주기에 다시 본다
        module = docs.get(ex["module_id"])
        if not module or not assess.has_checks(module):
            done.append(freeze(ex, "timeout")[0])
            continue
        _closing.add(lab)
        try:
            job = await runner.submit(lab, "check", module["stage"], None,
                                      ex["username"], module=module["id"])
        except Exception as e:                       # noqa: BLE001
            _closing.discard(lab)
            # 검사를 못 돌렸어도 **마감은 반드시 한다.** 안 그러면 잠금이 안 걸린다.
            print(f"[exam] lab{lab} 마감 검사 실패: {e}", file=sys.stderr)
            done.append(freeze(ex, "timeout")[0])
            continue
        # 검사가 끝날 때까지 기다린다. 다만 무한정 기다리지 않는다 —
        # 스위퍼는 모든 랩이 공유하는 루프 하나뿐이라, 한 랩의 멈춘 작업이
        # 다른 랩의 마감까지 막으면 안 된다.
        waited = 0.0
        while job.status not in ("ok", "failed") and waited < CHECK_TIMEOUT:
            await asyncio.sleep(0.5)
            waited += 0.5
        _closing.discard(lab)
        if job.status not in ("ok", "failed"):
            print(f"[exam] lab{lab} 마감 검사가 {CHECK_TIMEOUT:.0f}초를 넘겼다 — "
                  f"기다리지 않고 마감한다", file=sys.stderr)
            done.append(freeze(ex, "timeout")[0])
            continue
        state.record(lab, "check", module["stage"], job.status == "ok", None, job.id)
        done.append(freeze(ex, "timeout")[0])
    return done


async def sweeper(runner):
    """백그라운드 루프. 예외가 나도 절대 멈추지 않는다 — 멈추면 시험이 안 끝난다.

    다만 '멈추지 않는다'가 '같은 오류를 20초마다 영원히 찍는다'가 되면 안 된다.
    그러면 로그가 그 한 줄로 덮여서 정작 봐야 할 것이 안 보인다.
    같은 오류는 처음 한 번과 그 뒤로 가끔만 찍고, 횟수를 함께 남긴다.
    """
    period = max(5, int(cfg()["sweep_seconds"]))
    every = max(1, int(1200 / period))      # 되풀이되는 오류는 20분에 한 번만
    last, repeat, healed = None, 0, False
    while True:
        try:
            for ex in await sweep_once(runner):
                if ex:
                    print(f"[exam] lab{ex['lab_id']} {ex['module_id']} 마감 확정 "
                          f"— {ex['username']} · {ex['ok']}/{ex['total']}", file=sys.stderr)
            last, repeat = None, 0
        except Exception as e:              # noqa: BLE001
            msg = str(e)
            # 스키마가 없는 것은 기다린다고 낫지 않는다. 딱 한 번 고쳐 본다.
            if "no such table" in msg and not healed:
                healed = True
                try:
                    db.init()
                    print("[exam] DB 스키마를 다시 만들었다 — 계속한다", file=sys.stderr)
                    await asyncio.sleep(period)
                    continue
                except Exception as e2:     # noqa: BLE001
                    msg = f"{msg} · 복구 실패: {e2}"
            repeat = repeat + 1 if msg == last else 0
            last = msg
            if repeat == 0 or repeat % every == 0:
                more = f"  (같은 오류 {repeat + 1}회째)" if repeat else ""
                print(f"[exam] 마감 스위퍼 오류(무시하고 계속): {msg}{more}",
                      file=sys.stderr)
        await asyncio.sleep(period)
