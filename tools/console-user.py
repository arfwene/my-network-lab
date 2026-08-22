#!/usr/bin/env python3
"""
웹 콘솔 계정 관리 (SQLite: var/console.db)

  python3 tools/console-user.py add user01 --lab 1 --name "교육생01"
  python3 tools/console-user.py add admin2 --role admin --name "관리자2"
  python3 tools/console-user.py passwd user01
  python3 tools/console-user.py key user01 --file ~/.ssh/id_ed25519.pub   # 공개키 등록
  python3 tools/console-user.py key user01 --clear                        # 등록 해제
  python3 tools/console-user.py keys --lab 1                              # 그 랩에 배포될 키
  python3 tools/console-user.py lab user01 --lab 3       # 랩 재배정 (즉시 반영)
  python3 tools/console-user.py disable user01          # 즉시 차단
  python3 tools/console-user.py enable  user01
  python3 tools/console-user.py unlock  user01          # 로그인 잠금 해제
  python3 tools/console-user.py del user01
  python3 tools/console-user.py list

계정 종류
  admin : 모든 랩 + 계정 관리 + 해설 열람
  user  : 배정받은 랩 하나에 대한 생성·실행·삭제
"""
import argparse
import getpass
import secrets
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "console"))
import labdesign as L
import auth
import db
import sshkeys
import passwords


def ask_password(user):
    """정책을 통과할 때까지 다시 묻는다."""
    print(f"  비밀번호 규칙: {passwords.policy_text()}")
    while True:
        pw = getpass.getpass(f"{user} 비밀번호 (비우면 자동 생성): ")
        if not pw:
            pw = passwords.generate()
            print(f"  생성된 비밀번호: {pw}   ← 본인에게 전달할 것")
            return pw
        errs = passwords.validate(pw, user)
        if not errs:
            if getpass.getpass("  한 번 더: ") != pw:
                print("  두 번 입력이 다르다. 다시.")
                continue
            return pw
        for e in errs:
            print(f"  · {e}")


def check_lab(lab):
    lo, hi = L.SITE["labs"]["id_range"]
    if lab is not None and not (lo <= lab <= hi):
        sys.exit(f"랩 번호는 {lo}~{hi} 범위여야 한다")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="계정 추가")
    a.add_argument("username")
    a.add_argument("--role", default="user", choices=["user", "admin"])
    a.add_argument("--lab", type=int)
    a.add_argument("--name", default="")
    for c, h in [("passwd", "비밀번호 변경"), ("del", "삭제"),
                 ("disable", "즉시 차단"), ("enable", "차단 해제"),
                 ("unlock", "로그인 잠금 해제")]:
        p = sub.add_parser(c, help=h)
        p.add_argument("username")
    lb = sub.add_parser("lab", help="랩 재배정")
    lb.add_argument("username")
    lb.add_argument("--lab", type=int, required=True)
    sub.add_parser("list", help="목록")
    k = sub.add_parser("key", help="SSH 공개키 등록/해제")
    k.add_argument("username")
    k.add_argument("--file", help="공개키 파일 (.pub). 없으면 표준입력에서 읽는다")
    k.add_argument("--clear", action="store_true", help="등록 해제")
    ks = sub.add_parser("keys", help="한 랩에 배포될 키 목록")
    ks.add_argument("--lab", type=int, required=True)
    args = ap.parse_args()

    db.init()

    if args.cmd == "list":
        users = db.list_users()
        if not users:
            print("등록된 계정이 없다.  console-user.py add <id> --lab <N>")
            return
        print(f"  {'계정':14s} {'종류':8s} {'랩':10s} {'상태':16s} {'마지막 로그인':20s} 이름")
        for u in users:
            labs = auth.allowed_labs(u)
            labtxt = str(labs[0]) if len(labs) == 1 else ("전체" if labs else "-")
            state = "차단" if u["disabled"] else ("변경필요" if u["must_change_password"] else "정상")
            lock = db.locked_seconds(u["username"])
            if lock:
                state += f" (잠김 {lock // 60}분{lock % 60}초)"
            print(f"  {u['username']:14s} {auth.ROLE_LABEL[u['role']]:8s} {labtxt:10s} "
                  f"{state:16s} {u['last_login'] or '-':20s} {u['name']}")
        print(f"\n  DB: {db.DB_PATH}")
        return

    if args.cmd == "keys":
        rows = db.lab_keys(args.lab)
        print(f"  lab {args.lab} 노드에 배포될 교육생 키 ({len(rows)}개)")
        for r in rows:
            d = sshkeys.describe(r["key"]) or {}
            print(f"    {r['username']:14s} {d.get('type',''):14s} {d.get('fingerprint','?')}"
                  f"  {r['name']}")
        if not rows:
            print("    (없음 — 교육생이 콘솔 [접속 키] 에서 등록하면 여기 나온다)")
        print("\n  관리자/운영 서버 키는 config/site.yml 의 access.ssh_public_keys 에 있다.")
        return

    if args.cmd == "key":
        if not db.get_user(args.username):
            sys.exit(f"없는 계정: {args.username}")
        if args.clear:
            db.set_ssh_key(args.username, "")
            print(f"{args.username} 의 공개키를 지웠다. 다음 설정 적용 때 노드에서도 사라진다.")
            return
        raw = Path(args.file).read_text() if args.file else sys.stdin.read()
        try:
            norm = sshkeys.normalize(raw)
        except sshkeys.Invalid as e:
            sys.exit(f"거절: {e}")
        db.set_ssh_key(args.username, norm)
        print(f"{args.username} 공개키 등록: {sshkeys.fingerprint(norm)}")
        print("  배정된 랩에만 배포된다. 반영: 콘솔 [설정 적용] 또는 make config LAB=<N>")
        return

    if args.cmd == "add":
        okname, why = auth.valid_username(args.username)
        if not okname:
            sys.exit(f"거부: {why}")
        if db.get_user(args.username):
            sys.exit(f"이미 있는 계정: {args.username}")
        if args.role == "user" and args.lab is None:
            sys.exit("사용자 계정은 --lab 이 필요하다 (배정된 랩만 접근 가능)")
        if args.role == "admin" and args.lab is not None:
            sys.exit("관리자에게는 랩을 배정하지 않는다 (전체 접근)")
        check_lab(args.lab)
        try:
            db.add_user(args.username, auth.hash_password(ask_password(args.username)),
                        role=args.role, lab_id=args.lab, name=args.name)
        except sqlite3.IntegrityError as e:
            sys.exit(f"추가 실패: {e}")
        print(f"추가됨: {args.username} ({args.role}"
              + (f", lab{args.lab}" if args.lab else "") + ")")
        return

    if not db.get_user(args.username):
        sys.exit(f"없는 계정: {args.username}")

    if args.cmd == "passwd":
        db.set_password(args.username, auth.hash_password(ask_password(args.username)))
        print("변경됨")
    elif args.cmd == "lab":
        check_lab(args.lab)
        db.set_lab(args.username, args.lab)
        print(f"{args.username} → lab{args.lab} 재배정. "
              f"이미 로그인한 세션에도 즉시 반영된다.")
    elif args.cmd == "unlock":
        db.clear_failures(args.username)
        print("로그인 잠금 해제됨")
    elif args.cmd in ("disable", "enable"):
        if args.cmd == "disable" and db.count_admins(exclude=args.username) == 0 \
                and db.get_user(args.username)["role"] == "admin":
            sys.exit("마지막 관리자 계정은 막을 수 없다")
        db.set_disabled(args.username, args.cmd == "disable")
        print(f"{args.username} {'차단됨' if args.cmd == 'disable' else '차단 해제'}. "
              f"진행 중인 세션도 즉시 막힌다.")
    elif args.cmd == "del":
        if db.get_user(args.username)["role"] == "admin" \
                and db.count_admins(exclude=args.username) == 0:
            sys.exit("마지막 관리자 계정은 지울 수 없다")
        db.delete_user(args.username)
        print("삭제됨")


if __name__ == "__main__":
    main()
