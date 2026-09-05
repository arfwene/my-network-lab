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
#    ./install.sh --no-jump-apply 점프 계정 적용 권한을 설치하지 않는다
#
#  점프 계정 적용 권한은 **기본으로 설치한다.** 예전에는 --jump-apply 를 따로
#  줘야 했는데, 그걸 안 준 서버에서는 교육생이 키를 등록해도 아무 일도 일어나지
#  않았다 — 그리고 화면에는 그 사실이 드러나지 않았다. 기본값이어야 맞다.
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
DO_JUMP=1
PORT="${PORT:-8080}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[34m'; N=$'\033[0m'
step() { echo; echo "${B}▸ $*${N}"; }
ok()   { echo "  ${G}✔${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✘${N} $*" >&2; exit 1; }

for a in "$@"; do
  case "$a" in
    --service) DO_SERVICE=1 ;;
    --jump-apply) DO_JUMP=1 ;;          # 예전 이름 — 이제 기본값이라 아무 일도 하지 않는다
    --no-jump-apply) DO_JUMP=0 ;;
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
if [ "$DO_APT" = 1 ] || [ "$DO_SERVICE" = 1 ] || [ "$DO_JUMP" = 1 ] || ! command -v terraform >/dev/null; then
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
#  --service 를 안 줘도, **이미 설치돼 있으면** 유닛을 새로 쓴다.
#  안 그러면 유닛 자체의 버그(예: NoNewPrivileges 가 sudo 를 막던 것)를 고쳐도
#  `git pull && ./install.sh --no-apt` 로는 영영 전달되지 않는다.
UNIT=/etc/systemd/system/my-network-lab.service
if [ "$DO_SERVICE" = 0 ] && [ -f "$UNIT" ]; then
  DO_SERVICE=1
  command -v sudo >/dev/null && SUDO=sudo
fi
if [ "$DO_SERVICE" = 1 ]; then
  step "systemd 서비스"
  sed -e "s|@USER@|$(id -un)|g" -e "s|@GROUP@|$(id -gn)|g" \
      -e "s|@ROOT@|$ROOT|g"     -e "s|@PORT@|$PORT|g" \
      "$ROOT/deploy/my-network-lab.service" | $SUDO tee "$UNIT" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now my-network-lab.service
  # 이미 돌고 있었다면 enable --now 는 아무것도 하지 않는다 — 새 유닛과 새 코드를
  # 읽히려면 다시 띄워야 한다. (콘솔은 시작할 때 코드를 읽는다)
  $SUDO systemctl restart my-network-lab.service
  sleep 2
  if systemctl is-active --quiet my-network-lab.service; then
    ok "실행 중 — http://$(hostname -I | awk '{print $1}'):$PORT"
  else
    warn "기동에 실패했다 — sudo journalctl -u my-network-lab -n 50"
  fi
fi

# ------------------------------------------------- 6.5 점프 계정 적용 권한 (선택)
#  교육생이 늘 때마다 관리자가 서버에 들어가 `sudo ./dist/jump-access.sh` 를 치는 일을
#  콘솔 버튼으로 옮긴다. 그러려면 콘솔 계정에 sudo 를 줘야 하는데, 순진하게 주면
#
#      콘솔 계정이 dist/jump-access.sh 를 쓸 수 있다
#        + sudo 로 그 파일을 실행할 수 있다
#        = 콘솔 계정이 곧 root 다  (sudo 를 준 의미가 없다)
#
#  그래서 **콘솔 계정이 건드릴 수 없는 것만** 실행하게 한다.
#    · 실행되는 코드는 root 소유 /usr/local/sbin/lab-access-apply 하나뿐이고
#      저장소(tools/, dist/)를 읽지도 실행하지도 않는다
#    · 주소·경로는 root 소유 policy.json 에서 온다
#    · 콘솔에서 오는 것은 DB 의 데이터뿐이고 헬퍼가 이름·공개키를 다시 검증한다
#    · sudoers 규칙에는 인자 자리가 없다 (와일드카드 금지)
if [ "$DO_JUMP" = 1 ]; then
  step "점프 계정 적용 권한"
  HELPER=/usr/local/sbin/lab-access-apply
  MHELPER=/usr/local/sbin/lab-mgmt-apply
  POLDIR=/etc/my-network-lab
  SUDOERS=/etc/sudoers.d/my-network-lab
  ME="$(id -un)"

  $SUDO install -o root -g root -m 0755 "$ROOT/deploy/lab-access-apply.py" "$HELPER"
  ok "$HELPER  (root:root 0755)"

  $SUDO install -o root -g root -m 0755 "$ROOT/deploy/lab-mgmt-apply.py" "$MHELPER"
  ok "$MHELPER  (root:root 0755)"

  $SUDO install -d -o root -g root -m 0755 "$POLDIR"
  python3 "$ROOT/tools/gen-policy.py" | $SUDO tee "$POLDIR/policy.json" >/dev/null
  $SUDO chown root:root "$POLDIR/policy.json"
  $SUDO chmod 0644 "$POLDIR/policy.json"
  ok "$POLDIR/policy.json  (랩 주소 · DB 경로)"

  # visudo -c 로 먼저 검사한다. 깨진 sudoers 를 넣으면 그 서버에서 sudo 자체가 죽는다.
  TMPS=$(mktemp)
  printf '# my-network-lab — 웹 콘솔이 교육생 점프 계정을 적용한다.\n' > "$TMPS"
  printf '# 두 가지 실행만 허용한다: 인자 없이(적용), 그리고 --probe(권한 확인).\n' >> "$TMPS"
  printf '# sudoers 에서 인자를 안 적으면 **모든 인자가 허용된다** — 그래서 ""(빈 인자)로 못 박는다.\n' >> "$TMPS"
  printf '%s ALL=(root) NOPASSWD: %s "", %s --probe\n' "$ME" "$HELPER" "$HELPER" >> "$TMPS"
  printf '# 관리망 연결도 같은 방식이다 — netplan 을 쓰는 부분만 root 로 넘긴다.\n' >> "$TMPS"
  printf '%s ALL=(root) NOPASSWD: %s "", %s --probe, %s --show\n' "$ME" "$MHELPER" "$MHELPER" "$MHELPER" >> "$TMPS"

  # requiretty 는 sudo 1.9.17 에서 아예 사라졌다 (Ubuntu 26.04 이상).
  # 그 버전에서는 "unknown setting" 으로 파일 전체가 문법 오류가 된다.
  # 있으면 명시하고, 없으면 조용히 뺀다 — 어차피 기본값이 off 라 동작은 같다.
  TMPT=$(mktemp)
  cat "$TMPS" > "$TMPT"
  printf 'Defaults!%s !requiretty\n' "$HELPER" >> "$TMPT"
  printf 'Defaults!%s !requiretty\n' "$MHELPER" >> "$TMPT"
  if $SUDO visudo -cf "$TMPT" >/dev/null 2>&1; then
    cat "$TMPT" > "$TMPS"
  fi
  rm -f "$TMPT"

  if $SUDO visudo -cf "$TMPS" >/dev/null; then
    $SUDO install -o root -g root -m 0440 "$TMPS" "$SUDOERS"
    ok "$SUDOERS  ($ME 가 이 프로그램만 root 로 실행)"
  else
    rm -f "$TMPS"
    die "sudoers 조각이 문법 검사를 통과하지 못했다 — 설치하지 않았다"
  fi
  rm -f "$TMPS"

  # 콘솔이 쓰는 것과 **똑같은 방법**으로 확인한다.
  # 예전에는 여기서 --dry-run 을, 콘솔에서는 `sudo -n -l` 을 썼다. 뒤엣것은
  # 권한 목록을 요구하는 것이라 sudo 가 비밀번호를 묻고(verifypw 기본값 all),
  # 그래서 여기서는 "쓸 수 있다" 는데 콘솔에서는 버튼이 영영 안 나왔다.
  if sudo -n "$HELPER" --probe >/dev/null 2>&1; then
    ok "콘솔이 점프 계정을 직접 적용한다 — 교육생이 키를 넣으면 자동으로 반영된다"
  else
    warn "sudo -n 확인에 실패했다 — 콘솔은 계속 '복사할 명령' 으로 안내한다"
  fi
  if sudo -n "$MHELPER" --probe >/dev/null 2>&1; then
    ok "콘솔에 [관리망 연결] 버튼이 생긴다 — 서버에 들어와 make 를 칠 일이 없다"
  else
    warn "sudo -n 확인에 실패했다 — 관리망 연결은 계속 'make mgmt-net' 안내로 남는다"
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

       토큰이 확인되면 **콘솔이 남은 설치를 스스로 진행한다.**
       누를 것이 없다. [설치] 화면이 무엇이 끝났는지 보여 준다:
         · 교육생 접속 파일 (dist/console-access.sh)
         · 관리망 브리지            ← Proxmox 에 만든다
         · 이 서버를 관리망에 연결   ← netplan
         · 점프 계정                ← 교육생이 키를 넣을 때마다 저절로

       콘솔이 못 하는 것만 그 화면에 사유와 함께 남는다.

  4. dist/console-access.sh 를 Proxmox 호스트로 옮겨 root 로 한 번 실행한다.
       scp $ROOT/dist/console-access.sh root@<노드>:/tmp/
       ssh root@<노드> /tmp/console-access.sh
     랩당 1계정이고 권한은 랩 풀에 걸린다 — 교육생이 늘어도, 랩을 지웠다
     다시 만들어도 그대로 남는다. 랩 수를 늘릴 때만 다시 실행한다.

  5. 초록이 되면 랩 화면에서 [랩 생성]. make 를 칠 일은 없다.

  전체 절차: docs/DEPLOY.md
EOF
exit $RC
