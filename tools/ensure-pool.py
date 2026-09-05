#!/usr/bin/env python3
"""
랩 풀을 만든다 — 랩을 배포하기 직전에 한 번.

usage:  python3 tools/ensure-pool.py --lab 1

왜 풀인가
  교육생 콘솔 계정에 권한을 VMID 로 걸면(`/vms/900101`), Proxmox 가 VM 을
  지울 때 그 권한까지 함께 지운다. 랩을 지웠다 다시 만들면 VM 은 같은 번호로
  돌아오는데 권한만 안 돌아온다 — 로그인은 되고 화면은 비어 있다.

  풀은 VM 이 아니라서 랩을 지워도 남는다. 권한은 풀에 한 번 걸어 두고,
  Terraform 이 VM 을 만들 때 `pool_id` 로 풀에 넣는다.

무엇을 하지 않는가
  풀에 **권한을 걸지는 않는다.** 그건 root 만 할 수 있고
  `dist/console-access.sh` 가 한다. 여기서는 그릇만 준비한다.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "console"))
import pve      # noqa: E402


def main(lab):
    try:
        made, pid = pve.ensure_pool(lab)
    except Exception as e:                                  # noqa: BLE001
        sys.exit(f"풀을 준비하지 못했다: {type(e).__name__}: {e}\n"
                 "  토큰에 Pool.Allocate 가 없으면 403 이다. Proxmox 호스트에서\n"
                 "  ./infra/proxmox-setup.sh 를 한 번 실행할 것")
    print(f"풀 {pid} {'생성' if made else '확인'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="랩 풀 준비")
    ap.add_argument("--lab", type=int, required=True)
    main(ap.parse_args().lab)
