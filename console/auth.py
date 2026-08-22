"""
인증 · 권한.

계정 종류
  admin : 모든 랩 + 계정 관리 + 해설 열람
  user  : 배정받은 랩 하나에 대한 생성·실행·삭제

권한은 매 요청 DB 에서 다시 읽는다. 세션 쿠키에는 username 만 담는다.
그래야 계정을 막거나 랩을 옮겼을 때 즉시 반영된다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L      # noqa: E402
import db                  # noqa: E402
import passwords           # noqa: E402

hash_password = passwords.hash_password
verify_password = passwords.verify_password

_LOGIN = (L.SITE.get("console") or {}).get("login") or {}
MAX_ATTEMPTS = int(_LOGIN.get("max_attempts", 5))
LOCKOUT_MINUTES = int(_LOGIN.get("lockout_minutes", 5))

# ---------------------------------------------------------------------- 권한
LAB_CAPS = {"lab.deploy", "lab.destroy", "lab.apply", "lab.verify",
            "lab.reset", "lab.break", "lab.fix"}
CAPABILITIES = {
    # 랩을 하는 데 필요한 권한만. 남의 랩·계정 관리·해설은 없다.
    "user": set(LAB_CAPS),
    # 모든 기능 + 계정 생성/관리
    "admin": LAB_CAPS | {"lab.all", "user.manage", "module.answers"},
}
ACTION_CAP = {"deploy": "lab.deploy", "destroy": "lab.destroy", "apply": "lab.apply",
              "verify": "lab.verify", "reset": "lab.reset",
              "break": "lab.break", "fix": "lab.fix"}
ROLE_LABEL = {"admin": "관리자", "user": "사용자"}


def can(user, cap):
    return bool(user) and cap in CAPABILITIES.get(user.get("role", ""), set())


def allowed_labs(user):
    if not user:
        return []
    if can(user, "lab.all"):
        lo, _hi = L.SITE["labs"]["id_range"]
        return list(range(lo, L.SITE["labs"]["default_count"] + 1))
    return [user["lab_id"]] if user.get("lab_id") is not None else []


def can_see_answers(user):
    return can(user, "module.answers")


# ---------------------------------------------------------------------- 사용자
def load_user(username):
    """요청마다 DB 에서 다시 읽는다."""
    if not username:
        return None
    u = db.get_user(username)
    if not u or u.get("disabled"):
        return None
    u = {k: v for k, v in u.items() if k != "password"}
    u["role_label"] = ROLE_LABEL.get(u.get("role"), u.get("role"))
    return u


class LoginError(Exception):
    def __init__(self, message, locked_seconds=0, remaining=None):
        super().__init__(message)
        self.message = message
        self.locked_seconds = locked_seconds
        self.remaining = remaining


def authenticate(username, password):
    """성공하면 사용자 dict. 실패하면 LoginError(횟수 제한 포함)."""
    username = (username or "").strip()
    if not username:
        raise LoginError("아이디를 입력할 것")

    left = db.locked_seconds(username)
    if left > 0:
        raise LoginError(
            f"로그인 시도가 {MAX_ATTEMPTS}회를 넘어 잠겼다. "
            f"{left // 60}분 {left % 60}초 뒤에 다시 시도할 것.", locked_seconds=left)

    u = db.get_user(username)
    ok = bool(u) and not u.get("disabled") and verify_password(password, u["password"])
    if not ok:
        # 계정이 없어도 같은 시간이 걸리도록 한 번 해싱한다 (계정 열거 방지)
        if not u:
            verify_password(password, hash_password("dummy"))
        remaining, until = db.record_failure(username, MAX_ATTEMPTS, LOCKOUT_MINUTES)
        if remaining == 0:
            raise LoginError(
                f"로그인 시도 {MAX_ATTEMPTS}회 실패. {LOCKOUT_MINUTES}분간 잠긴다.",
                locked_seconds=LOCKOUT_MINUTES * 60)
        raise LoginError(f"아이디 또는 비밀번호가 맞지 않는다. "
                         f"({MAX_ATTEMPTS - remaining}/{MAX_ATTEMPTS}회 실패)",
                         remaining=remaining)

    db.clear_failures(username)
    db.touch_login(username)
    db.purge_old_attempts()
    return load_user(username)


def change_password(username, new_password, current_password=None, require_current=True):
    """정책 검사 후 변경. 위반 목록을 반환(비어 있으면 성공)."""
    u = db.get_user(username)
    if not u:
        return ["없는 계정"]
    if require_current and not verify_password(current_password or "", u["password"]):
        return ["현재 비밀번호가 맞지 않는다"]
    if verify_password(new_password, u["password"]):
        return ["기존과 다른 비밀번호를 써야 한다"]
    errs = passwords.validate(new_password, username)
    if errs:
        return errs
    db.set_password(username, hash_password(new_password), must_change=False)
    return []


# ----------------------------------------------------------------- 세션 시크릿
def session_secret():
    import os
    return os.environ.get("LAB_CONSOLE_SECRET") or db.session_secret()
