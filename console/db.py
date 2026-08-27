"""
로컬 SQLite 저장소 — 계정과 콘솔 설정.

위치: var/console.db
  · config/ 는 사람이 편집하는 설정, dist/ 는 언제든 재생성 가능한 산출물이다.
  · 계정은 둘 다 아니다. 지우면 복구할 수 없으므로 `make clean` 이 건드리지 않는 var/ 에 둔다.

동시성: WAL 모드 + 짧은 커넥션. 교육생 수십 명 규모에서 충분하다.
"""
import json
import re
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L

DB_PATH = L.ROOT / "var/console.db"
SCHEMA_VERSION = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name                 TEXT NOT NULL DEFAULT '',
    role                 TEXT NOT NULL DEFAULT 'user'
                         CHECK (role IN ('admin', 'user')),
    lab_id               INTEGER,
    password             TEXT NOT NULL,
    disabled             INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    password_changed_at  TEXT,
    last_login           TEXT,
    -- 교육생이 직접 넣는 SSH 공개키. 이 값은 **배정된 랩의 노드에만** 배포된다.
    -- 여기 두는 이유: site.yml 에 두면 전 랩에 박히고, 바꾸려면 VM 을 다시 만들어야 한다.
    ssh_key              TEXT NOT NULL DEFAULT '',
    ssh_key_at           TEXT,
    -- 사용자 계정은 반드시 랩이 배정되어야 한다. 스키마에서 강제한다.
    CHECK (role <> 'user' OR lab_id IS NOT NULL)
);

-- 로그인 실패 추적. 존재하지 않는 계정도 기록해야 계정 열거를 막을 수 있다.
CREATE TABLE IF NOT EXISTS login_attempts (
    username     TEXT PRIMARY KEY COLLATE NOCASE,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_attempt TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 제출 이력. 본인과 관리자가 나중에 확인할 수 있어야 하므로 지우지 않는다.
CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL COLLATE NOCASE,
    lab_id      INTEGER NOT NULL,
    module_id   TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('quiz', 'checks')),
    score       INTEGER NOT NULL DEFAULT 0,      -- 0~100
    correct     INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    passed      INTEGER NOT NULL DEFAULT 0,
    detail      TEXT,                            -- JSON: 문항별/검사별 결과
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS attempts_user_idx
    ON attempts(username, module_id, created_at DESC);

-- 서술형·구성도 제출물. 자동 채점이 불가능하거나 부적절한 과제만 여기로 온다.
--   status: submitted(관리자 검토 대기) / auto_ok(루브릭 자동 통과)
--           approved(관리자 승인) / changes_requested(재제출 요청)
-- 같은 항목을 여러 번 낼 수 있고, 가장 최근 것이 현재 상태다 (이력은 남긴다).
CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL COLLATE NOCASE,
    lab_id      INTEGER NOT NULL,
    module_id   TEXT    NOT NULL,
    item_id     TEXT    NOT NULL,
    body        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'submitted'
                CHECK (status IN ('submitted', 'auto_ok', 'approved', 'changes_requested')),
    auto_hit    INTEGER NOT NULL DEFAULT 0,      -- 루브릭 충족 개수
    auto_total  INTEGER NOT NULL DEFAULT 0,
    auto_detail TEXT,                            -- JSON: 루브릭 항목별 충족 여부
    feedback    TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS submissions_cur_idx
    ON submissions(username, module_id, item_id, id DESC);
CREATE INDEX IF NOT EXISTS submissions_queue_idx
    ON submissions(status, created_at);

-- 시험 세션 (캡스톤). 제한 시간과 그 끝에서의 점수 동결을 기록한다.
--   closed_at IS NULL                  진행 중
--   closed_at + closed_by='cancelled'  취소됨 — 성적도 잠금도 없다
--   closed_at + 그 외                  마감됨 — frozen 이 확정 성적이다
-- 랩의 현재 상태는 **그 랩의 가장 최근 행 하나**로만 판단한다.
-- 새 세션을 시작하면 이전 마감은 자동으로 효력을 잃는다 (재시도 무제한 원칙).
CREATE TABLE IF NOT EXISTS exams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL COLLATE NOCASE,
    lab_id       INTEGER NOT NULL,
    module_id    TEXT    NOT NULL,
    scenarios    TEXT    NOT NULL DEFAULT '',   -- 주입한 시나리오 id (콤마). 진행 중에는 감춘다
    minutes      INTEGER NOT NULL DEFAULT 45,
    started_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    deadline_at  TEXT    NOT NULL,
    closed_at    TEXT,
    closed_by    TEXT,                          -- 'timeout' | 'cancelled' | 관리자 아이디
    frozen       TEXT,                          -- JSON: 마감 시점 검사 결과 확정본
    ok           INTEGER,
    total        INTEGER,
    passed       INTEGER
);
CREATE INDEX IF NOT EXISTS exams_lab_idx  ON exams(lab_id, id DESC);
CREATE INDEX IF NOT EXISTS exams_user_idx ON exams(username, module_id, id DESC);

-- 모듈 안에서 어디까지 열어 봤는가. 교재 -> 과제 -> 퀴즈 순서를 지키게 한다.
--   순서를 강제하는 이유: 교재만 읽고 과제가 있는 줄 모른 채 다음 모듈로
--   넘어가는 일이 실제로 일어난다. 과제는 '읽었다'로 끝나지만, 읽지 않고
--   퀴즈로 건너뛰지는 못하게 한다.
CREATE TABLE IF NOT EXISTS tab_seen (
    username  TEXT NOT NULL COLLATE NOCASE,
    module_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (username, module_id, kind)
);

-- 모듈 통과 현황. 다음 모듈로 넘어갈 수 있는지 판단에 쓴다.
CREATE TABLE IF NOT EXISTS progress (
    username     TEXT    NOT NULL COLLATE NOCASE,
    module_id    TEXT    NOT NULL,
    quiz_passed  INTEGER NOT NULL DEFAULT 0,
    checks_passed INTEGER NOT NULL DEFAULT 0,
    -- 장애 실습: 스스로 주입하고 **[복구] 를 누르지 않고** 고쳤는가.
    -- 검사만으로는 "랩이 정상인가" 밖에 못 본다. 그건 [이 모듈 적용] 직후에도
    -- 참이라, 아무것도 안 해도 통과한다. 고장을 겪고 되살린 사실은 따로 센다.
    drill_passed INTEGER NOT NULL DEFAULT 0,
    best_score   INTEGER NOT NULL DEFAULT 0,
    tries        INTEGER NOT NULL DEFAULT 0,
    passed_at    TEXT,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (username, module_id)
);
"""


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        DB_PATH.parent.chmod(0o700)
    except OSError:
        pass


@contextmanager
def connect():
    _ensure_dir()
    first = not DB_PATH.exists()
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        if first:
            try:
                DB_PATH.chmod(0o600)
            except OSError:
                pass
        yield con
    finally:
        con.close()


# SCHEMA 가 만들기로 한 테이블. 목록을 따로 적지 않고 SCHEMA 에서 뽑는다 —
# 손으로 적으면 테이블을 더할 때마다 둘이 어긋난다.
EXPECTED_TABLES = frozenset(
    re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))


def missing_tables(con):
    have = {r["name"] for r in
            con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return sorted(EXPECTED_TABLES - have)


def init():
    """스키마 생성 · 버전 이행 · 최초 관리자 계정 생성."""
    with connect() as con:
        v = con.execute("PRAGMA user_version").fetchone()[0]
        _upgrade_v1_to_v2(con, v)
        _upgrade_v5_to_v6(con, v)
        _upgrade_v6_to_v7(con, v)
        con.executescript(SCHEMA)
        if v < SCHEMA_VERSION:
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        # 스키마를 만들었다고 믿지 않고 확인한다.
        #   executescript 가 도중에 실패하면 예외가 나지만, 이 파일이 **다른 경로에서
        #   이미 만들어져** 있었거나 권한 때문에 반영되지 않으면 조용히 반쪽 스키마가
        #   된다. 그러면 콘솔은 멀쩡히 뜨고 없는 테이블을 건드리는 기능만 20초마다
        #   오류를 뱉는다 — 원인을 찾기가 아주 어렵다. 여기서 한 번에 죽는 편이 낫다.
        gone = missing_tables(con)
        if gone:
            raise RuntimeError(
                f"{DB_PATH} 의 스키마가 불완전하다. 없는 테이블: {', '.join(gone)}\n"
                f"  이 파일을 만든 것이 이 프로그램이 맞는지, 쓰기 권한이 있는지 볼 것:\n"
                f"    ls -l {DB_PATH}\n"
                f"    sqlite3 {DB_PATH} '.tables'\n"
                f"  살릴 수 없는 DB 라면 옮겨 두고 다시 시작하면 새로 만든다 "
                f"(계정·API 토큰은 다시 넣어야 한다).")
    _migrate_from_yaml()
    ensure_bootstrap_admin()


def _upgrade_v1_to_v2(con, version):
    """v1(trainee/instructor) -> v2(user/admin). 컬럼 추가 + 역할 이름 변경."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if not have:
        return                       # 새 DB — SCHEMA 가 바로 v2 로 만든다
    if version >= 2:
        return
    for col, ddl in [("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
                     ("password_changed_at", "TEXT")]:
        if col not in have:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    # CHECK 제약 때문에 UPDATE 순서가 중요하다 — 새 이름으로 바꾸려면 제약을 먼저 푼다
    con.execute("PRAGMA writable_schema=ON")
    con.execute("UPDATE sqlite_master SET sql=replace(sql, "
                "'CHECK (role IN (''trainee'', ''instructor''))', "
                "'CHECK (role IN (''admin'', ''user''))') WHERE name='users'")
    con.execute("UPDATE sqlite_master SET sql=replace(sql, "
                "'CHECK (role <> ''trainee'' OR lab_id IS NOT NULL)', "
                "'CHECK (role <> ''user'' OR lab_id IS NOT NULL)') WHERE name='users'")
    con.execute("PRAGMA writable_schema=OFF")
    con.execute("UPDATE users SET role='admin' WHERE role='instructor'")
    con.execute("UPDATE users SET role='user'  WHERE role='trainee'")
    print("[db] 스키마 v1 -> v2 (역할: instructor->admin, trainee->user)", file=sys.stderr)


def _upgrade_v5_to_v6(con, version):
    """v5 -> v6: 사용자별 SSH 공개키. 기존 DB 에 컬럼만 더한다."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if not have or version >= 6:
        return
    for col, ddl in [("ssh_key", "TEXT NOT NULL DEFAULT ''"), ("ssh_key_at", "TEXT")]:
        if col not in have:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    print("[db] 스키마 v5 -> v6 (사용자별 SSH 공개키)", file=sys.stderr)


def _upgrade_v6_to_v7(con, version):
    """v6 -> v7: 장애 실습 통과 여부. 기존 DB 에 컬럼만 더한다."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(progress)")}
    if not have or version >= 7:
        return
    if "drill_passed" not in have:
        con.execute("ALTER TABLE progress ADD COLUMN "
                    "drill_passed INTEGER NOT NULL DEFAULT 0")
    print("[db] 스키마 v6 -> v7 (장애 실습 통과 여부)", file=sys.stderr)


# ============================================================ 탭 열람 순서
# 배우는 순서 = 탭 순서. 개념을 먼저 확인하고(퀴즈), 그다음 랩에서 손으로
# 만들고(과제), 마지막에 그 결과를 기계가 본다(검증).
# 전에는 과제가 퀴즈 앞에 있었는데, 과제 탭에는 제출할 것이 아무것도 없어서
# 교육생이 "이걸 어디에 내라는 거지" 에서 멈췄다.
TAB_ORDER = ["README", "quiz", "tasks", "verify"]


def mark_tab_seen(username, module_id, kind):
    if kind not in TAB_ORDER:
        return
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO tab_seen (username, module_id, kind) "
                    "VALUES (?,?,?)", (username, module_id, kind))


def tabs_seen(username, module_id):
    with connect() as con:
        rows = con.execute("SELECT kind FROM tab_seen WHERE username=? COLLATE NOCASE "
                           "AND module_id=?", (username, module_id)).fetchall()
    return {r["kind"] for r in rows}


def tab_allowed(username, module_id, kind, is_admin=False):
    """이 탭을 열 수 있는가. 앞 탭을 봐야 다음 탭이 열린다.

    관리자는 제한하지 않는다 — 교재를 만들고 검토하는 사람이다.
    'answers' 는 별도 권한으로 이미 막혀 있으므로 여기서는 다루지 않는다.
    """
    if is_admin or kind not in TAB_ORDER:
        return True
    idx = TAB_ORDER.index(kind)
    if idx == 0:
        return True
    seen = tabs_seen(username, module_id)
    return all(k in seen for k in TAB_ORDER[:idx])


# ============================================================ 랩 콘솔 비밀번호
#  랩 노드의 `lab` 계정 비밀번호. SSH 는 키로만 들어가지만, **콘솔은 키를 쓸 수 없다.**
#  화면에 붙는 경로라 비밀번호 말고는 방법이 없다 (M0 실습 5 — 자기가 관리 링크를
#  내렸을 때 되돌리는 유일한 길).
#
#  전 랩 공용이다. 랩을 나누는 것은 Proxmox 쪽 ACL 이 한다 —
#  교육생은 자기 랩 VM 의 콘솔만 열 수 있다(tools/gen-console-access.py).
#
#  파일로 내보내지 않는다. tfvars 는 생성물이라 재생성·복사된다.
#  Terraform 에는 실행 시 TF_VAR_lab_password 로만 넘긴다.
def lab_console_password(create=True):
    """없으면 만들어서 돌려준다. 있으면 그대로."""
    v = get_setting("lab.console_password")
    if v:
        return v
    if not create:
        return ""
    import passwords                                   # noqa: PLC0415
    v = passwords.generate(14)
    set_setting("lab.console_password", v)
    print("[db] 랩 콘솔 비밀번호를 새로 만들었다 (var/console.db 에만 있다)",
          file=sys.stderr)
    return v


def set_lab_console_password(value):
    set_setting("lab.console_password", value or "")


# ---------------------------------------------------------- Proxmox 콘솔 계정
#  왜 교육생 1인 1계정이 아니라 **랩당 1계정**인가
#    · 계정을 하나로 통일하면 남의 랩 화면이 열린다. 노드 `lab` 비밀번호는
#      전 랩 공용이라(위 lab_console_password), 화면만 열리면 바로 로그인된다.
#      랩 격리도 시험도 그 자리에서 무너진다.
#    · 반대로 1인 1계정은 교육생이 늘 때마다 Proxmox 호스트에서 root 작업이 생긴다.
#    · 같은 랩 교육생은 어차피 **같은 VM 13대**를 함께 쓴다. 랩 경계 안에서
#      계정을 나눠 봐야 지키는 것이 없다.
#  그래서 랩당 1개다. 만들 일은 랩을 늘릴 때뿐이고, 교육생 수와 무관하다.
#
#  비밀번호를 DB 에 두는 이유: 교육생 [접속 키] 화면이 **자기 랩 것만** 보여 준다.
#  관리자가 사람마다 비밀번호를 전달하는 일 자체를 없앤다.
PVE_CONSOLE_USER = "lab{lab_id}-console"
# 헷갈리는 글자를 뺀다 (0/O, 1/l/I). 화면 보고 손으로 치는 값이다.
PVE_PW_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def lab_pve_account(lab_id, create=True):
    """그 랩의 Proxmox 콘솔 계정. (userid, 비밀번호) — 없으면 만든다."""
    lab_id = int(lab_id)
    uid = PVE_CONSOLE_USER.format(lab_id=lab_id) + "@pve"
    key = f"lab{lab_id}.pve_console_password"
    v = get_setting(key)
    if not v and create:
        # 콘솔 화면은 붙여넣기가 안 되는 경우가 많다. 불러 주기 쉬운 문자만 쓴다.
        v = "".join(secrets.choice(PVE_PW_ALPHABET) for _ in range(14))
        set_setting(key, v)
    return uid, (v or "")


def set_lab_pve_password(lab_id, value):
    set_setting(f"lab{int(lab_id)}.pve_console_password", value or "")


# ============================================================ SSH 공개키
def set_ssh_key(username, key):
    """공개키 저장(빈 문자열이면 제거). 검증은 부르는 쪽에서 한다."""
    with connect() as con:
        cur = con.execute(
            # 밀리초까지 남긴다. 초 단위로는 [점프 계정 적용] 과 같은 초에 등록된 키를
            # 반영됐는지 아닌지 가릴 수 없다 (jump_stale_users 가 이 값을 본다).
            "UPDATE users SET ssh_key=?, ssh_key_at=CASE WHEN ?='' THEN NULL "
            "ELSE strftime('%Y-%m-%d %H:%M:%f','now') END "
            "WHERE username=? COLLATE NOCASE",
            (key, key, username))
        return cur.rowcount > 0


# ------------------------------------------------------- 점프 계정 반영 시각
#  교육생 키가 가는 곳은 **두 곳**이고 가는 길이 다르다.
#    운영 서버 /home/<id>/.ssh/authorized_keys ← root 헬퍼 ([점프 계정 적용])
#    랩 노드   ~lab/.ssh/authorized_keys       ← Ansible ([지금 랩에 반영])
#  키를 바꾼 뒤 뒤쪽만 돌리면 랩 노드에는 들어가는데 점프 호스트는 옛 키 그대로다.
#  그러면 ssh 가 첫 홉에서 막히는데 화면은 아무 말도 하지 않았다.
#  여기서 "언제 반영했는가" 를 남겨 두고 users.ssh_key_at 과 비교한다.
JUMP_APPLIED_KEY = "jump.applied_at"


def now_utc():
    """DB 가 쓰는 것과 **같은 시계·같은 형식**. 파이썬 시간을 섞으면 시간대가 어긋난다."""
    with connect() as con:
        return con.execute("SELECT strftime('%Y-%m-%d %H:%M:%f','now')").fetchone()[0]


def mark_jump_applied(stamp=None):
    """[점프 계정 적용] 이 성공했을 때 부른다.

    stamp 는 **작업을 시작한 시각**을 넣는다. 끝난 시각이 아니다 —
    헬퍼는 시작할 때의 DB 를 읽으므로, 도는 동안 등록된 키는 반영되지 않았다.
    끝난 시각으로 적으면 그 키를 '반영됨' 으로 삼켜 버린다.
    """
    v = stamp or now_utc()
    set_setting(JUMP_APPLIED_KEY, v)
    return v


def jump_applied_at():
    return get_setting(JUMP_APPLIED_KEY) or ""


def jump_stale_users():
    """키가 점프 계정에 아직 반영되지 않은 교육생.

    한 번도 반영한 적이 없으면 키를 가진 교육생 전부가 여기 들어온다 — 맞는 말이다.
    """
    applied = jump_applied_at()
    with connect() as con:
        rows = con.execute(
            "SELECT username, name, ssh_key_at FROM users "
            " WHERE role='user' AND disabled=0 AND ssh_key<>'' "
            # julianday 로 비교한다. 밀리초까지 있는 값과 예전의 초 단위 값이
            # 섞여 있어도 둘 다 제대로 파싱된다 — 문자열 비교로는 그게 안 된다.
            "   AND (?='' OR julianday(ssh_key_at) > julianday(?)) "
            " ORDER BY username", (applied, applied)).fetchall()
    return [{"username": r["username"], "name": r["name"],
             "ssh_key_at": r["ssh_key_at"]} for r in rows]


def lab_keys(lab_id):
    """이 랩에 배포할 공개키 목록.

    **비활성 계정은 뺀다.** 그래야 퇴소 처리가 다음 설정 적용에서 그대로 반영된다.
    관리자 계정의 키는 넣지 않는다 — 관리자는 운영 서버 키로 이미 들어간다.
    """
    with connect() as con:
        rows = con.execute(
            "SELECT username, name, ssh_key FROM users "
            " WHERE lab_id=? AND role='user' AND disabled=0 AND ssh_key<>'' "
            " ORDER BY username", (lab_id,)).fetchall()
    return [{"username": r["username"], "name": r["name"], "key": r["ssh_key"]} for r in rows]


def ensure_bootstrap_admin():
    """계정이 하나도 없으면 site.yml 의 초기 관리자를 만든다. 첫 로그인에 변경 강제."""
    if count_users() > 0:
        return None
    import passwords
    cfg = L.SITE.get("console", {}).get("bootstrap_admin") or {}
    u, pw = cfg.get("username", "admin"), cfg.get("password", "admin")
    with connect() as con:
        con.execute("INSERT INTO users (username, name, role, lab_id, password, "
                    "must_change_password) VALUES (?,?,?,?,?,1)",
                    (u, "관리자", "admin", None, passwords.hash_password(pw)))
    print(f"[db] 최초 관리자 계정 생성: {u} / {pw}  "
          f"— 첫 로그인에서 반드시 비밀번호를 바꿔야 한다.", file=sys.stderr)
    return u


# ------------------------------------------------------------------ 마이그레이션
def _migrate_from_yaml():
    """예전 config/users.yml 이 남아 있고 DB 가 비어 있으면 옮겨 온다."""
    import yaml
    old = L.ROOT / "config/users.yml"
    if not old.exists():
        return
    if count_users() > 0:
        return
    data = yaml.safe_load(old.read_text(encoding="utf-8")) or {}
    users = data.get("users") or []
    if not users:
        return
    with connect() as con:
        for u in users:
            try:
                con.execute(
                    "INSERT INTO users (username, name, role, lab_id, password) "
                    "VALUES (?,?,?,?,?)",
                    (u["username"], u.get("name", u["username"]),
                     u.get("role", "trainee"), u.get("lab_id"), u["password"]))
            except sqlite3.IntegrityError:
                pass
        if data.get("session_secret"):
            set_setting("session_secret", data["session_secret"], con)
    old.rename(old.with_suffix(".yml.migrated"))
    print(f"[db] config/users.yml 의 계정 {len(users)}개를 {DB_PATH} 로 옮겼다. "
          f"원본은 users.yml.migrated 로 보관한다.", file=sys.stderr)


# ---------------------------------------------------------------------- 설정
def get_setting(key, default=None, con=None):
    def _q(c):
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    if con:
        return _q(con)
    with connect() as c:
        return _q(c)


def set_setting(key, value, con=None):
    sql = "INSERT INTO settings(key,value) VALUES(?,?) " \
          "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    if con:
        con.execute(sql, (key, value))
        return value
    with connect() as c:
        c.execute(sql, (key, value))
    return value


def session_secret():
    s = get_setting("session_secret")
    if not s:
        s = set_setting("session_secret", secrets.token_hex(32))
    return s


# ---------------------------------------------------------------------- 계정
def _row(r):
    if r is None:
        return None
    d = dict(r)
    d.pop("id", None)
    d["disabled"] = bool(d.get("disabled"))
    d["must_change_password"] = bool(d.get("must_change_password"))
    return d


def count_users():
    with connect() as con:
        try:
            return con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        except sqlite3.OperationalError:
            return 0


def list_users():
    with connect() as con:
        return [_row(r) for r in con.execute(
            "SELECT * FROM users ORDER BY role DESC, username")]


def get_user(username):
    with connect() as con:
        return _row(con.execute(
            "SELECT * FROM users WHERE username=?", (username,)).fetchone())


def add_user(username, password_hash, role="user", lab_id=None, name="",
             must_change=False):
    with connect() as con:
        con.execute("INSERT INTO users (username, name, role, lab_id, password, "
                    "must_change_password) VALUES (?,?,?,?,?,?)",
                    (username, name or username, role, lab_id, password_hash,
                     1 if must_change else 0))
    return get_user(username)


def set_password(username, password_hash, must_change=False):
    with connect() as con:
        cur = con.execute(
            "UPDATE users SET password=?, must_change_password=?, "
            "password_changed_at=datetime('now') WHERE username=?",
            (password_hash, 1 if must_change else 0, username))
        return cur.rowcount > 0


def set_disabled(username, disabled=True):
    with connect() as con:
        cur = con.execute("UPDATE users SET disabled=? WHERE username=?",
                          (1 if disabled else 0, username))
        return cur.rowcount > 0


def set_lab(username, lab_id):
    with connect() as con:
        cur = con.execute("UPDATE users SET lab_id=? WHERE username=?",
                          (lab_id, username))
        return cur.rowcount > 0


def delete_user(username):
    with connect() as con:
        cur = con.execute("DELETE FROM users WHERE username=?", (username,))
        return cur.rowcount > 0


def touch_login(username):
    with connect() as con:
        con.execute("UPDATE users SET last_login=datetime('now') WHERE username=?",
                    (username,))


def set_must_change(username, flag=True):
    with connect() as con:
        con.execute("UPDATE users SET must_change_password=? WHERE username=?",
                    (1 if flag else 0, username))


def set_role(username, role, lab_id=None):
    with connect() as con:
        con.execute("UPDATE users SET role=?, lab_id=? WHERE username=?",
                    (role, lab_id, username))


def count_admins(exclude=None):
    with connect() as con:
        if exclude:
            return con.execute("SELECT COUNT(*) c FROM users WHERE role='admin' "
                               "AND disabled=0 AND username<>?", (exclude,)).fetchone()["c"]
        return con.execute("SELECT COUNT(*) c FROM users WHERE role='admin' "
                           "AND disabled=0").fetchone()["c"]


# ------------------------------------------------------------- 로그인 실패 추적
def attempt_state(username):
    with connect() as con:
        r = con.execute("SELECT * FROM login_attempts WHERE username=?",
                        (username,)).fetchone()
        return dict(r) if r else {"username": username, "failed_count": 0,
                                  "locked_until": None}


def record_failure(username, max_attempts, lockout_minutes):
    """실패를 기록하고, 한도를 넘으면 잠근다. (남은 시도, 잠금 해제 시각) 반환."""
    with connect() as con:
        con.execute(
            "INSERT INTO login_attempts (username, failed_count, last_attempt) "
            "VALUES (?, 1, datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET "
            "  failed_count = login_attempts.failed_count + 1, "
            "  last_attempt = datetime('now')", (username,))
        r = con.execute("SELECT failed_count FROM login_attempts WHERE username=?",
                        (username,)).fetchone()
        n = r["failed_count"]
        if n >= max_attempts:
            con.execute("UPDATE login_attempts SET locked_until="
                        f"datetime('now', '+{int(lockout_minutes)} minutes'), failed_count=0 "
                        "WHERE username=?", (username,))
            row = con.execute("SELECT locked_until FROM login_attempts WHERE username=?",
                              (username,)).fetchone()
            return 0, row["locked_until"]
        return max_attempts - n, None


def clear_failures(username):
    with connect() as con:
        con.execute("DELETE FROM login_attempts WHERE username=?", (username,))


def locked_seconds(username):
    """잠겨 있으면 남은 초, 아니면 0."""
    with connect() as con:
        r = con.execute(
            "SELECT CAST((julianday(locked_until) - julianday('now')) * 86400 AS INTEGER) s "
            "FROM login_attempts WHERE username=? AND locked_until IS NOT NULL",
            (username,)).fetchone()
        return max(0, r["s"]) if r and r["s"] is not None else 0


# --------------------------------------------------------------- 제출 이력·진도
def record_attempt(username, lab_id, module_id, kind, score, correct, total,
                   passed, detail):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO attempts (username, lab_id, module_id, kind, score, "
            "correct, total, passed, detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (username, lab_id, module_id, kind, int(score), int(correct),
             int(total), 1 if passed else 0, json.dumps(detail, ensure_ascii=False)))
        return cur.lastrowid


def update_progress(username, module_id, quiz_passed=None, checks_passed=None,
                    score=None, bump_try=False, module_complete=None,
                    drill_passed=None):
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO progress (username, module_id) VALUES (?,?)",
                    (username, module_id))
        sets, args = ["updated_at=datetime('now')"], []
        if quiz_passed is not None:
            sets.append("quiz_passed=?"); args.append(1 if quiz_passed else 0)
        if checks_passed is not None:
            sets.append("checks_passed=?"); args.append(1 if checks_passed else 0)
        if drill_passed is not None:
            # 한 번 통과하면 되돌리지 않는다 — 다음 모듈에서 랩을 초기화해도
            # "그때 스스로 고쳤다" 는 사실은 그대로다.
            sets.append("drill_passed=MAX(drill_passed, ?)")
            args.append(1 if drill_passed else 0)
        if score is not None:
            sets.append("best_score=MAX(best_score, ?)"); args.append(int(score))
        if bump_try:
            sets.append("tries=tries+1")
        if module_complete:
            sets.append("passed_at=COALESCE(passed_at, datetime('now'))")
        con.execute(f"UPDATE progress SET {', '.join(sets)} "
                    "WHERE username=? AND module_id=?", (*args, username, module_id))


def get_progress(username, module_id=None):
    with connect() as con:
        if module_id:
            r = con.execute("SELECT * FROM progress WHERE username=? AND module_id=?",
                            (username, module_id)).fetchone()
            return dict(r) if r else None
        return {r["module_id"]: dict(r) for r in con.execute(
            "SELECT * FROM progress WHERE username=?", (username,))}


def list_attempts(username=None, module_id=None, limit=100):
    q = "SELECT * FROM attempts WHERE 1=1"
    args = []
    if username:
        q += " AND username=?"; args.append(username)
    if module_id:
        q += " AND module_id=?"; args.append(module_id)
    q += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    with connect() as con:
        out = []
        for r in con.execute(q, args):
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"]) if d["detail"] else None
            except Exception:
                d["detail"] = None
            d["passed"] = bool(d["passed"])
            out.append(d)
        return out


def latest_attempt(username, module_id, kind):
    with connect() as con:
        r = con.execute("SELECT * FROM attempts WHERE username=? AND module_id=? "
                        "AND kind=? ORDER BY id DESC LIMIT 1",
                        (username, module_id, kind)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        except Exception:
            d["detail"] = None
        d["passed"] = bool(d["passed"])
        return d


def purge_old_attempts(days=7):
    with connect() as con:
        con.execute("DELETE FROM login_attempts WHERE last_attempt < "
                    f"datetime('now', '-{int(days)} days') AND "
                    "(locked_until IS NULL OR locked_until < datetime('now'))")


# ------------------------------------------------------- 서술형 제출물·검토
def add_submission(username, lab_id, module_id, item_id, body,
                   status="submitted", auto=None):
    auto = auto or {}
    with connect() as con:
        cur = con.execute(
            "INSERT INTO submissions (username, lab_id, module_id, item_id, body, "
            "status, auto_hit, auto_total, auto_detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (username, lab_id, module_id, item_id, body, status,
             int(auto.get("hit", 0)), int(auto.get("total", 0)),
             json.dumps(auto.get("detail", []), ensure_ascii=False)))
        return cur.lastrowid


def latest_submissions(username, module_id=None):
    """{item_id: row} — 항목마다 가장 최근 제출본 하나."""
    q = ("SELECT * FROM submissions WHERE username=? "
         + ("AND module_id=? " if module_id else "")
         + "ORDER BY id DESC")
    args = (username, module_id) if module_id else (username,)
    out = {}
    with connect() as con:
        for r in con.execute(q, args):
            key = r["item_id"] if module_id else (r["module_id"], r["item_id"])
            if key not in out:
                out[key] = _sub(r)
    return out


def pending_submissions(limit=200, module_id=None):
    """관리자 검토가 필요한 것만. 각 (사람·모듈·항목)의 **최신 제출본**만 올린다."""
    q = """
        SELECT s.* FROM submissions s
        WHERE s.status = 'submitted'
          AND s.id = (SELECT MAX(id) FROM submissions t
                      WHERE t.username = s.username AND t.module_id = s.module_id
                        AND t.item_id = s.item_id)
        {mf}
        ORDER BY s.created_at ASC LIMIT ?
    """.format(mf="AND s.module_id = ?" if module_id else "")
    args = (module_id, limit) if module_id else (limit,)
    with connect() as con:
        return [_sub(r) for r in con.execute(q, args)]


def count_pending():
    with connect() as con:
        return con.execute("""
            SELECT COUNT(*) FROM submissions s WHERE s.status='submitted'
              AND s.id = (SELECT MAX(id) FROM submissions t
                          WHERE t.username=s.username AND t.module_id=s.module_id
                            AND t.item_id=s.item_id)""").fetchone()[0]


def get_submission(sub_id):
    with connect() as con:
        r = con.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
        return _sub(r) if r else None


def review_submission(sub_id, status, feedback, reviewer):
    if status not in ("approved", "changes_requested"):
        raise ValueError(status)
    with connect() as con:
        con.execute("UPDATE submissions SET status=?, feedback=?, reviewed_by=?, "
                    "reviewed_at=datetime('now') WHERE id=?",
                    (status, feedback or "", reviewer, sub_id))


def _sub(r):
    d = dict(r)
    try:
        d["auto_detail"] = json.loads(d.get("auto_detail") or "[]")
    except Exception:
        d["auto_detail"] = []
    return d


# ------------------------------------------------------------------ 시험 세션
#  시각은 전부 SQLite 의 datetime('now') = UTC 로 통일한다.
#  파이썬에서 시각을 만들어 넣으면 콘솔 프로세스의 시간대에 따라 마감이 어긋난다.
def exam_open(username, lab_id, module_id, scenarios, minutes):
    """새 세션을 연다. 같은 랩의 이전 세션은 이 행이 최신이 되면서 효력을 잃는다."""
    with connect() as con:
        cur = con.execute(
            "INSERT INTO exams (username, lab_id, module_id, scenarios, minutes, "
            "deadline_at) VALUES (?,?,?,?,?, datetime('now', ?))",
            (username, lab_id, module_id, ",".join(scenarios), int(minutes),
             f"+{int(minutes)} minutes"))
        return exam_get(cur.lastrowid, con)


def _exam(r):
    if r is None:
        return None
    d = dict(r)
    d["scenarios"] = [s for s in (d.get("scenarios") or "").split(",") if s]
    try:
        d["frozen"] = json.loads(d["frozen"]) if d.get("frozen") else None
    except Exception:
        d["frozen"] = None
    d["open"] = d["closed_at"] is None
    d["cancelled"] = d["closed_by"] == "cancelled"
    d["locked"] = bool(d["closed_at"]) and not d["cancelled"]
    return d


_EXAM_SEL = ("SELECT *, CAST(strftime('%s', deadline_at) AS INTEGER) "
             "         - CAST(strftime('%s', 'now') AS INTEGER) AS remaining, "
             "       CAST(strftime('%s', started_at)  AS INTEGER) AS started_epoch, "
             "       CAST(strftime('%s', deadline_at) AS INTEGER) AS deadline_epoch "
             "FROM exams ")


def exam_get(exam_id, con=None):
    def _q(c):
        return _exam(c.execute(_EXAM_SEL + "WHERE id=?", (exam_id,)).fetchone())
    if con:
        return _q(con)
    with connect() as c:
        return _q(c)


def exam_latest(lab_id=None, username=None, module_id=None):
    """가장 최근 세션 하나. 랩의 잠금 여부는 오직 이 행으로 판단한다."""
    where, args = [], []
    if lab_id is not None:
        where.append("lab_id=?"); args.append(lab_id)
    if username:
        where.append("username=? COLLATE NOCASE"); args.append(username)
    if module_id:
        where.append("module_id=?"); args.append(module_id)
    sql = _EXAM_SEL + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT 1"
    with connect() as con:
        return _exam(con.execute(sql, args).fetchone())


def exam_overdue():
    """마감 시각이 지났는데 아직 확정되지 않은 세션. 콘솔 재시작 뒤에도 여기서 잡힌다."""
    with connect() as con:
        rows = con.execute(_EXAM_SEL + "WHERE closed_at IS NULL "
                           "AND datetime(deadline_at) <= datetime('now') "
                           "ORDER BY id").fetchall()
    return [_exam(r) for r in rows]


def exam_close(exam_id, by, result=None):
    """확정. result 는 run-checks 결과 전체(JSON 으로 통째로 보관한다).

    이미 닫힌 세션은 건드리지 않는다 — 확정본은 한 번만 만들어진다.
    스위퍼와 관리자의 [즉시 마감]이 겹쳐도 먼저 닫은 쪽이 이긴다.
    """
    with connect() as con:
        cur = con.execute(
            "UPDATE exams SET closed_at=datetime('now'), closed_by=?, "
            "frozen=?, ok=?, total=?, passed=? WHERE id=? AND closed_at IS NULL",
            (by,
             json.dumps(result, ensure_ascii=False) if result else None,
             (result or {}).get("ok"), (result or {}).get("total"),
             1 if (result or {}).get("passed") else 0,
             exam_id))
        changed = cur.rowcount
        return _exam(con.execute(_EXAM_SEL + "WHERE id=?", (exam_id,)).fetchone()), bool(changed)


def exam_extend(exam_id, minutes):
    """분 단위 연장. 음수도 받는다 — 부호를 붙여야 SQLite 가 '+-5 minutes' 를 만들지 않는다."""
    with connect() as con:
        con.execute("UPDATE exams SET deadline_at=datetime(deadline_at, ?), "
                    "minutes=MAX(1, minutes+?) WHERE id=? AND closed_at IS NULL",
                    (f"{int(minutes):+d} minutes", int(minutes), exam_id))
    return exam_get(exam_id)


def exam_expire(exam_id):
    """마감 시각을 지금으로 당긴다. 확정 자체는 스위퍼가 한다 —
    관리자의 [즉시 마감]과 시간 초과가 **같은 경로**를 타야 결과가 어긋나지 않는다."""
    with connect() as con:
        con.execute("UPDATE exams SET deadline_at=datetime('now') "
                    "WHERE id=? AND closed_at IS NULL", (exam_id,))
    return exam_get(exam_id)


def exam_list(limit=50, username=None):
    where, args = [], []
    if username:
        where.append("username=? COLLATE NOCASE"); args.append(username)
    sql = _EXAM_SEL + ("WHERE " + " AND ".join(where) if where else "") + \
        " ORDER BY id DESC LIMIT ?"
    with connect() as con:
        return [_exam(r) for r in con.execute(sql, args + [int(limit)]).fetchall()]
