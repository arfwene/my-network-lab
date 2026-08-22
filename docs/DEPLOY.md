# 배포 — 리눅스 서버 한 대에 랩 운영 환경 올리기

이 문서는 **Proxmox 호스트가 아닌 별도의 리눅스 서버**에 이 저장소를 올려
Terraform·Ansible·웹 콘솔을 돌리는 절차다.

> 왜 별도 서버인가
> Proxmox 호스트에 개발 도구를 얹으면 하이퍼바이저가 범용 서버가 된다.
> 호스트가 흔들리면 랩 6개가 같이 죽는다. 그리고 이 저장소의 원칙 —
> **"Proxmox 호스트 설정을 자동으로 바꾸지 않는다"** — 는 실행 주체가 호스트 밖에 있을 때 지켜진다.

---

## 0. 한 장 요약

```
사무실 LAN                         Proxmox VE 호스트
┌──────────────┐                  ┌────────────────────────────────┐
│ 교육생 PC     │                  │  vmbr0   ─ 사무실 LAN            │
│              │                  │                                │
│ 랩 운영 서버   │ ── API 8006 ──▶  │  vmbr9  ─ 관리망 (전 랩 공용)      │
│  (이 문서)    │ ── SSH  22  ──▶  │           랩N = VLAN 300N        │
│  · terraform │      랩 노드      │  vmbr1101.. ─ 랩 서비스망 (격리)    │
│  · ansible   │                  │                                │
│  · 웹 콘솔    │                  │  VM 13대 × 랩 수                 │
└──────────────┘                  └────────────────────────────────┘
      ▲
      └── 교육생 브라우저 :8080
```

랩 운영 서버가 **두 곳에 닿아야** 한다.

| 대상 | 포트 | 쓰는 곳 | 없으면 |
|---|---|---|---|
| Proxmox API | tcp/8006 | Terraform · 콘솔 상태 점검 | VM 을 만들 수 없다 |
| 각 랩 관리망 `172.30.N.0/24` | tcp/22 | Ansible | VM 은 생기지만 설정이 안 들어간다 |

두 번째가 배포에서 가장 자주 막히는 지점이다. → [3절](#3-랩-관리망에-닿게-한다)

---

## 1. 랩 운영 서버 준비

### 요구사항

| 항목 | 값 |
|---|---|
| OS | Ubuntu 22.04+ / Debian 12+ (다른 배포판은 `--no-apt` 로 수동 설치) |
| Python | 3.10 이상 |
| CPU · RAM | 2 vCPU · 2 GB (Terraform·Ansible 실행용. VM 은 Proxmox 가 돌린다) |
| 디스크 | 5 GB |
| 인터넷 | 설치 시에만 필요 (Terraform 바이너리 · 프로바이더 · pip). 폐쇄망은 [7절](#7-폐쇄망) |
| 계정 | **root 아님.** sudo 가 되는 일반 계정 |

**가장 쉬운 선택은 Proxmox 위의 VM 하나다.** 관리망 브리지에 NIC 을 붙이기만 하면
3절의 도달성 문제가 사라진다. 물리 서버여도 되지만 그때는 3절을 읽어야 한다.

### 저장소 옮기기

git 원격이 있으면 `git clone`. 없으면 개발 PC 에서:

```bash
make pack                              # → /tmp/my-network-lab.tar.gz
scp /tmp/my-network-lab.tar.gz lab@<운영서버>:~
```

```bash
# 운영 서버에서
tar xzf my-network-lab.tar.gz && cd my-network-lab
```

`make pack` 은 **`var/` 를 담지 않는다.** 계정 DB 에 Proxmox API 토큰이 들어 있어서다.
토큰은 옮긴 서버에서 콘솔 [연결 설정] 으로 다시 넣는다.
tfstate 와 인벤토리도 제외한다 — 같은 tfstate 를 두 서버가 들고 있으면 같은 VM 을 두 곳에서 관리하게 된다.

### 설치

```bash
./install.sh --service
```

한 번에 이걸 다 한다.

1. OS 패키지 (`python3-venv` · `curl` · `unzip` · `rsync` · `openssh-client`)
2. **Terraform** — 공식 배포처에서 받아 SHA256 검증 후 `/usr/local/bin` 에 설치
3. `console/.venv` 에 FastAPI·Jinja2·**ansible-core** + `ansible.posix` 컬렉션
4. 이 서버의 SSH 키 생성 (`~/.ssh/id_ed25519`) — **공개키를 화면에 찍어 준다**
5. `config/site.local.yml` 을 예시에서 생성, `var/` 를 0700 으로
6. `--service` 면 systemd 등록 + 기동
7. 마지막에 사전 점검을 돌려 남은 할 일을 알려준다

> Ansible 을 시스템이 아니라 venv 안에 넣는 이유: 버전을 이 저장소가 정하고,
> 웹 콘솔(`jobs.py`)과 CLI(`make config`)가 **같은 ansible** 을 쓰게 하기 위해서다.
> 시스템 ansible 을 따로 깔면 "콘솔에서는 되는데 터미널에서는 안 된다"가 생긴다.

옵션:

| 옵션 | 언제 |
|---|---|
| `--no-apt` | 패키지를 이미 깔았거나 apt 가 아닌 배포판 |
| `--service` | 부팅 시 자동 기동. 없으면 `make console` 로 직접 띄운다 |
| `TERRAFORM_ZIP=/path/to.zip` | 폐쇄망 |
| `PORT=9000` | 콘솔 포트 변경 |

---

## 2. 사내 값 채우기

`config/site.local.yml` 을 연다. 이 파일은 **git 에 올라가지 않는다.**

> 아래 주소는 **예시(RFC5737 문서 전용 대역)** 다. 사내 실제 값으로 바꿔 쓴다.
> 이 저장소의 공개 대상 파일에는 사내 값을 적지 않는다 — `make check` 가 막는다.

```yaml
access:
  office_lan: 192.0.2.0/24            # 교육생 PC 가 있는 대역
  proxmox:
    host_ip:      192.0.2.10
    api_endpoint: "https://192.0.2.10:8006/"
    node_name:    pve01                 # Proxmox 에서 `hostname -s`
    datastore:    local-lvm
  ssh_public_keys:
    - "ssh-ed25519 AAAA... my-network-lab@labsrv"   # ← install.sh 가 찍어 준 이 서버의 공개키
    - "ssh-ed25519 AAAA... trainee01@office"        # ← 교육생 공개키

forbidden:
  - {cidr: 10.99.0.0/16, severity: error, reason: "사내 사용 중 — 실제 대역으로 바꿀 것"}
```

**`ssh_public_keys` 에 이 서버의 공개키를 반드시 넣는다.** cloud-init 이 이 목록으로
랩 노드의 `authorized_keys` 를 만든다. 빠지면 VM 은 뜨지만 Ansible 이 한 대도 로그인하지 못한다.

```bash
make check      # 대역 충돌 · 용량 · 공개 안전성
make ipam       # 계산된 주소 계획 — 사내 대역과 겹치는 게 없는지 눈으로 확인
```

### Proxmox 접속 정보는 웹에서 넣어도 된다

`site.local.yml` 대신 콘솔 **[관리자 → 연결 설정]** 에서 주소·노드·데이터스토어를 넣으면
`var/runtime.yml` 로 저장되어 `site.local.yml` 보다 우선한다.
**API 토큰은 어느 쪽이든 파일에 쓰지 않는다** — `var/console.db`(0600)에 두고 실행 시 환경변수로만 넘어간다.

---

## 3. 랩 관리망에 닿게 한다 — **1회 작업**

Terraform 은 API(8006)로만 붙으므로 문제가 없다. 막히는 쪽은 **Ansible** 이다.
Ansible 은 `172.30.N.11` 같은 관리망 주소로 SSH 해야 하는데, 그 대역은 Proxmox 안의 브리지에만 있다.

### 관리망은 브리지 하나를 VLAN 으로 나눈다

랩마다 관리망 브리지를 따로 두지 않는다. 그러면 랩을 만들 때마다 운영 서버에 NIC 을 붙여야 하고,
더 나쁘게는 `terraform destroy` 가 **운영 서버의 NIC 이 꽂힌 브리지를 지우려 든다.**

```
vmbr9   VLAN-aware · 물리 포트 없음 · 전 랩 공용        ← 1회 생성, 지우지 않는다
  ├ 랩1 노드 net0   tag 3001    ← 랩 Terraform 이 붙인다
  ├ 랩2 노드 net0   tag 3002
  └ 운영 서버 net1   태그 없음(트렁크)                  ← 1회 연결, 랩이 늘어도 그대로

vmbr1101 … vmbr1402   랩1 링크 브리지 13개 + VM 13대    ← 수시로 만들고 지운다
```

**랩을 만들거나 지울 때 Proxmox 호스트도 운영 서버의 VM 설정도 건드리지 않는다.**
랩 링크 브리지에는 운영 서버를 붙이지 않는다 — 랩 서비스망은 격리가 목적이고,
운영 서버는 관리망으로만 노드에 닿는다.

> **격리는 그대로다.** 태깅을 브리지가 하므로 랩 노드의 게스트는 untagged 프레임만 보고
> 다른 VLAN 을 주입할 수 없다. 브리지를 랩마다 나눈 것과 격리 수준이 같다.
> 물리 NIC 이 없으니 이 L2 는 호스트 밖으로도 나가지 않는다.

### 절차 (권장 — 운영 서버가 Proxmox 위의 VM 일 때)

```bash
make mgmt LABS=9                      # ① 관리망 브리지 1개 생성 (VLAN 1~9 준비, 1회)
make opsvm VMID=<운영서버 VMID> LABS=9  # ② 붙일 명령·netplan 생성 → dist/ops-server.md
```

`dist/ops-server.md` 에 이런 것들이 **주소까지 채워져서** 나온다.

```bash
# Proxmox 호스트에서 (1회) — 랩 수와 무관하게 NIC 은 하나다
qm set 9100 -net1 virtio,bridge=vmbr9       # 태그 없음 = 트렁크
```

```yaml
# 운영 서버 안 /etc/netplan/60-lab-mgmt.yaml (1회)
network:
  version: 2
  ethernets:
    ens19: {dhcp4: false}                   # 트렁크 자체는 주소를 갖지 않는다
  vlans:
    mgmt1: {id: 3001, link: ens19, addresses: [172.30.1.9/24]}
    mgmt2: {id: 3002, link: ens19, addresses: [172.30.2.9/24]}
    # ... 랩9까지
```

마지막 옥텟 `9` 는 `site.yml` 의 `access.jump_host.host_octet`, VLAN 은 `vlan_base + 랩번호` 다.
**게이트웨이는 주지 않는다.** 이 인터페이스들은 관리망 안에서만 쓴다 —
기본 경로가 여러 개가 되면 사무실로 나가는 트래픽이 어디로 나갈지 흔들린다.

여기까지 하면 **끝이다.** 이후 랩을 몇 번 만들고 지우든 Proxmox 호스트도 운영 서버도 건드리지 않는다.

```bash
make doctor          # "관리망 브리지" · "이 서버의 관리망 주소" · "경로" 가 초록인지 본다
                     # vlan_aware 가 꺼져 있으면 오류로 잡는다 — 켜지 않으면 전 랩 관리망이 한 L2 로 합쳐진다
```

### Proxmox 호스트에도 주소를 줄 것인가 (선택)

호스트가 랩 관리망에 주소(`172.30.N.1`)를 가지면 호스트에서도 랩 노드를 확인할 수 있다.
`/etc/network/interfaces` 에 랩당 VLAN 서브인터페이스 한 덩이:

```
auto vmbr9.3001
iface vmbr9.3001 inet static
    address 172.30.1.1/24
```

> 브리지 자체는 `make mgmt` 가 만든다. 손으로 넣는 것은 **호스트 IP 뿐**이고, 그것도 선택이다.
> 이 저장소는 Proxmox 호스트 설정을 자동으로 바꾸지 않는다.
> 주소를 준다면 `dist/host-guard.nft` 을 함께 검토할 것 — 랩에서 하이퍼바이저로 오는
> 신규 연결을 막는 규칙이다.

### 운영 서버가 물리 서버라면 — Proxmox 호스트를 경유한다

브리지에 발을 걸칠 수 없으니 Ansible 이 SSH ProxyJump 로 호스트를 지나간다.

```bash
ssh-copy-id root@192.0.2.10          # 이 서버의 공개키를 Proxmox root 에 등록
```

```yaml
# config/site.local.yml
access:
  jump_host:
    proxy_via_proxmox: true
    proxmox_ssh_user: root
```

```bash
make gen LAB=1
grep ssh_common infra/ansible/inventory/lab1/group_vars/all.yml
# → ansible_ssh_common_args: -o ProxyJump=root@192.0.2.10 ...
```

경유 호스트에는 아무것도 설치되지 않는다(`-W` 방식). 다만 느리고, Proxmox 호스트를
SSH 경로에 끌어들인다. **VM 으로 올릴 수 있으면 그쪽이 낫다.**

### 라우팅으로 푸는 방법 (권장하지 않음)

운영 서버에 `172.30.0.0/16 via <Proxmox>` 정적 경로를 주고 호스트에서 `ip_forward` 를 켜는 방법.
동작은 하지만 **Proxmox 호스트를 라우터로 만든다.** `dist/access.md` 가 명시적으로 금지하는 구성이고,
랩의 브로드캐스트·잘못된 OSPF 광고가 사무실로 나갈 경로를 하나 여는 셈이다.

---

## 4. Proxmox 쪽 준비

### API 토큰 — 스크립트로 만든다

**손으로 `pveum` 을 치지 말 것.** 권한을 하나 빠뜨려도 티가 안 나고, `terraform apply` 가
브리지를 만들다 HTTP 403 으로 멈춘다. 그때 Proxmox 는 **무엇이 없는지 알려주지 않는다.**

```bash
# Proxmox 호스트에서 root 로, 한 번만
./infra/proxmox-setup.sh
```

이 스크립트가 하는 일:

1. 역할 `LabProvision` 생성(있으면 권한 목록을 최신으로 맞춤)
2. 사용자 `terraform@pve` 생성 + `/` 에 역할 부여
3. API 토큰 발급 — **`--privsep 0`**
4. **토큰이 실제로 무엇을 할 수 있는지 확인**하고, 모자라면 이름을 대고 멈춤
5. 통과하면 토큰 비밀값을 한 번 출력 (다시 볼 수 없다)

```bash
./infra/proxmox-setup.sh --show        # 아무것도 바꾸지 않고 상태만 점검
./infra/proxmox-setup.sh --new-token   # 비밀값을 잃었을 때 다시 발급
```

토큰은 콘솔 **[관리자 → 연결 설정]** 에 넣는다. 파일에 쓰지 않는다.

**`make mgmt` · `make deploy` 도 같은 토큰을 쓴다.** `tools/with-pve-env.py` 가
`var/console.db` 에서 읽어 실행 순간에만 환경변수로 넘긴다 — 따로 export 하지 않아도 된다.

#### 가장 흔한 실패: 권한 분리

```
Error: Could not create Linux Bridge ... HTTP 403 (/nodes/pve01, Sys.Modify)
```

**웹 UI 로 토큰을 만들면 "Privilege Separation" 이 기본으로 켜진다.** 그러면 사용자에게 준
역할을 토큰이 물려받지 않아, 역할을 아무리 손봐도 계속 403 이다.

```bash
pveum user token permissions terraform@pve lab --path /nodes/pve01   # 비어 있으면 이것이다
pveum user token modify terraform@pve lab --privsep 0                # 한 줄로 끝
```

`./infra/proxmox-setup.sh` 는 이 상태를 감지해서 꺼 준다.

#### 무엇 때문에 어떤 권한이 필요한가

| 권한 | 없으면 |
|---|---|
| `Sys.Modify` (`/nodes/<node>`) | **브리지가 안 생긴다.** VM 은 만들어져도 랜선이 없다 |
| `VM.Clone` | 골든 템플릿을 복제하지 못한다 |
| `VM.Config.Cloudinit` | 관리망 주소·SSH 키가 안 들어가 노드에 접속할 수 없다 |
| `Datastore.AllocateSpace` | 디스크·cloud-init 드라이브를 만들지 못한다 |

`make doctor` 와 콘솔의 배포 전 검사가 **이 목록을 Proxmox 에 직접 물어본다.**
모자라면 `terraform apply` 전에 이름을 대고 막는다.

### 골든 템플릿

랩 노드는 인터넷에 못 나간다. 필요한 패키지(FRR·nftables·bind9·tcpdump…)를 템플릿에 미리 넣는다.

```bash
# Proxmox 호스트에서
apt install -y libguestfs-tools
./infra/template/build-golden-template.sh --storage local-lvm
```

`config/site.yml` 의 `labs.template_vmid` (기본 9000)와 번호가 맞아야 한다.
`make doctor` 가 이 VMID 의 존재를 확인해 준다.

---

## 5. 첫 랩 띄우기

```bash
make doctor                 # 오류 0 인지 확인. 여기서 걸리는 게 배포 중에 걸리는 것보다 싸다
make mgmt LABS=9            # 관리망 브리지 — 최초 1회만. 이미 했다면 생략
make gen LAB=1              # tfvars · 인벤토리 · 교재 · 부록 · SSH 설정 생성
make deploy LAB=1           # terraform apply — 브리지 14개 + VM 13대
make config  LAB=1 STAGE=m1 # Ansible — M1 단계 설정만 올린다
make verify  LAB=1 STAGE=m1
```

또는 웹 콘솔에서 버튼으로. 콘솔은 같은 명령을 같은 순서로 돌리고 로그를 실시간으로 흘려준다.

```bash
make console                                        # --service 로 깔았으면 이미 떠 있다
python3 tools/console-user.py add trainee01 --lab 1  # 교육생 계정
```

첫 로그인은 `admin / admin` 이고 **비밀번호 변경 전에는 아무 화면도 보이지 않는다.**

### 단계 진행

```bash
make config LAB=1 STAGE=m2      # VM 은 그대로. 설정 범위만 넓어진다
```

---

## 5.5 교육생 접속 (교육생이 올 때)

교육생은 콘솔만으로는 실습을 못 한다 — 이 과정의 실습은 전부 터미널이다.
랩 노드는 사무실에서 직접 보이지 않으므로 **운영 서버를 ProxyJump 로 거친다.**

```
교육생 PC ──ssh──▶ 운영 서버 ──ssh──▶ 랩 노드 (pc1 · r1 · web …)
```

운영 서버는 콘솔·Terraform·Ansible 이 도는 장비다. 여기에 교육생 **셸**을 주면
모듈 해설(`answers.md`)과 캡스톤 장애 대응표를 읽을 수 있고, 다른 랩 관리망으로도 나갈 수 있다.
그래서 점프 계정은 **셸이 없고 자기 랩 노드로만 나간다.**

```bash
# ① 교육생이 콘솔 [접속 키] 에서 자기 공개키를 등록한다
python3 tools/console-user.py keys --lab 1        # 등록 현황 확인

# ② 그 키로 점프 계정 절차를 생성한다
make jumpaccess                                    # → dist/jump-access.{sh,conf}

# ③ 운영 서버에 적용 (root)
sudo ./dist/jump-access.sh
sudo cp dist/jump-access.conf /etc/ssh/sshd_config.d/60-lab-jump.conf
sudo sshd -t && sudo systemctl reload ssh          # -t 로 먼저 검사할 것

# ④ 교육생에게 나눠 줄 ssh 설정
python3 tools/gen-ssh-config.py --lab 1 --user user01 > ssh-config-lab1
```

교육생은 그 파일을 `~/.ssh/config` 에 붙이면 `ssh pc1` 로 들어간다.

| 제한 | 무엇 |
|---|---|
| `ForceCommand` + nologin 셸 | 대화형 접속 불가 → 운영 서버의 파일을 못 읽는다 |
| `PermitOpen` = 자기 랩 13대:22 | 다른 랩 관리망으로 못 나간다 |
| 비밀번호 잠금 | 키로만 들어온다 |

> `Match all` 로 끝나는 것을 지우지 말 것. `sshd_config.d` 는 보통 `sshd_config` 앞부분에서
> include 되므로, 닫지 않으면 **그 뒤의 전역 설정이 전부 마지막 Match 안에 갇힌다.**
> 생성기가 이 줄을 자동으로 넣는다.

교육생이 늘거나 키를 바꾸면 ②③ 을 다시 하면 된다.

---

## 6. 운영

| 하는 일 | 명령 |
|---|---|
| 상태 점검 | `make doctor` |
| 서비스 상태 | `systemctl status my-network-lab` |
| 로그 | `journalctl -u my-network-lab -f` |
| 재시작 | `sudo systemctl restart my-network-lab` |
| 랩 초기화 | `make reset LAB=1 STAGE=m4` |
| 랩 삭제 | 콘솔 [삭제] 또는 `cd infra/terraform/envs/lab1 && terraform destroy` |
| 계정 목록 | `make users` |

### 백업할 것

**`var/` 하나다.** 계정·비밀번호 해시·진도·시험 기록·API 토큰이 전부 여기 있고,
나머지는 `config/` 에서 언제든 다시 만들어진다.

```bash
tar czf var-$(date +%F).tar.gz var/     # 0600 으로 보관할 것 — 토큰이 들어 있다
```

tfstate(`infra/terraform/envs/lab*/`)도 잃으면 Terraform 이 기존 VM 을 모르게 된다.
같이 챙기거나, 잃었을 때는 `terraform import` 대신 랩을 지우고 다시 만드는 편이 빠르다.

### 서버마다 다른 환경변수

systemd 유닛이 `var/console.env` 를 있으면 읽는다 (없어도 된다).

```bash
cat > var/console.env <<'EOF'
# 사내 프록시
HTTPS_PROXY=http://proxy.example:3128
NO_PROXY=192.0.2.0/24,172.30.0.0/16,localhost
# Terraform 이 Proxmox 에 SSH 해야 하는 경우에만 (보통 필요 없다)
# PROXMOX_VE_SSH_USERNAME=root
# PROXMOX_VE_SSH_PRIVATE_KEY=/home/lab/.ssh/id_ed25519
EOF
chmod 600 var/console.env && sudo systemctl restart my-network-lab
```

---

## 7. 폐쇄망

인터넷이 없는 서버라면 세 가지를 미리 옮긴다.

```bash
# 인터넷 되는 PC 에서
curl -LO https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip
pip download -d wheels -r console/requirements.txt
ansible-galaxy collection download -r infra/ansible/requirements.yml -p galaxy
terraform providers mirror ./tf-mirror     # bpg/proxmox 프로바이더
```

```bash
# 운영 서버에서
TERRAFORM_ZIP=~/terraform_1.9.8_linux_amd64.zip ./install.sh --no-apt
console/.venv/bin/pip install --no-index --find-links wheels -r console/requirements.txt
```

프로바이더는 `~/.terraformrc` 로 미러를 가리킨다:

```hcl
provider_installation {
  filesystem_mirror {
    path    = "/home/lab/tf-mirror"
    include = ["registry.terraform.io/bpg/*"]
  }
  direct {
    exclude = ["registry.terraform.io/bpg/*"]
  }
}
```

---

## 8. 막혔을 때

| 증상 | 원인 | 확인 |
|---|---|---|
| `make doctor` — TCP 연결 실패 | 방화벽 / 주소 오타 | `nc -vz <proxmox> 8006` |
| `must provide either username and password, an API token, or a ticket` | 토큰을 못 찾았다 | 콘솔 [연결 설정] 에 넣거나 `export PROXMOX_VE_API_TOKEN=...`. `make doctor` 의 "CLI 자격 증명" 항목 |
| 토큰이 거부됨 (401/403) | privsep 켠 토큰에 ACL 없음 | `pveum user token list terraform@pve` |
| 브리지 생성만 실패 | 역할에 `Sys.Modify` 없음 | 4절의 `pveum role add` 다시 |
| VM 은 생겼는데 Ansible 이 전부 UNREACHABLE | 관리망 도달 불가 | 3절. `ip route get 172.30.1.11` |
| 다른 랩의 노드가 서로 보인다 | 브리지의 `vlan_aware` 가 꺼져 있다 | `make doctor` — 오류로 잡힌다. `make mgmt` 재실행 |
| VM 이 기동하지 않는다 (bridge not found) | `make mgmt` 를 안 했다 | `make mgmt LABS=9` |
| 특정 노드만 `Permission denied (publickey)` | 공개키 누락 상태로 cloud-init 이 돌았다 | `ssh_public_keys` 채우고 `terraform apply` 후 VM 재생성 |
| 콘솔은 되는데 터미널에서 `ansible-playbook: command not found` | 시스템에 ansible 이 없다 | 정상이다. `make config` 를 쓸 것 (venv 를 자동으로 찾는다) |
| 서비스가 뜨자마자 죽는다 | 포트 충돌 / 권한 | `journalctl -u my-network-lab -n 50` |
| terraform 이 SSH agent 를 찾는다 | 드문 경우 | `var/console.env` 에 `PROXMOX_VE_SSH_*` 지정 (6절) |
| 교육생이 노드에 SSH 못 함 | 교육생 공개키 누락 | `dist/ssh-config-lab1` 배포 여부 확인 |

로그는 콘솔 화면(SSE 실시간)과 `journalctl` 두 곳에 남는다.
Terraform·Ansible 원문 출력이 그대로 흐르므로, 콘솔에서 본 오류를 그대로 검색하면 된다.

---

## 9. 삭제

```bash
for n in 1 2 3; do (cd infra/terraform/envs/lab$n && terraform destroy -auto-approve); done
sudo systemctl disable --now my-network-lab
sudo rm /etc/systemd/system/my-network-lab.service && sudo systemctl daemon-reload
```

관리망 브리지(`vmbr9`)는 랩 destroy 로 사라지지 않는다. 전부 없애려면 마지막에:

```bash
cd infra/terraform/envs/mgmt && terraform destroy    # 운영 서버 NIC 을 먼저 떼고 할 것
```

Proxmox 호스트에 손으로 넣은 것은 (줬다면) 관리망 브리지 IP 뿐이다(3절). 지울 때도 그것만 지우면 된다.
