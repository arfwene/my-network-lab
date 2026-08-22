#!/bin/bash
# =============================================================================
#  골든 템플릿 생성 — Proxmox 호스트에서 root 로 실행
#
#  랩 노드는 인터넷에 나갈 수 없다(의도된 격리). 그러므로 필요한 패키지를
#  전부 템플릿에 미리 넣어둔다. 노드는 이 템플릿의 linked clone 으로 만들어진다.
#
#  usage:  ./build-golden-template.sh [--storage local-lvm] [--vmid 9000]
# =============================================================================
set -euo pipefail

VMID=9000
STORAGE=local-lvm
NAME=ubuntu-2404-lab
IMG_URL=https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
WORK=/var/lib/vz/template/lab

FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --storage) STORAGE="$2"; shift 2;;
    --vmid)    VMID="$2";    shift 2;;
    --force)   FORCE=1;      shift;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

# --- 필요한 도구가 있는가 ---------------------------------------------------
#  virt-customize 로 cloud 이미지 안에 패키지를 미리 넣는다. 랩 노드는 인터넷에
#  못 나가므로 이 단계를 건너뛸 수 없다.
#  "Unable to locate package" 는 대개 apt 목록이 없어서다 — 먼저 apt update.
if ! command -v virt-customize >/dev/null; then
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
  echo "  Debian 기본 저장소(main)가 비어 있지 않은지도 확인할 것:" >&2
  echo "    grep -rh '^deb ' /etc/apt/sources.list /etc/apt/sources.list.d/ | grep -v proxmox" >&2
  exit 1
fi

for c in wget qm; do
  command -v "$c" >/dev/null || { echo "중단: $c 가 없다" >&2; exit 1; }
done

# --- 남의 VM 을 지우지 않는다 -------------------------------------------------
#  이 스크립트는 회사 Proxmox 에서 돈다. $VMID 에 운영 VM 이 있는데 그냥 지우면
#  되돌릴 수 없다. 우리가 만든 템플릿일 때만 다시 만든다.
if qm config "$VMID" >/dev/null 2>&1; then
  IS_TPL=$(qm config "$VMID" | awk -F': ' '/^template:/{print $2}')
  CUR_NAME=$(qm config "$VMID" | awk -F': ' '/^name:/{print $2}')
  if [[ "$IS_TPL" != "1" || "$CUR_NAME" != "$NAME" ]]; then
    echo "중단: VMID $VMID 를 이미 다른 VM 이 쓰고 있다 (name=${CUR_NAME:-?}, template=${IS_TPL:-0})" >&2
    echo "      이 번호를 지우면 그 VM 이 사라진다. 다음 중 하나를 할 것:" >&2
    echo "        · 비어 있는 번호로:  --vmid <다른 번호>" >&2
    echo "          (design/ipam.yml 의 labs.template_vmid 도 같이 바꿀 것)" >&2
    echo "        · 정말 지워도 된다면: --force" >&2
    exit 1
  fi
  if [[ "$FORCE" != "1" ]]; then
    echo "==> VMID $VMID 에 우리 템플릿($NAME)이 이미 있다. 다시 만든다."
    echo "    이 템플릿의 linked clone 으로 만들어진 랩 VM 이 있으면 함께 깨진다."
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

echo "==> 작업 디렉토리 준비: $WORK"
mkdir -p "$WORK"; cd "$WORK"

echo "==> Ubuntu 24.04 cloud image 다운로드"
[[ -f noble.img ]] || wget -O noble.img "$IMG_URL"
cp -f noble.img build.img

echo "==> 필요한 도구 설치 (libguestfs-tools 필요: apt install libguestfs-tools)"
virt-customize -a build.img \
  --install "$PKG_CSV" \
  --run-command 'systemctl enable qemu-guest-agent' \
  --run-command 'systemctl disable --now nginx vsftpd named frr systemd-networkd-wait-online || true' \
  --run-command 'sed -i "s/^#\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config' \
  --run-command 'echo "lab ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-lab && chmod 440 /etc/sudoers.d/90-lab' \
  --run-command 'mkdir -p /etc/netplan && rm -f /etc/netplan/*.yaml' \
  --run-command 'printf "net.ipv4.ip_forward=0\n" > /etc/sysctl.d/99-lab-default.conf' \
  --truncate /etc/machine-id

echo "==> Proxmox VM 생성 및 템플릿화 (VMID=$VMID)"
qm create "$VMID" --name "$NAME" --memory 512 --cores 1 --net0 virtio,bridge=vmbr0 \
  --scsihw virtio-scsi-single --ostype l26 --agent enabled=1 \
  --serial0 socket --vga serial0
# 볼륨 이름은 스토리지 종류마다 다르다 (local-lvm: vm-9000-disk-0, dir: vm-9000-disk-0.raw).
# 이름을 짐작하지 말고 importdisk 가 알려주는 값을 쓴다.
IMPORTED=$(qm importdisk "$VMID" build.img "$STORAGE" 2>&1 | tee /dev/stderr \
           | sed -n "s/.*mported disk as '\([^']*\)'.*/\1/p" \
           | sed "s/^unused[0-9]*://")
if [[ -z "$IMPORTED" ]]; then
  echo "중단: 디스크 가져오기 결과를 읽지 못했다. 위 출력을 확인할 것" >&2
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
