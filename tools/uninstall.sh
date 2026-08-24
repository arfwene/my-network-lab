#!/usr/bin/env bash
# =============================================================================
#  uninstall.sh — 랩 운영 서버에서 이 랩을 걷어낸다.
# =============================================================================
#    ./tools/uninstall.sh --dry-run     무엇을 지울지 보여만 준다 (아무것도 안 지운다)
#    ./tools/uninstall.sh               확인을 받고 지운다
#    ./tools/uninstall.sh --keep-labs   Proxmox 의 랩 VM 은 그대로 둔다
#    ./tools/uninstall.sh --keep-repo   저장소 디렉터리는 남긴다 (재설치가 빠르다)
#
#  ─ 순서가 중요하다 ──────────────────────────────────────────────────────────
#  저장소를 먼저 지우면 **tfstate 와 API 토큰이 같이 사라진다.** 그러면 Proxmox 에
#  VM 13대 × 랩 수가 고아로 남고, 다음 배포는 VMID 충돌로 막힌다. 손으로 지우는
#  수밖에 없어진다. 그래서 이 스크립트는 항상
#
#      ① 랩 VM 파괴 → ② 관리망 브리지 → ③ 서버 설정 → ④ 저장소
#
#  순으로 간다. ①이 실패하면 ④로 넘어가지 않는다.
#  ②는 무언가 물려 있으면 되지 않는다 — 그래서 먼저 물어보고, 지운 뒤 다시 물어본다.
#  종료 코드는 근거로 쓰지 않는다. tfstate 가 비는 것과 브리지가 사라지는 것은 다른 사건이다.
#
#  ─ 지우지 않는 것 ──────────────────────────────────────────────────────────
#  · 골든 템플릿 VMID 9000  — 다시 만드는 데 가장 오래 걸린다. 재설치 때 그대로 쓴다.
#  · Proxmox 의 역할·계정·토큰 — 이 서버에서 지울 수 없다(pveum 은 Proxmox 에만 있다).
#    대신 Proxmox 에서 돌릴 스크립트를 만들어 준다.
#  · 우리가 만들지 않은 것 — 계정도 파일도 표시를 확인하고 나서만 지운다.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0; KEEP_LABS=0; KEEP_REPO=0; ASSUME_YES=0
HELPER=/usr/local/sbin/lab-access-apply
POLDIR=/etc/my-network-lab
SUDOERS=/etc/sudoers.d/my-network-lab
SNIPPET=/etc/ssh/sshd_config.d/60-lab-jump.conf
NETPLAN=/etc/netplan/60-lab-mgmt.yaml
UNIT=/etc/systemd/system/my-network-lab.service
TAG="my-network-lab"
OUT="$HOME/proxmox-cleanup.sh"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[34m'; N=$'\033[0m'
step() { echo; echo "${B}▸ $*${N}"; }
ok()   { echo "  ${G}✔${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✘${N} $*" >&2; exit 1; }
plan() { echo "  ${Y}·${N} $*"; }

for a in "$@"; do
  case "$a" in
    --dry-run)   DRY=1 ;;
    --keep-labs) KEEP_LABS=1 ;;
    --keep-repo) KEEP_REPO=1 ;;
    --yes)       ASSUME_YES=1 ;;
    -h|--help)   sed -n '2,30p' "$0"; exit 0 ;;
    *) die "모르는 옵션: $a" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || die "root 로 실행하지 말 것. 설치할 때와 같은 계정으로 실행한다."
run() { if [ "$DRY" = 1 ]; then echo "      \$ $*"; else "$@"; fi; }

# ============================================================ 0. 무엇이 있는가
step "지금 이 서버에 남아 있는 것"

LAB_ENVS=()
for d in "$ROOT"/infra/terraform/envs/lab*; do
  [ -d "$d" ] || continue
  if [ -s "$d/terraform.tfstate" ] && grep -q '"resources": \[$' "$d/terraform.tfstate" 2>/dev/null \
     && [ "$(grep -c '"type":' "$d/terraform.tfstate" 2>/dev/null || echo 0)" -gt 0 ]; then
    LAB_ENVS+=("$d"); plan "$(basename "$d")  — Proxmox 에 자원이 남아 있다"
  fi
done
[ ${#LAB_ENVS[@]} -gt 0 ] || ok "랩 tfstate 에 남은 자원 없음"

MGMT_ENV="$ROOT/infra/terraform/envs/mgmt"
MGMT_LEFT=0
if [ -s "$MGMT_ENV/terraform.tfstate" ] && \
   [ "$(grep -c '"type":' "$MGMT_ENV/terraform.tfstate" 2>/dev/null || echo 0)" -gt 0 ]; then
  MGMT_LEFT=1; plan "관리망 브리지 (envs/mgmt tfstate)"
fi

# 우리가 만든 점프 계정만. GECOS 표시가 없는 계정은 남의 것이다.
mapfile -t JUMPERS < <(getent passwd | awk -F: -v t="$TAG" '$5 ~ t {print $1}' | sort)
[ ${#JUMPERS[@]} -eq 0 ] || plan "점프 계정 ${#JUMPERS[@]}개: ${JUMPERS[*]}"

for f in "$UNIT" "$SNIPPET" "$SUDOERS" "$HELPER" "$NETPLAN"; do
  [ -e "$f" ] && plan "$f"
done
[ -d "$POLDIR" ] && plan "$POLDIR/"
[ "$KEEP_REPO" = 0 ] && plan "저장소 $ROOT  (var/console.db 의 API 토큰 포함)"

echo
echo "  ${B}지우지 않는 것${N}"
echo "    · 골든 템플릿 VMID 9000 (재설치 때 그대로 쓴다)"
echo "    · Proxmox 의 역할·계정·토큰 → $OUT 를 만들어 준다"

# ============================================================ 1. 확인
if [ "$DRY" = 1 ]; then
  echo; echo "${B}연습이었다. 아무것도 지우지 않았다.${N}"; exit 0
fi
if [ "$ASSUME_YES" = 0 ]; then
  echo
  echo "${R}위 항목을 지운다. 되돌릴 수 없다.${N}"
  read -r -p "  계속하려면 이 서버의 호스트명을 그대로 입력할 것 [$(hostname -s)]: " ANS
  [ "$ANS" = "$(hostname -s)" ] || die "입력이 다르다. 중단했다."
fi

# ============================================================ 2. 랩 VM 파괴
#  가장 먼저 한다. 여기가 실패하면 저장소를 지우지 않고 멈춘다 —
#  tfstate 를 잃으면 Proxmox 에 VM 이 고아로 남는다.
if [ "$KEEP_LABS" = 0 ] && [ ${#LAB_ENVS[@]} -gt 0 ]; then
  step "랩 VM 파괴 (Proxmox)"
  command -v terraform >/dev/null || die "terraform 이 없다. --keep-labs 로 건너뛰거나 설치할 것"
  for d in "${LAB_ENVS[@]}"; do
    echo "  $(basename "$d") ..."
    ( cd "$d" && python3 "$ROOT/tools/with-pve-env.py" -- \
        terraform destroy -auto-approve -input=false ) \
      || die "$(basename "$d") 파괴 실패 — 저장소를 지우지 않고 멈춘다.
      Proxmox 연결(토큰)을 먼저 고칠 것. 지금 저장소를 지우면 VM 이 고아가 된다."
    ok "$(basename "$d") 파괴됨"
  done
elif [ "$KEEP_LABS" = 1 ]; then
  warn "랩 VM 은 그대로 둔다 (--keep-labs). 다음 배포에서 VMID 충돌로 막힐 수 있다."
fi

# ============================================================ 3. 관리망 브리지
#  여기서 두 번 데었다 (docs/TODO.md 1).
#    · terraform destroy 가 0 으로 끝나는 것과 브리지가 실제로 사라지는 것은 다른 사건이다.
#      tfstate 만 비고 호스트에는 그대로 남아 있을 수 있다.
#    · 무언가 물려 있으면 애초에 사라지지 않는다. 운영 서버 VM 의 트렁크 NIC 이 그렇다.
#  그래서 **묻고 → 지우고 → 다시 물어본다.** 종료 코드는 근거로 쓰지 않는다.
BRIDGE=$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/tools')
import labdesign as L; print(L.mgmt_bridge_name())" 2>/dev/null || echo vmbr9)

pve_bridge() {   # pve_bridge --check|--attached  → 0 없음/안물림 · 1 있음/물림 · 2 못물어봄
  python3 "$ROOT/tools/pve-bridge.py" "$1" "$BRIDGE" 2>&1
}

if [ "$KEEP_LABS" = 0 ] && [ "$MGMT_LEFT" = 1 ]; then
  step "관리망 브리지 $BRIDGE"

  ATTACHED=$(pve_bridge --attached) && RC=0 || RC=$?
  if [ "$RC" = 1 ]; then
    warn "아직 물려 있는 VM 이 있다 — 이 상태로는 브리지가 지워지지 않는다:"
    echo "$ATTACHED" | sed 's/^/      /'
    warn "  Proxmox 웹에서 해당 VM 의 NIC 을 먼저 떼고 이 스크립트를 다시 돌릴 것"
    warn "  (운영 서버 VM 의 트렁크 NIC 이 대개 여기 걸린다)"
    warn "브리지 파괴는 건너뛴다."
  else
    [ "$RC" = 2 ] && warn "무엇이 물려 있는지 Proxmox 에 물어보지 못했다 — 그대로 시도한다"
    ( cd "$MGMT_ENV" && python3 "$ROOT/tools/with-pve-env.py" -- \
        terraform destroy -auto-approve -input=false ) \
      || warn "terraform destroy 가 실패했다"

    # 여기가 핵심이다. 성공했다고 믿지 않고 다시 물어본다.
    RES=$(pve_bridge --check) && RC=0 || RC=$?
    case "$RC" in
      0) ok "$BRIDGE 제거 확인됨 (Proxmox 에 다시 물어봤다)" ;;
      1) warn "$RES"
         warn "  tfstate 는 비었는데 호스트에는 남아 있다. 대개 둘 중 하나다:"
         warn "   · Proxmox 웹 [System → Network] 에 **적용 대기 중인 변경**이 있다"
         warn "     → [Apply Configuration] 을 누르면 반영된다"
         warn "       (이 스크립트는 호스트 네트워크를 대신 적용하지 않는다 — 원칙이다)"
         warn "   · 무언가 아직 물려 있다 → 위 [Network] 화면에서 확인할 것"
         warn "  남겨 두면 재설치 때 '남의 브리지' 로 오인돼 배포가 막힌다." ;;
      *) warn "제거됐는지 Proxmox 에 확인하지 못했다: $RES"
         warn "  Proxmox 웹 [System → Network] 에서 $BRIDGE 가 없는지 직접 볼 것" ;;
    esac
  fi
fi

# ============================================================ 4. 콘솔 서비스
step "웹 콘솔 서비스"
if systemctl list-unit-files 2>/dev/null | grep -q '^my-network-lab\.service'; then
  run sudo systemctl disable --now my-network-lab.service || true
  run sudo rm -f "$UNIT"
  run sudo systemctl daemon-reload
  ok "서비스 제거"
else
  ok "등록된 서비스 없음"
fi

# ============================================================ 5. 점프 계정
step "교육생 점프 계정"
if [ ${#JUMPERS[@]} -eq 0 ]; then
  ok "없음"
else
  for u in "${JUMPERS[@]}"; do
    # 표시를 한 번 더 확인한다. 그 사이에 바뀌었을 수도 있다.
    if getent passwd "$u" | cut -d: -f5 | grep -q "$TAG"; then
      run sudo userdel -r "$u" 2>/dev/null || run sudo userdel "$u" || true
      ok "$u 제거"
    else
      warn "$u — 표시가 없다. 우리 계정이 아니므로 건드리지 않는다"
    fi
  done
fi

# ============================================================ 6. sshd 제한
step "sshd 설정 조각"
if [ -e "$SNIPPET" ]; then
  run sudo rm -f "$SNIPPET"
  # 반드시 검사하고 reload 한다. 틀린 상태로 reload 하면 아무도 못 들어온다.
  if [ "$DRY" = 1 ]; then
    echo "      \$ sudo sshd -t && sudo systemctl reload ssh"
  elif sudo sshd -t; then
    sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true
    ok "$SNIPPET 제거 · sshd reload"
  else
    die "sshd 설정 검사 실패 — reload 하지 않았다. 지금 접속은 살아 있다. 직접 확인할 것"
  fi
else
  ok "없음"
fi

# ============================================================ 7. root 헬퍼
step "점프 계정 적용 헬퍼 · sudoers"
for f in "$SUDOERS" "$HELPER"; do
  if [ -e "$f" ]; then run sudo rm -f "$f"; ok "$f 제거"; fi
done
[ -d "$POLDIR" ] && { run sudo rm -rf "$POLDIR"; ok "$POLDIR 제거"; }

# ============================================================ 8. 관리망 인터페이스
step "이 서버의 관리망 VLAN 인터페이스"
if [ -e "$NETPLAN" ]; then
  run sudo rm -f "$NETPLAN"
  if [ "$DRY" = 1 ]; then echo "      \$ sudo netplan apply"; else
    sudo netplan apply && ok "$NETPLAN 제거 · netplan 적용"
  fi
  warn "Proxmox 쪽 운영 서버 VM 의 트렁크 NIC 은 남아 있다 — 웹에서 net1 을 떼면 된다"
else
  ok "없음"
fi

# ============================================================ 9. Proxmox 정리 스크립트
step "Proxmox 에서 마저 지울 것"
cat > "$OUT" <<'PVEEOF'
#!/bin/bash
# =============================================================================
#  Proxmox 호스트에서 root 로 실행 — my-network-lab 흔적 제거
#  tools/uninstall.sh 가 만들었다.
# =============================================================================
#  골든 템플릿(VMID 9000)은 지우지 않는다. 다시 만드는 데 가장 오래 걸리고
#  재설치 때 그대로 쓴다. 정말 지우려면 맨 아래 주석을 풀 것.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "root 로 실행할 것" >&2; exit 1; }
command -v pveum >/dev/null || { echo "Proxmox 호스트에서 실행할 것" >&2; exit 1; }

echo "▸ 남아 있는 랩 VM (태그 my-network-lab)"
qm list | awk 'NR>1{print $1}' | while read -r id; do
  if qm config "$id" 2>/dev/null | grep -q 'tags:.*my-network-lab'; then
    echo "  VM $id — 아직 있다.  지우려면:  qm stop $id; qm destroy $id"
  fi
done

echo "▸ 콘솔 계정 · 역할 제거"
for u in $(pveum user list --output-format json | grep -o '"userid":"lab[0-9]*-console@pve"' | cut -d'"' -f4); do
  pveum user delete "$u" && echo "  $u 삭제"
done
pveum role list --output-format json | grep -q '"LabConsole"' && pveum role delete LabConsole && echo "  역할 LabConsole 삭제"

echo "▸ Terraform 계정 · 역할 제거"
pveum user list --output-format json | grep -q '"terraform@pve"' \
  && { pveum user delete terraform@pve && echo "  terraform@pve 삭제 (토큰도 함께)"; }
pveum role list --output-format json | grep -q '"LabProvision"' \
  && { pveum role delete LabProvision && echo "  역할 LabProvision 삭제"; }

echo "▸ 관리망 브리지"
#  세 곳을 따로 본다. 셋이 다를 수 있고, 다르다는 것 자체가 정보다.
#    interfaces      = 적용된 설정
#    interfaces.new  = 아직 [Apply Configuration] 을 누르지 않은 변경
#    ip link         = 지금 커널에 실제로 있는 것
BR=vmbr9
LEFT=0
if grep -qE "^[[:space:]]*(auto|iface)[[:space:]]+$BR([[:space:]]|$)" /etc/network/interfaces 2>/dev/null; then
  echo "  /etc/network/interfaces 에 $BR 가 있다"; LEFT=1
fi
if [ -f /etc/network/interfaces.new ]; then
  echo "  적용 대기 중인 변경이 있다 (/etc/network/interfaces.new)"
  echo "    → 웹 [System -> Network] 의 [Apply Configuration] 을 눌러야 반영된다"
  grep -qE "^[[:space:]]*(auto|iface)[[:space:]]+$BR([[:space:]]|$)" /etc/network/interfaces.new \
    && echo "    (반영해도 $BR 는 남는다)" \
    || echo "    (반영하면 $BR 가 사라진다)"
  LEFT=1
fi
if ip link show "$BR" >/dev/null 2>&1; then
  echo "  커널에 $BR 가 살아 있다 (ip link)"; LEFT=1
  ATT=$(bridge link 2>/dev/null | grep -c "master $BR" || true)
  [ "${ATT:-0}" -gt 0 ] && echo "    포트 ${ATT}개가 물려 있다 — 이게 남아 있으면 지워지지 않는다"
fi
if [ "$LEFT" = 0 ]; then
  echo "  없음"
else
  echo "  운영 서버 VM 의 트렁크 NIC 을 먼저 떼고, 웹 [System -> Network] 에서 지울 것."
  echo "  이 스크립트는 호스트 네트워크를 대신 바꾸지 않는다."
fi

echo
echo "골든 템플릿 VMID 9000 은 남겨 뒀다 (재설치 때 그대로 쓴다)."
echo "정말 지우려면:  qm destroy 9000"
PVEEOF
chmod 750 "$OUT"
ok "$OUT 를 만들었다 — Proxmox 호스트로 옮겨 root 로 실행할 것"

# ============================================================ 10. 저장소
if [ "$KEEP_REPO" = 0 ]; then
  step "저장소"
  warn "$ROOT 를 지운다 — var/console.db 의 API 토큰도 함께 사라진다"
  if [ "$DRY" = 0 ]; then
    cd /
    rm -rf "$ROOT"
  fi
  ok "제거됨"
else
  step "저장소"
  ok "남긴다 (--keep-repo). 재설치는 git pull 후 ./install.sh 로 이어서 하면 된다"
  warn "var/console.db 에 이전 계정과 API 토큰이 그대로 있다 —"
  warn "  정말 처음부터 하려면:  rm -f $ROOT/var/console.db"
fi

echo
echo "=============================================================================="
echo " 끝났다. 다음"
echo "=============================================================================="
echo "  1. Proxmox 웹에서 운영 서버 VM 의 net1(트렁크 NIC) 제거"
echo "     — 이게 남아 있으면 관리망 브리지가 지워지지 않는다. 그래서 첫 번째다."
echo "  2. $OUT 를 Proxmox 호스트에서 root 로 실행"
echo "     — 브리지가 남았다고 나오면 [System → Network] 에서 지우고 [Apply Configuration]"
echo "  3. 저장소를 다시 받아 ./install.sh --service"
echo "     전체 절차: docs/DEPLOY.md 0절"
