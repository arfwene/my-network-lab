#!/usr/bin/env python3
"""
Proxmox 자격 증명을 환경변수로 넣고 명령을 실행한다.

  python3 tools/with-pve-env.py -- terraform apply

왜 필요한가
  API 토큰은 `var/console.db` 에만 둔다 (0600). 웹 콘솔은 작업을 띄울 때
  `pve.env()` 로 환경변수에 실어 넘기지만, `make deploy` 같은 CLI 타깃은
  콘솔을 거치지 않아 토큰이 없다 — Terraform 이 "credentials" 오류로 멈춘다.

  그렇다고 토큰을 tfvars 나 .env 로 내보내면 안 된다. 생성물은 재생성되고 복사된다.
  그래서 이 래퍼가 **실행 순간에만** 환경변수로 넣고 exec 한다.

절대 하지 않는 것
  · 토큰을 파일로 쓰지 않는다
  · 토큰을 화면에 찍지 않는다 (`eval $(...)` 형태를 일부러 만들지 않았다 —
    그러면 셸 히스토리와 프로세스 목록에 남는다)

토큰이 없으면 무엇을 해야 하는지 알려주고 멈춘다.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))


def main(argv):
    if not argv:
        sys.exit("usage: with-pve-env.py -- <명령> [인자...]")

    env = dict(os.environ)

    # 셸에서 이미 넣어 뒀다면 그대로 존중한다 — 임시로 다른 토큰을 쓰는 경우가 있다.
    if env.get("PROXMOX_VE_API_TOKEN"):
        print("[pve-env] 셸의 PROXMOX_VE_API_TOKEN 을 그대로 쓴다", file=sys.stderr)
    else:
        if not (ROOT / "var/console.db").exists():
            sys.exit(
                "Proxmox API 토큰이 없다 (var/console.db 미생성).\n"
                "  웹 콘솔을 띄우고 [관리자 → 연결 설정] 에서 토큰을 넣거나,\n"
                "  이 셸에서만: export PROXMOX_VE_API_TOKEN='terraform@pve!lab=...'")
        try:
            import pve                                 # noqa: PLC0415
            extra = pve.env()
        except Exception as e:                         # noqa: BLE001
            sys.exit(f"연결 설정을 읽지 못했다: {type(e).__name__}: {e}")
        if not extra.get("PROXMOX_VE_API_TOKEN"):
            sys.exit(
                "Proxmox API 토큰이 저장돼 있지 않다.\n"
                "  웹 콘솔 [관리자 → 연결 설정] 에서 토큰 ID 와 비밀값을 넣을 것.\n"
                "  (토큰은 var/console.db 에만 저장되고 파일로 나가지 않는다)\n"
                "  또는 이 셸에서만: export PROXMOX_VE_API_TOKEN='terraform@pve!lab=...'")
        env.update(extra)
        tid = extra["PROXMOX_VE_API_TOKEN"].split("=", 1)[0]
        print(f"[pve-env] {extra.get('PROXMOX_VE_ENDPOINT', '?')} · 토큰 {tid}", file=sys.stderr)

    try:
        os.execvpe(argv[0], argv, env)
    except FileNotFoundError:
        sys.exit(f"명령을 찾을 수 없다: {argv[0]}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--":
        a = a[1:]
    main(a)
