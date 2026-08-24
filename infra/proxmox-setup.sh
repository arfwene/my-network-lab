#!/usr/bin/env bash
# =============================================================================
#  Proxmox 쪽 준비 — 호스트에서 root 로 한 번 실행한다.
# =============================================================================
#  역할 · 사용자 · API 토큰을 만들고 **실제로 권한이 붙었는지 확인**한다.
#
#    ./infra/proxmox-setup.sh                 역할·사용자 확인/생성 + 토큰 발급
#    ./infra/proxmox-setup.sh --show          지금 상태만 점검 (아무것도 안 바꾼다)
#    ./infra/proxmox-setup.sh --new-token     토큰만 다시 발급
#
#  왜 스크립트인가
#    권한을 손으로 넣으면 빠뜨려도 티가 안 난다. Terraform 이 브리지를 만들다
#    HTTP 403 으로 멈추고, 무엇이 없는지는 알려주지 않는다.
#    여기서 만들고, 여기서 확인한다.
#
#  가장 흔한 사고: 웹 UI 로 토큰을 만들면 **권한 분리(privilege separation)가 기본으로
#  켜진다.** 그러면 사용자에게 준 역할을 토큰이 물려받지 않아 무슨 짓을 해도 403 이다.
#  이 스크립트는 --privsep 0 으로 만들고, 이미 있으면 꺼 준다.
# =============================================================================
set -euo pipefail

ROLE=LabProvision
USER_ID=terraform@pve
TOKEN=lab
MODE=create

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[34m'; N=$'\033[0m'
ok()   { echo "  ${G}✔${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
bad()  { echo "  ${R}✘${N} $*"; }
die()  { bad "$*"; exit 1; }
step() { echo; echo "${B}▸ $*${N}"; }

for a in "$@"; do
  case "$a" in
    --show)      MODE=show ;;
    --new-token) MODE=token ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "모르는 옵션: $a" >&2; exit 1 ;;
  esac
done

command -v pveum >/dev/null || { echo "pveum 이 없다. Proxmox 호스트에서 실행할 것" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "root 로 실행할 것" >&2; exit 1; }

NODE=$(hostname -s)

# 이 랩이 쓰는 권한. console/pve.py 의 REQUIRED_PRIVS 와 짝을 이룬다.
#   Sys.Modify        리눅스 브리지 생성 ← 이게 없으면 VM 은 생겨도 랜선이 안 생긴다
#   VM.Config.Cloudinit  관리망 주소·SSH 키 주입
PRIVS="VM.Allocate,VM.Clone,VM.Config.CPU,VM.Config.Disk,VM.Config.Memory,\
VM.Config.Network,VM.Config.Options,VM.Config.Cloudinit,VM.Monitor,VM.PowerMgmt,\
VM.Audit,Datastore.Allocate,Datastore.AllocateSpace,Datastore.Audit,\
Sys.Audit,Sys.Console,Sys.Modify,SDN.Use"

# ---------------------------------------------------------------- 확인 함수
has_priv() {   # has_priv <경로> <권한>
  pveum user token permissions "$USER_ID" "$TOKEN" --path "$1" 2>/dev/null \
    | grep -qw "$2"
}

verify() {
  local fail=0
  step "권한 확인 (토큰이 실제로 무엇을 할 수 있는가)"
  for pair in "/nodes/$NODE|Sys.Modify|리눅스 브리지 생성" \
              "/nodes/$NODE|Sys.Audit|노드 상태 조회" \
              "/vms|VM.Allocate|VM 생성" \
              "/vms|VM.Clone|템플릿 복제" \
              "/vms|VM.Config.Cloudinit|관리망·SSH 키 주입"; do
    IFS='|' read -r path priv why <<< "$pair"
    if has_priv "$path" "$priv"; then
      ok "$path  $priv  ($why)"
    else
      bad "$path  $priv  ($why) ← 없다"
      fail=1
    fi
  done
  return $fail
}

# ---------------------------------------------------------------- 현재 상태
step "현재 상태"
echo "  노드: $NODE"
pveum role list --output-format json 2>/dev/null | grep -q "\"$ROLE\"" \
  && ok "역할 $ROLE 있음" || warn "역할 $ROLE 없음"
pveum user list --output-format json 2>/dev/null | grep -q "\"$USER_ID\"" \
  && ok "사용자 $USER_ID 있음" || warn "사용자 $USER_ID 없음"
if pveum user token list "$USER_ID" --output-format json 2>/dev/null | grep -q "\"$TOKEN\""; then
  PRIVSEP=$(pveum user token list "$USER_ID" --output-format json 2>/dev/null \
            | tr ',' '\n' | grep -A1 "\"$TOKEN\"" | grep -o '"privsep":[01]' | grep -o '[01]$' || echo "?")
  if [ "$PRIVSEP" = "1" ]; then
    warn "토큰 $TOKEN 있음 — ${R}권한 분리가 켜져 있다${N} (이 상태로는 무조건 403)"
  else
    ok "토큰 $TOKEN 있음 (권한 분리 꺼짐)"
  fi
else
  warn "토큰 $TOKEN 없음"
fi

if [ "$MODE" = show ]; then
  verify && { echo; echo "${G}준비 끝. 콘솔 [연결 설정] 에 토큰을 넣고 make doctor 를 돌릴 것.${N}"; exit 0; }
  echo; echo "${R}권한이 모자란다. 옵션 없이 다시 실행하면 고친다:${N}  $0"
  exit 1
fi

# ---------------------------------------------------------------- 역할
#  Proxmox 는 판올림마다 권한 이름이 늘고 준다 — PVE 9 는 VM.Monitor 를 없앴다.
#  목록을 박아 두면 그때마다 설치가 여기서 멈춘다.
#  거부당한 이름만 빼고 다시 물어본다. 다른 이유의 실패는 그대로 보여 준다.
role_apply() {   # role_apply <add|modify>
  local verb="$1" privs="$PRIVS" out bad
  while :; do
    if out=$(pveum role "$verb" "$ROLE" -privs "$privs" 2>&1); then
      PRIVS="$privs"
      return 0
    fi
    bad=$(printf '%s\n' "$out" | sed -n "s/.*invalid privilege '\([^']*\)'.*/\1/p" | head -1)
    if [ -z "$bad" ]; then
      printf '%s\n' "$out" >&2
      return 1
    fi
    warn "이 Proxmox 는 권한 '$bad' 를 모른다 — 빼고 다시 시도한다"
    privs=$(printf '%s' "$privs" | tr ',' '\n' | grep -vFx "$bad" | paste -sd,)
    [ -n "$privs" ] || { echo "남은 권한이 없다" >&2; return 1; }
  done
}

step "역할 $ROLE"
if pveum role list --output-format json 2>/dev/null | grep -q "\"$ROLE\""; then
  role_apply modify || die "역할 권한을 맞추지 못했다"
  ok "권한 목록을 최신으로 맞췄다"
else
  role_apply add || die "역할을 만들지 못했다"
  ok "만들었다"
fi

# ---------------------------------------------------------------- 사용자 · ACL
step "사용자 $USER_ID"
pveum user list --output-format json 2>/dev/null | grep -q "\"$USER_ID\"" \
  || { pveum user add "$USER_ID"; ok "만들었다"; }
pveum aclmod / -user "$USER_ID" -role "$ROLE"
ok "/ 에 $ROLE 부여 (하위로 전파된다)"

# ---------------------------------------------------------------- 토큰
step "API 토큰 $USER_ID!$TOKEN"
NEW=""
if pveum user token list "$USER_ID" --output-format json 2>/dev/null | grep -q "\"$TOKEN\""; then
  if [ "$MODE" = token ]; then
    pveum user token remove "$USER_ID" "$TOKEN" >/dev/null
    NEW=$(pveum user token add "$USER_ID" "$TOKEN" --privsep 0 --output-format json)
    ok "다시 발급했다"
  else
    # 있는 토큰의 비밀값은 다시 볼 수 없다. 권한 분리만 꺼 준다.
    pveum user token modify "$USER_ID" "$TOKEN" --privsep 0 >/dev/null
    ok "이미 있다 — 권한 분리를 껐다 (비밀값은 다시 볼 수 없다)"
    warn "비밀값을 모르면 다시 발급할 것:  $0 --new-token"
  fi
else
  NEW=$(pveum user token add "$USER_ID" "$TOKEN" --privsep 0 --output-format json)
  ok "발급했다"
fi

# ---------------------------------------------------------------- 검증
if verify; then
  echo; echo "${G}준비 끝.${N}"
else
  echo; echo "${R}권한이 아직 모자란다. 위 목록을 보고 pveum 으로 확인할 것.${N}"
  echo "  pveum user token permissions $USER_ID $TOKEN --path /nodes/$NODE"
  exit 1
fi

if [ -n "$NEW" ]; then
  SECRET=$(echo "$NEW" | grep -o '"value":"[^"]*"' | cut -d'"' -f4)
  echo
  echo "=============================================================================="
  echo " 토큰 — ${Y}이 화면에서만 볼 수 있다. 다시 볼 수 없다.${N}"
  echo "=============================================================================="
  echo "   토큰 ID : $USER_ID!$TOKEN"
  echo "   비밀값  : $SECRET"
  echo
  echo " 콘솔 [관리자 → 연결 설정] 에 넣는다. 파일에 적지 말 것."
  echo " (콘솔은 var/console.db 0600 에만 두고 실행 시 환경변수로만 넘긴다)"
  echo "=============================================================================="
fi
