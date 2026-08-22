#!/usr/bin/env python3
"""
root 헬퍼가 읽을 정책 파일을 만든다 — 표준 출력으로 JSON.

usage:  python3 tools/gen-policy.py            (install.sh 가 sudo tee 로 받는다)
설치 위치: /etc/my-network-lab/policy.json     (root:root 0644)

왜 별도 파일인가
  /usr/local/sbin/lab-access-apply 는 root 로 돈다. 그 프로그램이 저장소의
  파일을 읽으면, 저장소를 쓸 수 있는 콘솔 계정이 곧 root 가 된다.
  그래서 헬퍼가 필요로 하는 값(DB 경로 · 랩별 노드 주소)만 뽑아
  **root 만 쓸 수 있는 자리**에 미리 놓아 둔다.

랩 구성(design/ipam.yml, 랩 수)을 바꾸면 다시 만들어야 한다.
  python3 tools/gen-policy.py | sudo tee /etc/my-network-lab/policy.json >/dev/null
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L      # noqa: E402

SNIPPET = "/etc/ssh/sshd_config.d/60-lab-jump.conf"


def build():
    lo, _hi = L.IPAM["labs"]["id_range"]
    labs = range(lo, L.IPAM["labs"]["default_count"] + 1)
    return {
        "_comment": "자동 생성: tools/gen-policy.py — 직접 수정하지 말 것",
        "db": str(L.ROOT / "var/console.db"),
        "sshd_snippet": SNIPPET,
        # 랩별 관리망 노드 주소. sshd 의 PermitOpen 에 그대로 들어간다.
        "labs": {str(n): [L.mgmt_ip(n, x["name"]) for x in L.TOPO["nodes"]] for n in labs},
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
