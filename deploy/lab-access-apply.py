#!/usr/bin/env python3
"""
교육생 점프 계정 적용기 — **root 로 실행된다.**

  설치 위치 : /usr/local/sbin/lab-access-apply   (root:root 0755)
  호출자    : 웹 콘솔이 `sudo -n /usr/local/sbin/lab-access-apply` 로 부른다
  설정      : /etc/my-network-lab/policy.json    (root:root 0644, install.sh 가 쓴다)

=============================================================================
 왜 저장소 파일을 쓰지 않는가 — 이 파일의 존재 이유
=============================================================================
교육생이 늘 때마다 관리자가 서버에 들어가 `sudo ./dist/jump-access.sh` 를 치는 것이
운영에서 가장 자주 반복되는 일이었다. 그 일을 콘솔 버튼으로 옮기려면 콘솔 계정에
sudo 를 줘야 한다. 그런데 순진하게 주면 이렇게 된다.

    콘솔 계정이 dist/jump-access.sh 를 쓸 수 있다
      + sudo 로 그 파일을 실행할 수 있다
      = 콘솔 계정이 곧 root 다 (sudo 를 준 의미가 없다)

그래서 이 프로그램은 **콘솔 계정이 건드릴 수 없는 것만 실행한다.**
  · 코드는 이 파일뿐이다. 저장소(tools/, dist/)를 읽지도 실행하지도 않는다.
  · 주소·경로는 root 소유 policy.json 에서 온다.
  · 콘솔에서 오는 것은 var/console.db 의 **데이터뿐**이고, 아래에서 전부 검증한다.
    이름은 좁은 화이트리스트로, 공개키는 형식·base64·타입 일치까지 본다.
  · 셸 스크립트를 만들어 돌리지 않는다. 문자열이 셸에 닿는 경로 자체를 없앤다.

그래서 콘솔이 뚫려도 이 sudo 로 할 수 있는 일은 "교육생 점프 계정을 만드는 것"
그 이상이 되지 않는다.

=============================================================================
 무엇을 하는가
=============================================================================
  ① DB 에서 (사용 중 · 공개키 등록됨) 교육생을 읽는다
  ② 계정을 만들거나 갱신한다 — 셸 없음(nologin), 비밀번호 잠금, 키 1개
  ③ 우리가 만든 계정 중 **DB 에 더 이상 없는 것은 접근을 회수한다**
     (지우지는 않는다 — 홈 디렉터리를 남겨야 사고 시 되돌릴 수 있다)
  ④ sshd 조각을 쓴다 — 각 계정은 자기 랩 노드의 22번으로만 나갈 수 있다
  ⑤ `sshd -t` 로 검사한 뒤에만 reload 한다

  --dry-run 을 주면 ①~④를 계산해 보여 주기만 하고 아무것도 바꾸지 않는다.
"""
import argparse
import base64
import json
import os
import pwd
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

POLICY = Path("/etc/my-network-lab/policy.json")
TAG = "my-network-lab"                       # 우리가 만든 계정 표시 (GECOS)

# 콘솔 계정 이름 = OS 계정 이름. console/auth.py 의 USERNAME_RE 와 같은 규칙이다.
# 여기서 한 번 더 보는 이유: DB 는 콘솔 계정이 쓸 수 있는 파일이다.
# 콘솔이 검사했다는 것을 믿고 root 작업을 하면 검사한 적이 없는 것과 같다.
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
RESERVED = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news",
    "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody",
    "systemd-network", "systemd-resolve", "messagebus", "syslog", "_apt", "tss",
    "uuidd", "tcpdump", "landscape", "pollinate", "sshd", "ubuntu", "debian",
    "admin", "adm", "sudo", "docker", "terraform", "ansible", "lab",
}
KEY_TYPES = {"ssh-ed25519", "ssh-rsa",
             "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"}
KEY_RE = re.compile(r"^(\S+) ([A-Za-z0-9+/]+={0,2})(?: (.*))?$")

G, Y, R, N = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def ok(m):   print(f"  {G}✔{N} {m}")
def warn(m): print(f"  {Y}!{N} {m}")
def die(m):  print(f"  {R}✘{N} {m}", file=sys.stderr); sys.exit(1)


# --------------------------------------------------------------------- 검증
def valid_username(n):
    return bool(USERNAME_RE.match(n or "")) and n not in RESERVED


def valid_key(line):
    """authorized_keys 한 줄로 안전한가.

    `command=`·`environment=` 같은 **옵션이 앞에 붙은 줄을 반드시 막아야 한다.**
    옵션이 붙으면 셸 없는 계정에서도 우리가 정하지 않은 명령이 돌 수 있다.
    아래 정규식은 줄이 키 타입으로 시작할 것을 요구하므로 옵션 줄은 통과하지 못한다.
    """
    if not line or "\n" in line or "\r" in line:
        return False
    m = KEY_RE.match(line.strip())
    if not m:
        return False
    ktype, blob = m.group(1), m.group(2)
    if ktype not in KEY_TYPES:
        return False
    try:
        raw = base64.b64decode(blob, validate=True)
        n = struct.unpack(">I", raw[:4])[0]
        # blob 안에 적힌 타입과 줄 맨 앞의 타입이 같아야 한다.
        # 다르면 sshd 가 blob 쪽을 믿는다 — 눈에 보이는 것과 실제가 달라진다.
        return raw[4:4 + n].decode("ascii") == ktype
    except Exception:                                   # noqa: BLE001
        return False


# --------------------------------------------------------------------- 입력
def load_policy():
    if not POLICY.exists():
        die(f"{POLICY} 가 없다. 운영 서버에서 `sudo ./install.sh --hardened` 를 먼저 실행할 것")
    st = POLICY.stat()
    if st.st_uid != 0 or (st.st_mode & 0o022):
        die(f"{POLICY} 가 root 소유가 아니거나 다른 사용자가 쓸 수 있다 "
            f"(uid={st.st_uid} mode={st.st_mode & 0o777:04o}) — 신뢰할 수 없다")
    return json.loads(POLICY.read_text(encoding="utf-8"))


def load_students(dbpath, labs):
    """(lab_id, username, name, key) — 검증을 통과한 것만. 거부한 것은 알린다."""
    if not Path(dbpath).exists():
        die(f"콘솔 DB 가 없다: {dbpath}")
    con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT username, name, lab_id, ssh_key FROM users "
            " WHERE role='user' AND disabled=0 AND ssh_key IS NOT NULL AND ssh_key<>'' "
            " ORDER BY lab_id, username").fetchall()
    finally:
        con.close()
    good, bad = [], []
    for r in rows:
        u, lab, key = r["username"], r["lab_id"], (r["ssh_key"] or "").strip()
        if not valid_username(u):
            bad.append((u, "이름이 규칙에 맞지 않거나 시스템 계정과 겹친다")); continue
        if str(lab) not in labs:
            bad.append((u, f"모르는 랩 {lab}")); continue
        if not valid_key(key):
            bad.append((u, "공개키가 올바르지 않다 (옵션이 붙었거나 형식이 틀렸다)")); continue
        good.append({"lab": str(lab), "user": u, "name": r["name"] or "", "key": key})
    return good, bad


def our_accounts():
    """이 프로그램이 만든 계정 (GECOS 에 표시가 있는 것)."""
    return {p.pw_name for p in pwd.getpwall() if TAG in (p.pw_gecos or "")}


# --------------------------------------------------------------------- 적용
def run(argv, dry):
    if dry:
        print(f"      $ {' '.join(argv)}")
        return 0
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0:
        die(f"{' '.join(argv)}\n      {(p.stderr or p.stdout).strip()}")
    return 0


def ensure_account(s, dry):
    u, lab = s["user"], s["lab"]
    nologin = shutil.which("nologin") or "/usr/sbin/nologin"
    gecos = f"{TAG} lab{lab}"
    try:
        ent = pwd.getpwnam(u)
        # 같은 이름의 기존 계정을 가로채지 않는다. 운영 서버에는 관리자 계정도 있고,
        # 우연히 이름이 겹치면 그 사람의 셸을 nologin 으로 바꿔 로그인을 막아 버린다.
        if TAG not in (ent.pw_gecos or ""):
            die(f"계정 {u} 이(가) 이미 있고 이 랩이 만든 것이 아니다.\n"
                f"      그대로 두면 그 계정의 로그인이 막힌다.\n"
                f"      콘솔에서 교육생 계정 이름을 겹치지 않게 바꿀 것.")
        run(["usermod", "-s", nologin, "-c", gecos, u], dry)
        home = Path(ent.pw_dir)
    except KeyError:
        run(["useradd", "-m", "-s", nologin, "-c", gecos, u], dry)
        home = Path(f"/home/{u}")
    run(["passwd", "-l", u], dry)                       # 비밀번호로는 못 들어온다
    write_authorized(u, home, s["key"], dry)
    return True


def write_authorized(u, home, key, dry):
    """authorized_keys 를 통째로 덮어쓴다 (append 가 아니다).

    덮어쓰는 이유: 교육생이 키를 바꾸면 **옛 키가 남아 있으면 안 된다.**
    이어 붙이면 지난 학기 키가 계속 살아 있는 파일이 만들어진다.
    """
    if dry:
        print(f"      → {home}/.ssh/authorized_keys  ({key.split()[0]}, 1개)")
        return
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700, exist_ok=True)
    ent = pwd.getpwnam(u)
    os.chown(ssh, ent.pw_uid, ent.pw_gid)
    os.chmod(ssh, 0o700)
    # 같은 디렉터리에 임시 파일로 쓰고 rename 한다. 도중에 죽어도
    # 반쯤 쓰인 authorized_keys 가 남지 않는다 (= 아무도 못 들어오는 상태).
    fd, tmp = tempfile.mkstemp(dir=str(ssh), prefix=".ak-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        os.chown(tmp, ent.pw_uid, ent.pw_gid)
        os.chmod(tmp, 0o600)
        os.replace(tmp, ssh / "authorized_keys")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def revoke(u, dry):
    """더 이상 DB 에 없는 계정의 접근을 회수한다. 계정 자체는 지우지 않는다."""
    if dry:
        print(f"      → {u}: 키 제거 + 잠금 (계정과 홈은 남긴다)")
        return
    try:
        home = Path(pwd.getpwnam(u).pw_dir)
    except KeyError:
        return
    ak = home / ".ssh" / "authorized_keys"
    if ak.exists():
        ak.unlink()
    subprocess.run(["passwd", "-l", u], capture_output=True, text=True)
    subprocess.run(["usermod", "-s", "/usr/sbin/nologin", u], capture_output=True, text=True)


def sshd_snippet(students, labs, path, dry):
    out = ["# =============================================================================",
           "#  교육생 점프 접속 제한 — 자동 생성: /usr/local/sbin/lab-access-apply",
           "#  직접 수정하지 말 것. 다음 적용 때 덮어쓴다.",
           "# =============================================================================",
           "#  각 계정은 **자기 랩 노드 22번으로만** 나갈 수 있고 셸을 얻지 못한다.",
           "#  ProxyJump(-W)는 세션이 아니라 direct-tcpip 채널이라 셸 없이도 동작한다.",
           ""]
    for s in students:
        targets = " ".join(f"{ip}:22" for ip in labs[s["lab"]])
        out += [f"# {s['user']} — lab{s['lab']}" + (f" ({s['name']})" if s["name"] else ""),
                f"Match User {s['user']}",
                f"    PermitOpen {targets}",
                "    AllowTcpForwarding yes",
                "    ForceCommand /usr/sbin/nologin",
                "    PermitTTY no",
                "    X11Forwarding no",
                "    AllowAgentForwarding no",
                "    PermitTunnel no",
                ""]
    if not students:
        out += ["# (등록된 교육생 키가 없다)", ""]
    # Match 블록은 다음 Match 또는 **파싱 끝까지** 이어진다. sshd_config.d 는 보통
    # sshd_config 앞부분에서 include 되므로, 여기서 닫지 않으면 나머지 전역 설정이
    # 전부 마지막 Match 안으로 들어가 버린다.
    out += ["# 전역 문맥으로 되돌린다. 이 줄을 지우지 말 것 —",
            "# 없으면 아래(=include 이후)의 모든 설정이 마지막 Match 안에 갇힌다.",
            "Match all", ""]
    text = "\n".join(out)
    if dry:
        print(f"      → {path}  ({len(students)}개 Match 블록)")
        return False
    p = Path(path)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    if old == text:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    p.chmod(0o644)
    return True


def reload_sshd(dry):
    """`sshd -t` 를 통과할 때만 reload 한다.

    틀린 설정을 reload 하면 **지금 열린 세션 말고는 아무도 들어오지 못한다.**
    원격 서버에서 그건 되돌릴 방법이 없는 사고다.
    """
    if dry:
        print("      $ sshd -t && systemctl reload ssh")
        return
    t = subprocess.run(["sshd", "-t"], capture_output=True, text=True)
    if t.returncode != 0:
        die("sshd 설정 검사 실패 — reload 하지 않았다 (지금 접속은 그대로 살아 있다)\n"
            f"      {(t.stderr or t.stdout).strip()}")
    for unit in ("ssh", "sshd"):
        r = subprocess.run(["systemctl", "reload", unit], capture_output=True, text=True)
        if r.returncode == 0:
            ok(f"sshd 설정 검사 통과 · {unit} reload")
            return
    warn("reload 하지 못했다 — 손으로 `systemctl reload ssh` 할 것")


def main():
    ap = argparse.ArgumentParser(description="교육생 점프 계정 적용 (root 전용)")
    ap.add_argument("--dry-run", action="store_true", help="계산만 하고 아무것도 바꾸지 않는다")
    # 콘솔이 "이 버튼을 보여도 되는가" 를 물을 때 쓴다. 아무것도 읽지 않고 바로 끝난다 —
    # 이 물음은 화면을 그릴 때마다 나오므로 정책·DB 를 훑어서는 안 된다.
    ap.add_argument("--probe", action="store_true",
                    help="sudo 로 여기까지 올 수 있는지만 확인하고 끝낸다")
    a = ap.parse_args()
    if a.probe:
        print("ok")
        return
    if os.geteuid() != 0 and not a.dry_run:
        die("root 로 실행할 것 (콘솔은 sudo 로 부른다)")

    pol = load_policy()
    labs = {str(k): v for k, v in pol["labs"].items()}
    students, bad = load_students(pol["db"], labs)

    print("교육생 점프 계정 적용" + ("  (연습 — 아무것도 바꾸지 않는다)" if a.dry_run else ""))
    for u, why in bad:
        warn(f"건너뜀 {u} — {why}")

    for s in students:
        ensure_account(s, a.dry_run)
        ok(f"{s['user']:<16s} lab{s['lab']}  {s['name']}")

    want = {s["user"] for s in students}
    for u in sorted(our_accounts() - want):
        revoke(u, a.dry_run)
        warn(f"{u:<16s} 접근 회수 (콘솔에서 사라졌거나 키를 지웠다)")

    changed = sshd_snippet(students, labs, pol["sshd_snippet"], a.dry_run)
    if changed or a.dry_run:
        reload_sshd(a.dry_run)
    else:
        ok("sshd 설정 변경 없음 — reload 하지 않았다")

    print(f"\n계정 {len(students)}개 준비됨"
          + (f" · 거부 {len(bad)}개" if bad else "")
          + ("  (연습이었다)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
