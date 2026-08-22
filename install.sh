#!/usr/bin/env bash
# =============================================================================
#  install.sh — 새 Linux 서버 한 대를 "랩 운영 서버"로 만든다.
# =============================================================================
#  Proxmox 호스트가 아니라 **별도의 리눅스 서버**에서 돌리는 것을 전제로 한다.
#  이 서버가 하는 일:  Terraform 으로 VM 을 만들고 · Ansible 로 설정을 넣고 · 웹 콘솔을 띄운다.
#
#    ./install.sh                 설치만
#    ./install.sh --service       설치 + systemd 서비스 등록 (부팅 시 자동 기동)
#    ./install.sh --no-apt        OS 패키지 설치를 건너뛴다 (권한이 없거나 이미 있을 때)
#
#  폐쇄망이면 Terraform zip 을 미리 받아 두고:
#    TERRAFORM_ZIP=/tmp/terraform_1.9.8_linux_amd64.zip ./install.sh
#
#  root 로 실행하지 않는다. sudo 가 필요한 곳에서만 sudo 를 쓴다.
#  (랩 운영 파일이 root 소유가 되면 콘솔이 자기 파일을 못 고친다)
# =============================================================================
set -euo pipefail

TF_VERSION="${TF_VERSION:-1.9.8}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/console/.venv"
DO_APT=1
DO_SERVICE=0
PORT="${PORT:-8080}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[34m'; N=$'\033[0m'
step() { echo; echo "${B}▸ $*${N}"; }
ok()   { echo "  ${G}✔${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✘${N} $*" >&2; exit 1; }

for a in "$@"; do
  case "$a" in
    --service) DO_SERVICE=1 ;;
    --no-apt)  DO_APT=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "모르는 옵션: $a" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || die "root 로 실행하지 말 것. 일반 계정으로 실행하면 필요한 곳에서만 sudo 를 쓴다."

# ---------------------------------------------------------------- 0. 확인
step "환경 확인"
. /etc/os-release 2>/dev/null || die "/etc/os-release 를 읽을 수 없다"
ok "$PRETTY_NAME  ($(uname -m))"
case "${ID_LIKE:-$ID}" in
  *debian*|debian|ubuntu) PKG=apt ;;
  *) PKG=""; warn "Debian/Ubuntu 계열이 아니다 — OS 패키지는 직접 설치할 것"; DO_APT=0 ;;
esac

SUDO=""
if [ "$DO_APT" = 1 ] || [ "$DO_SERVICE" = 1 ] || ! command -v terraform >/dev/null; then
  command -v sudo >/dev/null || die "sudo 가 필요하다"
  sudo -v || die "sudo 권한이 없다. --no-apt 로 돌리고 Terraform 은 직접 설치할 것"
  SUDO=sudo
fi

# ---------------------------------------------------------------- 1. OS 패키지
if [ "$DO_APT" = 1 ]; then
  step "OS 패키지"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq \
      python3 python3-venv python3-pip \
      openssh-client ca-certificates curl unzip rsync
  ok "python3 · venv · ssh · curl · unzip · rsync"
else
  step "OS 패키지 (건너뜀)"
  for b in python3 ssh curl; do
    command -v "$b" >/dev/null || die "$b 가 없다"
  done
  python3 -c 'import venv' 2>/dev/null || die "python3-venv 가 없다"
  ok "필수 명령 확인"
fi

PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' \
  || die "Python $PYV — 3.10 이상이 필요하다"
ok "Python $PYV"

# ---------------------------------------------------------------- 2. Terraform
step "Terraform"
if command -v terraform >/dev/null; then
  ok "이미 설치돼 있다 — $(terraform version | head -1)"
else
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;;
    *) die "지원하지 않는 아키텍처: $(uname -m). Terraform 을 직접 설치할 것" ;;
  esac
  ZIP="${TERRAFORM_ZIP:-}"
  if [ -z "$ZIP" ]; then
    TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
    URL="https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_${ARCH}.zip"
    echo "  내려받는 중: $URL"
    curl -fsSL --retry 3 -o "$TMP/tf.zip" "$URL" \
      || die "내려받지 못했다. 폐쇄망이면 zip 을 미리 받아 TERRAFORM_ZIP=... 로 넘길 것"
    # 배포처의 체크섬으로 검증한다 — 이 파일은 사내 인프라를 만들 권한을 갖는다
    if curl -fsSL --retry 3 -o "$TMP/sums" \
         "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_SHA256SUMS"; then
      ( cd "$TMP" && cp tf.zip "terraform_${TF_VERSION}_linux_${ARCH}.zip" \
        && sha256sum --ignore-missing -c sums >/dev/null 2>&1 ) \
        && ok "체크섬 검증 통과" || die "체크섬이 맞지 않는다 — 설치를 중단한다"
    else
      warn "체크섬 파일을 받지 못했다 (검증 없이 진행)"
    fi
    ZIP="$TMP/tf.zip"
  else
    [ -f "$ZIP" ] || die "TERRAFORM_ZIP 경로에 파일이 없다: $ZIP"
    ok "미리 받아 둔 zip 을 쓴다: $ZIP"
  fi
  $SUDO unzip -oq "$ZIP" terraform -d /usr/local/bin
  $SUDO chmod 755 /usr/local/bin/terraform
  ok "$(terraform version | head -1)  → /usr/local/bin/terraform"
fi

# ---------------------------------------------------------------- 3. 웹 콘솔 venv
step "웹 콘솔 · Ansible (venv)"
# Ansible 도 venv 안에 넣는다. 시스템 파이썬을 건드리지 않고, 버전을 이 저장소가 정한다.
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/console/requirements.txt"
ok "$("$VENV/bin/python" -c 'import fastapi,jinja2;print("fastapi",fastapi.__version__)')"
ok "$("$VENV/bin/ansible" --version | head -1)"

"$VENV/bin/ansible-galaxy" collection install -r "$ROOT/infra/ansible/requirements.yml" >/dev/null
ok "ansible.posix 컬렉션"

# ---------------------------------------------------------------- 4. SSH 키
step "SSH 키"
KEY="$HOME/.ssh/id_ed25519"
if [ -f "$KEY" ]; then
  ok "이미 있다 — $KEY"
else
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -N "" -C "my-network-lab@$(hostname -s)" -f "$KEY" >/dev/null
  ok "새로 만들었다 — $KEY"
fi
echo
echo "  이 서버의 공개키다. ${Y}config/site.local.yml 의 access.ssh_public_keys 에 넣어야${N}"
echo "  Ansible 이 랩 노드에 로그인할 수 있다."
echo
sed 's/^/      /' "$KEY.pub"

# ---------------------------------------------------------------- 5. 설정 파일
step "설정 파일"
LOCAL="$ROOT/config/site.local.yml"
if [ -f "$LOCAL" ]; then
  ok "site.local.yml 이 이미 있다"
else
  cp "$ROOT/config/site.local.yml.example" "$LOCAL"
  chmod 600 "$LOCAL"
  warn "site.local.yml 을 예시에서 만들었다 — ${Y}열어서 사내 값으로 고칠 것${N}"
fi
mkdir -p "$ROOT/var" && chmod 700 "$ROOT/var"
ok "var/ (0700)"

# ---------------------------------------------------------------- 6. systemd
if [ "$DO_SERVICE" = 1 ]; then
  step "systemd 서비스"
  UNIT=/etc/systemd/system/my-network-lab.service
  sed -e "s|@USER@|$(id -un)|g" -e "s|@GROUP@|$(id -gn)|g" \
      -e "s|@ROOT@|$ROOT|g"     -e "s|@PORT@|$PORT|g" \
      "$ROOT/deploy/my-network-lab.service" | $SUDO tee "$UNIT" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now my-network-lab.service
  sleep 2
  if systemctl is-active --quiet my-network-lab.service; then
    ok "실행 중 — http://$(hostname -I | awk '{print $1}'):$PORT"
  else
    warn "기동에 실패했다 — sudo journalctl -u my-network-lab -n 50"
  fi
fi

# ---------------------------------------------------------------- 7. 사전 점검
step "사전 점검"
set +e
python3 "$ROOT/tools/preflight.py" --lab 1
RC=$?
set -e

echo
echo "=============================================================================="
echo " 다음 할 일"
echo "=============================================================================="
cat <<EOF
  1. config/site.local.yml 을 사내 값으로 고친다
       - access.proxmox        Proxmox 주소 · 노드 이름 · 데이터스토어
       - access.ssh_public_keys 위에 찍힌 이 서버의 공개키
       - forbidden             사내에서 이미 쓰는 대역

  2. Proxmox 호스트에서 root 로 한 번 (다른 호스트라 여기서 못 한다)
       ./infra/proxmox-setup.sh                      # 권한 · API 토큰
       ./infra/template/build-golden-template.sh     # 골든 템플릿 VMID 9000

  3. 브라우저로 콘솔에 들어간다 — **여기서부터는 화면에서 끝난다**
       $([ "$DO_SERVICE" = 1 ] && echo "이미 서비스로 떠 있다" || echo "make console 로 띄운다")
       admin / admin  →  비밀번호 변경  →  [연결 설정] 에 Proxmox 토큰 입력
       확인을 누르면 [설치] 화면으로 넘어간다.

       [설치] 화면이 남은 것을 전부 보여 준다:
         · 무엇이 준비됐고 무엇이 안 됐는지 (make doctor 와 같은 검사)
         · 콘솔이 대신 할 수 있는 것은 버튼    (관리망 브리지 · 접속 파일 · 문서)
         · root 가 필요한 것은 복사할 명령     (sudo make mgmt-net 등)

  4. 초록이 되면 랩 화면에서 [랩 생성]. make 를 칠 일은 없다.

  전체 절차: docs/DEPLOY.md
EOF
exit $RC
