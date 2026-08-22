#!/usr/bin/env bash
# =============================================================================
#  pack.sh — 이 저장소를 다른 서버로 옮길 tarball 을 만든다.
# =============================================================================
#  git 원격이 없어도 옮길 수 있게 하기 위한 것이다.
#
#    ./tools/pack.sh                  ← 사내 값(site.local.yml) 제외 (기본)
#    ./tools/pack.sh --with-local     ← site.local.yml 포함 (사내망 안에서만!)
#
#  절대 담지 않는 것:  var/ (계정 DB·API 토큰) · .venv · tfstate · dist/
#  var/ 를 담지 않는 이유: 계정 DB 에 Proxmox API 토큰이 들어 있다. 토큰은
#  옮긴 서버에서 [연결 설정] 으로 다시 넣는다.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="my-network-lab"
OUT="${OUT:-/tmp/$NAME.tar.gz}"
WITH_LOCAL=0
[ "${1:-}" = "--with-local" ] && WITH_LOCAL=1

cd "$ROOT/.."
BASE="$(basename "$ROOT")"

EXCL=(
  --exclude="$BASE/var"                      # 계정 DB · API 토큰 · 진행 상태
  --exclude="$BASE/console/.venv"
  --exclude="$BASE/dist"
  --exclude="$BASE/.git"
  --exclude="$BASE/**/__pycache__"
  --exclude="$BASE/infra/terraform/envs/lab*"   # tfstate — 옮기면 두 서버가 같은 VM 을 관리한다
  --exclude="$BASE/infra/ansible/inventory/lab*"
  --exclude="*.tfstate*"
  --exclude=".terraform"
)
[ "$WITH_LOCAL" = 1 ] || EXCL+=( --exclude="$BASE/config/site.local.yml" )

tar czf "$OUT" "${EXCL[@]}" "$BASE"
echo "생성: $OUT  ($(du -h "$OUT" | cut -f1))"
if [ "$WITH_LOCAL" = 1 ]; then
  echo "⚠ config/site.local.yml 이 들어 있다 — 사내 값이다. 외부로 내보내지 말 것."
else
  echo "  config/site.local.yml 은 빠졌다. 옮긴 서버에서 다시 만들 것"
  echo "  (install.sh 가 예시 파일에서 만들어 준다)"
fi
