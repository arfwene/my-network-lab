#!/bin/bash
# =============================================================================
#  골든 템플릿 생성 — Proxmox 호스트에서 root 로 실행
#
#  랩 노드는 인터넷에 나갈 수 없다(의도된 격리). 그러므로 필요한 패키지를
#  전부 템플릿에 미리 넣어둔다. 노드는 이 템플릿의 linked clone 으로 만들어진다.
#
#  두 단계로 나눠 실행할 수 있다. 하이퍼바이저에 저장소를 추가하거나 도구를 깔지
#  않으려면 이쪽을 쓴다 (libguestfs-tools 는 Debian main 에 있고, Proxmox 저장소에는 없다).
#
#    ① 이미지 만들기 — 운영 서버(Ubuntu)에서. 여기는 apt 가 자유롭다.
#         sudo apt install -y libguestfs-tools
#         ./build-golden-template.sh --image-only --out /tmp/lab.img
#         scp /tmp/lab.img root@<proxmox>:/var/lib/vz/template/lab/
#
#    ② 템플릿 등록 — Proxmox 호스트에서. qm 만 있으면 된다.
#         ./build-golden-template.sh --from-image /var/lib/vz/template/lab/lab.img
#
#  한 대에서 다 할 수 있으면(호스트에 libguestfs-tools 가 있으면) 그냥:
#    usage:  ./build-golden-template.sh [--storage local-lvm] [--vmid 9000]
# =============================================================================
set -euo pipefail

VMID=9000
STORAGE=local-lvm
NAME=ubuntu-2404-lab
IMG_URL=https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
WORK=/var/lib/vz/template/lab

FORCE=0
MODE=all           # all | image-only | from-image
OUT=""             # --image-only 의 결과 파일
FROM=""            # --from-image 의 입력 파일

while [[ $# -gt 0 ]]; do
  case "$1" in
    --storage) STORAGE="$2"; shift 2;;
    --vmid)    VMID="$2";    shift 2;;
    --force)   FORCE=1;      shift;;
    --image-only) MODE=image-only; shift;;
    --out)     OUT="$2";     shift 2;;
    --from-image) MODE=from-image; FROM="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

# --- 필요한 도구가 있는가 ---------------------------------------------------
#  virt-customize 로 cloud 이미지 안에 패키지를 미리 넣는다. 랩 노드는 인터넷에
#  못 나가므로 이 단계를 건너뛸 수 없다.
#  "Unable to locate package" 는 대개 apt 목록이 없어서다 — 먼저 apt update.
need_virt() { [[ "$MODE" != "from-image" ]]; }
need_qm()   { [[ "$MODE" != "image-only" ]]; }

if need_virt && ! command -v virt-customize >/dev/null; then
  CODENAME=$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-bookworm}")
  echo "중단: virt-customize 가 없다 (libguestfs-tools)" >&2
  echo >&2
  echo "  apt update && apt install -y libguestfs-tools" >&2
  echo >&2
  echo "  'Unable to locate package' 가 나오면 패키지 목록이 없는 것이다." >&2
  echo "  apt update 가 실패하는지 먼저 볼 것 — Proxmox 는 구독이 없으면" >&2
  echo "  enterprise 저장소가 401 을 뱉는다. 그 경우:" >&2
  echo >&2
  echo "    # 구독 없는 설치라면 enterprise 를 끄고 no-subscription 을 켠다" >&2
  echo "    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list 2>/dev/null" >&2
  echo "    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/ceph.list 2>/dev/null" >&2
  echo "    echo 'deb http://download.proxmox.com/debian/pve $CODENAME pve-no-subscription' \\" >&2
  echo "      > /etc/apt/sources.list.d/pve-no-subscription.list" >&2
  echo "    apt update && apt install -y libguestfs-tools" >&2
  echo >&2
  echo "  Debian 기본 저장소(main)가 비어 있지 않은지도 확인할 것 —" >&2
  echo "  libguestfs-tools 는 **Debian main** 에 있다. Proxmox 저장소에는 없다:" >&2
  echo "    apt-cache policy libguestfs-tools" >&2
  echo "    grep -rhE '^\\s*(deb |URIs:)' /etc/apt/sources.list /etc/apt/sources.list.d/" >&2
  echo >&2
  echo "  ▸ 하이퍼바이저에 저장소를 추가하고 싶지 않다면 두 단계로 나눌 것." >&2
  echo "    이미지는 운영 서버(Ubuntu)에서 만들고, 여기서는 등록만 한다:" >&2
  echo >&2
  echo "      # 운영 서버에서" >&2
  echo "      sudo apt install -y libguestfs-tools" >&2
  echo "      ./infra/template/build-golden-template.sh --image-only --out /tmp/lab.img" >&2
  echo "      scp /tmp/lab.img root@$(hostname -s 2>/dev/null || echo proxmox):/var/lib/vz/template/lab/" >&2
  echo >&2
  echo "      # 이 호스트에서" >&2
  echo "      ./infra/template/build-golden-template.sh --from-image /var/lib/vz/template/lab/lab.img" >&2
  exit 1
fi

need_virt && { command -v wget >/dev/null || { echo "중단: wget 이 없다" >&2; exit 1; }; }
need_qm   && { command -v qm   >/dev/null || {
  echo "중단: qm 이 없다 — 이 명령은 Proxmox 호스트에서 실행해야 한다" >&2
  echo "      운영 서버에서라면 --image-only 로 이미지만 만들 것" >&2; exit 1; }; }

# --- 남의 VM 을 지우지 않는다 -------------------------------------------------
#  이 스크립트는 회사 Proxmox 에서 돈다. $VMID 에 운영 VM 이 있는데 그냥 지우면
#  되돌릴 수 없다. 우리가 만든 템플릿일 때만 다시 만든다.
if need_qm && qm config "$VMID" >/dev/null 2>&1; then
  IS_TPL=$(qm config "$VMID" | awk -F': ' '/^template:/{print $2}')
  CUR_NAME=$(qm config "$VMID" | awk -F': ' '/^name:/{print $2}')
  # 이름이 우리 것이면 템플릿이 아니어도 우리가 만들다 만 것이다 —
  # import 는 됐는데 마지막 단계에서 멈춘 경우가 정확히 이 상태다.
  if [[ "$CUR_NAME" != "$NAME" ]]; then
    echo "중단: VMID $VMID 를 이미 다른 VM 이 쓰고 있다 (name=${CUR_NAME:-?}, template=${IS_TPL:-0})" >&2
    echo "      이 번호를 지우면 그 VM 이 사라진다. 다음 중 하나를 할 것:" >&2
    echo "        · 비어 있는 번호로:  --vmid <다른 번호>" >&2
    echo "          (design/ipam.yml 의 labs.template_vmid 도 같이 바꿀 것)" >&2
    echo "        · 정말 지워도 된다면: --force" >&2
    exit 1
  fi
  if [[ "$FORCE" != "1" ]]; then
    if [[ "$IS_TPL" == "1" ]]; then
      echo "==> VMID $VMID 에 우리 템플릿($NAME)이 이미 있다. 다시 만든다."
      echo "    이 템플릿의 linked clone 으로 만들어진 랩 VM 이 있으면 함께 깨진다."
    else
      echo "==> VMID $VMID 에 만들다 만 VM($NAME)이 있다. 지우고 다시 만든다."
    fi
    read -r -p "    계속할까? [y/N] " a
    [[ "$a" == "y" || "$a" == "Y" ]] || { echo "중단."; exit 1; }
  fi
  qm destroy "$VMID" --purge
fi

# 랩에서 쓰는 도구 일체. 역할별로 템플릿을 나누지 않는다 —
# 어떤 노드든 어떤 역할이 될 수 있어야 실습 중 재배치가 자유롭다.
PACKAGES=$(cat <<'PKG'
qemu-guest-agent
frr frr-pythontools
nginx vsftpd bind9 bind9-utils
tcpdump tshark
iproute2 bridge-utils vlan ethtool net-tools
iputils-ping iputils-arping iputils-tracepath traceroute mtr-tiny
telnet netcat-openbsd curl wget socat lsof
dnsutils nftables conntrack iperf3
python3 python3-yaml
PKG
)
PKG_CSV=$(echo "$PACKAGES" | tr '\n' ' ' | tr -s ' ' | sed 's/ $//' | tr ' ' ',')

if [[ "$MODE" == "from-image" ]]; then
  [[ -f "$FROM" ]] || { echo "중단: 이미지가 없다: $FROM" >&2; exit 1; }
  BUILD="$FROM"
  echo "==> 미리 만들어 둔 이미지를 쓴다: $BUILD"
else
  # --image-only 는 Proxmox 가 아닌 곳(운영 서버)에서도 돈다. 그때는 /var/lib/vz 가 없다.
  if [[ "$MODE" == "image-only" ]]; then
    WORK=$(dirname "${OUT:-/tmp/lab.img}")
    BUILD="${OUT:-/tmp/lab.img}"
  else
    BUILD="$WORK/build.img"
  fi
  echo "==> 작업 디렉토리 준비: $WORK"
  mkdir -p "$WORK"

  # --- libguestfs 가 돌 수 있는 상태인가 -------------------------------------
  #  virt-customize 는 커널로 작은 VM(어플라이언스)을 띄워 이미지를 편집한다.
  #  그래서 두 가지가 필요하고, 둘 다 조용히 실패한다:
  #    · 커널 이미지 읽기 권한 — 데비안/우분투는 /boot/vmlinuz-* 를 0600 으로 깐다.
  #      일반 계정으로 돌리면 supermin 이 "exited with error status 1" 만 남기고 죽는다.
  #    · 하드웨어 가상화 — 없으면 TCG(소프트웨어 에뮬레이션)로 떨어뜨려야 한다.
  UNREADABLE=0
  for k in /boot/vmlinuz-*; do
    [[ -e "$k" ]] || continue
    [[ -r "$k" ]] || UNREADABLE=$((UNREADABLE + 1))
  done
  if [[ "$UNREADABLE" -gt 0 ]]; then
    echo
    echo "==> libguestfs 가 커널 이미지를 읽지 못한다 ($UNREADABLE 개)"
    echo "    데비안/우분투는 /boot/vmlinuz-* 를 root 전용(0600)으로 둔다."
    echo "    이 상태로는 supermin 이 'error status 1' 만 남기고 죽는다."
    echo "    고치는 방법 (libguestfs 문서가 안내하는 표준 해법):"
    echo "        sudo chmod 0644 /boot/vmlinuz-*"
    if [[ -t 0 ]]; then
      read -r -p "    지금 실행할까? [y/N] " a
      if [[ "$a" == "y" || "$a" == "Y" ]]; then
        sudo chmod 0644 /boot/vmlinuz-* || { echo "중단: chmod 실패" >&2; exit 1; }
        echo "    적용했다. (커널을 새로 설치하면 다시 0600 이 되니 그때 한 번 더 해야 한다)"
      else
        echo "중단. 위 명령을 실행한 뒤 다시 시작할 것." >&2; exit 1
      fi
    else
      echo "중단: 대화형이 아니라 물어볼 수 없다. 위 명령을 실행할 것." >&2; exit 1
    fi
  fi

  if [[ ! -e /dev/kvm ]]; then
    # VM 안에서 빌드하면 대개 /dev/kvm 이 없다 (중첩 가상화 꺼짐).
    # TCG 로 떨어뜨리면 동작은 한다 — 다만 느리다.
    export LIBGUESTFS_BACKEND_SETTINGS=force_tcg
    echo "==> /dev/kvm 이 없다 — 소프트웨어 에뮬레이션(TCG)으로 돈다."
    echo "    동작은 하지만 느리다. 10~25분쯤 걸린다고 보면 된다."
  fi

  echo "==> Ubuntu 24.04 cloud image 다운로드"
  BASE="$WORK/noble.img"
  [[ -f "$BASE" ]] || wget -O "$BASE" "$IMG_URL"
  cp -f "$BASE" "$BUILD"

  echo "==> 필요한 도구 설치 (virt-customize)"
  virt-customize -a "$BUILD" \
    --install "$PKG_CSV" \
    --run-command 'systemctl enable qemu-guest-agent' \
    --run-command 'systemctl disable --now nginx vsftpd named frr systemd-networkd-wait-online || true' \
    --run-command 'sed -i "s/^#\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config' \
    --run-command 'echo "lab ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-lab && chmod 440 /etc/sudoers.d/90-lab' \
    --run-command 'mkdir -p /etc/netplan && rm -f /etc/netplan/*.yaml' \
    --run-command 'printf "net.ipv4.ip_forward=0\n" > /etc/sysctl.d/99-lab-default.conf' \
    --truncate /etc/machine-id || {
      echo >&2
      echo "중단: virt-customize 가 실패했다." >&2
      echo "  진단:" >&2
      echo "    libguestfs-test-tool 2>&1 | tail -30" >&2
      echo "  자주 걸리는 것:" >&2
      echo "    · /boot/vmlinuz-* 읽기 권한   → sudo chmod 0644 /boot/vmlinuz-*" >&2
      echo "    · /dev/kvm 없음               → export LIBGUESTFS_BACKEND_SETTINGS=force_tcg" >&2
      echo "    · 디스크 여유 부족(약 3GB)     → df -h $(dirname "$BUILD")" >&2
      rm -f "$BUILD"
      exit 1
    }
fi

if [[ "$MODE" == "image-only" ]]; then
  echo
  echo "완료: $BUILD"
  echo "  다음 — Proxmox 호스트로 옮기고 등록한다:"
  echo "    scp $BUILD root@<proxmox>:/var/lib/vz/template/lab/"
  echo "    ./infra/template/build-golden-template.sh \\"
  echo "        --from-image /var/lib/vz/template/lab/$(basename "$BUILD") --storage $STORAGE"
  exit 0
fi

echo "==> Proxmox VM 생성 및 템플릿화 (VMID=$VMID)"
qm create "$VMID" --name "$NAME" --memory 512 --cores 1 --net0 virtio,bridge=vmbr0 \
  --scsihw virtio-scsi-single --ostype l26 --agent enabled=1 \
  --serial0 socket --vga serial0
# 볼륨 이름은 스토리지 종류마다 다르다 (local-lvm: vm-9000-disk-0,
# dir: vm-9000-disk-0.raw, zfs: vm-9000-disk-0). 이름을 짐작하지 않는다.
#
# 화면 메시지를 파싱하지도 않는다 — PVE 버전마다 문구가 다르다.
#   구:  Successfully imported disk as 'unused0:local-lvm:vm-9000-disk-0'
#   신:  unused0: successfully imported disk 'local-zfs:vm-9000-disk-0'
# 대신 import 뒤 **VM 설정에서 unused 슬롯을 읽는다.** 이건 형식이 고정돼 있다.
qm importdisk "$VMID" "$BUILD" "$STORAGE"
IMPORTED=$(qm config "$VMID" | sed -n 's/^unused[0-9]*: *//p' | head -1)
if [[ -z "$IMPORTED" ]]; then
  echo "중단: 가져온 디스크를 VM 설정에서 찾지 못했다." >&2
  echo "      확인:  qm config $VMID" >&2
  echo "      unused0 이 보이면 그 값으로 직접 이어서 할 수 있다:" >&2
  echo "        qm set $VMID --scsi0 <unused0 값>" >&2
  echo "        qm set $VMID --ide2 $STORAGE:cloudinit" >&2
  echo "        qm set $VMID --boot order=scsi0 && qm resize $VMID scsi0 8G" >&2
  echo "        qm template $VMID" >&2
  exit 1
fi
echo "==> 가져온 디스크: $IMPORTED"
qm set "$VMID" --scsi0 "$IMPORTED"
qm set "$VMID" --ide2 "$STORAGE:cloudinit"
qm set "$VMID" --boot order=scsi0
qm resize "$VMID" scsi0 8G
qm template "$VMID"

echo
echo "완료: VMID $VMID ($NAME) 템플릿 생성됨"
echo "  design/ipam.yml 의 labs.template_vmid 값과 일치해야 한다 (현재 설정: $VMID)"
