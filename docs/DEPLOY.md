# 배포 절차

**대상** — 랩 관리자 / 강사. 이 저장소를 리눅스 서버 한 대에 올려 Terraform · Ansible · 웹 콘솔을 돌린다.

**원칙** — Proxmox 호스트가 아닌 **별도 서버**에 올린다.
- 하이퍼바이저에 개발 도구를 얹지 않는다. 호스트가 흔들리면 전 랩이 같이 죽는다.
- "Proxmox 호스트 설정을 자동으로 바꾸지 않는다"는 원칙은 실행 주체가 호스트 밖일 때만 지켜진다.
- 가장 쉬운 선택은 **같은 Proxmox 위의 VM 하나**. 물리 서버여도 되지만 [4. 관리망](#4-관리망-연결--1회) 을 읽어야 한다.

---

## 1. 구성

```
사무실 LAN                          Proxmox VE 호스트
┌──────────────┐                   ┌──────────────────────────────┐
│ 교육생 PC     │                   │ vmbr0      사무실 LAN          │
│              │                   │                              │
│ 랩 운영 서버   │ ── API 8006 ──▶   │ vmbr9      관리망 (전 랩 공용)   │
│  · terraform │ ── SSH   22 ──▶   │            랩N = VLAN 300N     │
│  · ansible   │      (랩 노드)     │ vmbr1101…  랩 서비스망 (격리)    │
│  · 웹 콘솔    │                   │                              │
└──────────────┘                   │ VM 13대 × 랩 수                │
      ▲                            └──────────────────────────────┘
      └── 교육생 브라우저 :8080
```

**운영 서버가 두 곳에 닿아야 한다.**

| 대상 | 포트 | 쓰는 곳 | 없으면 |
|---|---|---|---|
| Proxmox API | tcp/8006 | Terraform · 상태 점검 | VM 을 만들 수 없다 |
| 랩 관리망 `172.30.N.0/24` | tcp/22 | Ansible | VM 은 생기지만 설정이 안 들어간다 |

두 번째가 배포에서 가장 자주 막히는 지점이다 → [4. 관리망 연결](#4-관리망-연결--1회).

## 2. 요구사항

| 항목 | 값 |
|---|---|
| 랩 운영 서버 OS | Ubuntu 22.04+ / Debian 12+ (그 외는 `./install.sh --no-apt`) |
| Python | 3.10+ |
| 운영 서버 자원 | 2 vCPU · 2 GB RAM · 5 GB 디스크 |
| 운영 서버 계정 | **root 아님.** sudo 되는 일반 계정 |
| Proxmox | VE 8.x · 랩당 RAM 약 9 GB (64 GB → 6랩 권장) |
| 인터넷 | 설치 시에만 필요. 없으면 [8. 폐쇄망](#8-폐쇄망) |

Terraform · Ansible 을 손으로 깔지 않는다. `install.sh` 가 Terraform 을 `/usr/local/bin` 에,
Ansible 을 `console/.venv` 안에 넣는다 — 콘솔과 CLI 가 **같은 ansible** 을 쓴다.

## 3. 절차 한눈에 — CLI 와 GUI

| # | 단계 | 어디서 | 방식 | 횟수 |
|---|---|---|---|---|
| 1 | 저장소 배치 · `./install.sh --service` | 랩 운영 서버 | **CLI** | 1회 |
| 2 | `config/site.local.yml` 사내 값 입력 | 랩 운영 서버 | **CLI** | 1회 |
| 3 | `./infra/proxmox-setup.sh` — API 토큰 | Proxmox 호스트 · root | **CLI** | 1회 |
| 4 | `build-golden-template.sh` — 골든 템플릿 | Proxmox 호스트 · root | **CLI** | 1회 |
| 5 | `[관리자 → 연결 설정]` — 주소 · 토큰 입력 | 웹 콘솔 | **GUI** | 1회 |
| 6 | `[관리자 → 설치]` — 점검 · 관리망 브리지 생성 | 웹 콘솔 | **GUI** | 1회 |
| 7 | `[랩 생성]` → `[이 모듈 적용]` → `[검증]` | 웹 콘솔 | **GUI** | 수시 |
| 8 | `[관리자 → 계정 관리]` — 교육생 계정 | 웹 콘솔 | **GUI** | 수시 |

- **터미널은 1~4 뿐이다.** 3·4 는 다른 호스트(Proxmox)라 콘솔이 대신할 수 없다.
- 5~8 은 전부 화면이다. 일상 운영에 `make` 를 칠 일은 없다.
- `make` 타깃은 GUI 와 **같은 일을 하는 대안**이다 → [부록 A](#부록-a-cli-대안-표).
- `[관리자 → 설치]` 화면이 `make doctor` 와 **같은 검사**를 돌리고, 콘솔이 할 수 있는 것은 버튼으로,
  root 가 필요한 것은 복사할 명령으로 보여 준다. **오류 0 을 확인하고 랩을 만든다.**

---

## 단계별

### 1단계 — 운영 서버 설치 · CLI

저장소 배치. git 원격이 없으면 개발 PC 에서 `make pack` → `scp`.

```bash
tar xzf my-network-lab.tar.gz && cd my-network-lab
./install.sh --service
```

`install.sh` 가 하는 일 — OS 패키지 · Terraform(SHA256 검증) · venv(FastAPI + ansible-core) ·
SSH 키 생성(**공개키를 화면에 찍는다**) · `site.local.yml` 생성 · systemd 등록 · 사전 점검.

| 옵션 | 언제 |
|---|---|
| `--no-apt` | 패키지를 이미 깔았거나 apt 가 아닌 배포판 |
| `--service` | 부팅 시 자동 기동 (없으면 `make console` 로 수동) |
| `TERRAFORM_ZIP=/path.zip` | 폐쇄망 |
| `PORT=9000` | 콘솔 포트 변경 |

- `make pack` 은 `var/` 와 tfstate 를 담지 않는다 — 토큰이 들어 있고, 같은 tfstate 를 두 서버가 들면 안 된다.

### 2단계 — 사내 값 입력 · CLI

`config/site.local.yml` (git 제외). 아래는 예시값(RFC5737)이므로 사내 실제 값으로 바꾼다.

```yaml
access:
  office_lan: 192.0.2.0/24                 # 교육생 PC 대역
  proxmox:
    host_ip:      192.0.2.10
    api_endpoint: "https://192.0.2.10:8006/"
    node_name:    pve01                    # Proxmox 에서 `hostname -s` 의 값
    datastore:    local-lvm
  ssh_public_keys:
    - "ssh-ed25519 AAAA... my-network-lab@labsrv"   # install.sh 가 찍어 준 이 서버의 공개키
forbidden:
  - {cidr: 10.99.0.0/16, severity: error, reason: "사내 사용 중"}
```

```bash
make check      # 대역 충돌 · 용량 · 공개 안전성
make ipam       # 계산된 주소 계획 — 사내 대역과 겹치는지 눈으로 확인
```

**주의**
- `ssh_public_keys` 에 **이 서버의 공개키를 반드시 넣는다.** cloud-init 이 이 목록으로 노드의 `authorized_keys` 를 만든다. 빠지면 VM 은 뜨지만 Ansible 이 한 대도 로그인하지 못한다.
- 여기 넣은 키는 전 랩 전 노드에 박히고 바꾸려면 VM 을 다시 만들어야 한다. **교육생 키는 넣지 않는다** — 콘솔 `[접속 키]` 로 등록한다.
- `node_name` 을 틀리면 증상이 401/403 이 아니라 **"노드가 없다"** 다.
- 접속 값 우선순위: `site.yml` → `site.local.yml` → **`var/runtime.yml`(콘솔 [연결 설정])**. 뒤가 이긴다. 콘솔에서 한 번이라도 저장했으면 파일을 고쳐도 덮인다. 되돌리려면 `rm var/runtime.yml`. 지금 어느 쪽이 이기는지는 `make doctor` 의 "접속 값 출처".

### 3단계 — Proxmox API 토큰 · CLI (Proxmox 호스트 · root)

```bash
./infra/proxmox-setup.sh              # 역할 생성 · 사용자 · 토큰 발급 · 권한 검증
./infra/proxmox-setup.sh --show       # 상태만 점검 (변경 없음)
./infra/proxmox-setup.sh --new-token  # 비밀값을 잃었을 때 재발급
```

- **손으로 `pveum` 을 치지 않는다.** 권한 하나가 빠져도 티가 안 나고 `terraform apply` 가 403 으로 멈춘다.
- **웹 UI 로 토큰을 만들지 않는다.** Privilege Separation 이 기본 ON 이라 역할을 물려받지 못해 계속 403 이다 (`--privsep 0` 필요). 이 스크립트는 감지해서 꺼 준다.
- 토큰 비밀값은 **한 번만** 출력된다. 콘솔 `[연결 설정]` 에 넣는다 — **어떤 파일에도 쓰지 않는다** (`var/console.db` 0600 에 저장, 실행 순간에만 환경변수로 전달).

| 권한 | 없으면 |
|---|---|
| `Sys.Modify` (`/nodes/<node>`) | 브리지가 안 생긴다. VM 은 생겨도 랜선이 없다 |
| `VM.Clone` | 골든 템플릿을 복제하지 못한다 |
| `VM.Config.Cloudinit` | 관리망 주소 · SSH 키가 안 들어가 접속 불가 |
| `Datastore.AllocateSpace` | 디스크 · cloud-init 드라이브 생성 실패 |

### 4단계 — 골든 템플릿 · CLI (Proxmox 호스트 · root)

랩 노드는 인터넷에 못 나간다. 필요한 패키지(FRR · nftables · bind9 · tcpdump …)를 템플릿에 미리 넣는다.

**권장 — 이미지는 운영 서버에서 만들고 Proxmox 는 등록만 한다.**
`libguestfs-tools` 는 Debian main 에 있어 하이퍼바이저에 저장소를 추가해야 하기 때문이다.

```bash
# ① 운영 서버 (apt 가 자유롭다)
sudo apt install -y libguestfs-tools
./infra/template/build-golden-template.sh --image-only --out /tmp/lab.img
scp /tmp/lab.img root@<proxmox>:/var/lib/vz/template/lab/

# ② Proxmox 호스트 (qm 만 있으면 된다)
./infra/template/build-golden-template.sh --from-image /var/lib/vz/template/lab/lab.img --storage local-lvm
```

호스트에서 전부 하려면 `apt install -y libguestfs-tools` 후 `--storage local-lvm` 만으로 실행한다.

- VMID 는 `config/site.yml` 의 `labs.template_vmid` (기본 9000) 과 맞아야 한다.
- `supermin exited with error status 1` → 커널 권한(`sudo chmod 0644 /boot/vmlinuz-*`) 또는 `/dev/kvm` 없음(`export LIBGUESTFS_BACKEND_SETTINGS=force_tcg`, 10~25분 소요). 스크립트가 시작 전에 둘 다 확인한다.

### 5~6단계 — 연결 설정 · 설치 점검 · GUI

브라우저 `http://<운영서버>:8080` → `admin / admin` → **비밀번호 변경 강제** (변경 전에는 아무 화면도 안 보인다).

1. `[관리자 → 연결 설정]` — Proxmox 주소 · 노드 이름 · 데이터스토어 · **API 토큰** 입력 → 확인
2. `[관리자 → 설치]` — 점검 결과가 나온다
   - 콘솔이 대신 할 수 있는 것 → **버튼** (관리망 브리지 생성 · 교육생 접속 파일 · 문서 생성)
   - root / 다른 호스트가 필요한 것 → **복사할 명령**
3. **오류 0** 을 확인한 뒤 다음으로 넘어간다

### 7단계 — 랩 배포 · GUI

`[랩 생성]` → `[이 모듈 적용]` → `[검증]`.

- tfvars 와 인벤토리는 버튼을 누를 때마다 다시 만든다. 미리 생성해 둘 것이 없다.
- **배선은 항상 전체 토폴로지**다. 모듈을 넘어갈 때 VM 은 그대로 두고 설정 범위만 넓힌다.
- `[이 모듈 적용]` 은 랩을 그 모듈의 시작 상태로 되돌린다 — 교육생이 망가뜨렸을 때의 복구 버튼이기도 하다.

### 8단계 — 교육생 접속 · GUI

교육생은 **운영 서버를 ProxyJump 로 거쳐** 랩 노드에 SSH 한다.

```
교육생 PC ──ssh──▶ 운영 서버(점프 계정) ──ssh──▶ 랩 노드 (pc1 · r1 · web …)
```

점프 계정은 **셸이 없고 자기 랩 노드로만 나간다** (`ForceCommand` + nologin, `PermitOpen` = 자기 랩 13대:22).
운영 서버 셸을 주면 강사용 해설(`answers.md`)과 캡스톤 대응표를 읽을 수 있기 때문이다.

**관리자가 할 일**
- `[관리자 → 계정 관리]` 에서 계정 발급. 그 뒤는 자동이다.
- 교육생이 첫 로그인에서 비밀번호를 바꾸고 `[접속 키]` 에 공개키를 등록하면 콘솔이 점프 계정 적용과 랩 노드 반영을 **자동으로 건다**.
- 손으로 걸 때만 `[관리자 → 설치] → [점프 계정 적용]`. 콘솔에서 사라진 사람은 **접근을 회수한다**.
- 랩 구성(주소 · 랩 수)을 바꿨으면: `python3 tools/gen-policy.py | sudo tee /etc/my-network-lab/policy.json >/dev/null`

**교육생이 할 일** — `[접속 키]` 에서 공개키 등록, `[내 SSH 설정 내려받기]`. 관리자가 파일을 나눠 줄 일이 없다.

**Proxmox 콘솔 계정 (M0 실습 5 가 요구한다)**
교육생이 자기 관리 링크를 내리면 SSH 자체가 죽는다. 되돌릴 유일한 길이 화면 콘솔이다.

```bash
make consoleaccess                          # → dist/console-access.sh
scp dist/console-access.sh root@<proxmox>:/tmp/ && ssh root@<proxmox> /tmp/console-access.sh
```

- **랩당 1계정**이다 (1인 1계정 아님). 교육생이 늘어도 다시 하지 않고, **랩을 늘릴 때만** 한다.
- 계정 `lab<N>-console@pve` 는 **그 랩 VM 13대의 콘솔만** 열린다.
- 비밀번호는 `var/console.db` 에 있고 교육생 `[접속 키] → 5. 콘솔` 에 자기 랩 것만 표시된다.

---

## 4. 관리망 연결 — 1회

Terraform 은 API(8006)로만 붙으므로 문제없다. 막히는 쪽은 **Ansible** 이다 — `172.30.N.11` 은 Proxmox 안의 브리지에만 있다.

**설계** — 랩마다 관리망 브리지를 두지 않는다. 브리지 **하나**를 VLAN 으로 나눈다.

```
vmbr9   VLAN-aware · 물리 포트 없음 · 전 랩 공용     ← 1회 생성, 지우지 않는다
  ├ 랩1 노드 net0   tag 3001                      ← 랩 Terraform 이 붙인다
  ├ 랩2 노드 net0   tag 3002
  └ 운영 서버 net1   태그 없음(트렁크)               ← 1회 연결, 랩이 늘어도 그대로

vmbr1101 …          랩1 링크 브리지 + VM           ← 수시로 만들고 지운다
```

- 랩을 만들거나 지울 때 **Proxmox 호스트도 운영 서버 VM 설정도 건드리지 않는다.**
- 태깅을 브리지가 하므로 게스트는 untagged 프레임만 본다 → 격리 수준은 브리지를 나눈 것과 같다.
- 물리 NIC 이 없으니 이 L2 는 호스트 밖으로 나가지 않는다.

### 운영 서버가 Proxmox 위의 VM 일 때 (권장)

| 무엇 | GUI | CLI |
|---|---|---|
| 관리망 브리지 생성 | `[관리자 → 설치]` 의 **[관리망 브리지 만들기]** | `make mgmt LABS=9` |
| 이 서버를 거기 연결 | — (root 필요) | `make mgmt-net` |

`make mgmt-net` 이 하는 일 — 자기 VM 을 MAC 으로 찾고, 트렁크 NIC 을 **API 로 핫플러그**(재부팅 없음),
게스트에 VLAN 서브인터페이스 생성, 주소 확인.

```bash
make mgmt-net-dry        # 무엇을 할지만 보여준다
make mgmt-net VMID=9100  # 자동 탐지 실패 시 직접 지정
sudo rm /etc/netplan/60-lab-mgmt.yaml && sudo netplan apply    # 되돌리기
```

- **root 로 돌리지 않는다.** netplan 쓰는 부분에서만 sudo 를 쓴다 — root 로 돌면 `var/` 가 root 소유가 되어 콘솔이 자기 DB 를 못 고친다.
- 확인: `make doctor` 의 "관리망 브리지" · "이 서버의 관리망 주소" · "경로" 가 초록인지. `vlan_aware` 가 꺼져 있으면 오류로 잡는다.

### 운영 서버가 물리 서버일 때

Ansible 이 SSH ProxyJump 로 Proxmox 호스트를 지나간다.

```bash
ssh-copy-id root@192.0.2.10
```
```yaml
# config/site.local.yml
access: {jump_host: {proxy_via_proxmox: true, proxmox_ssh_user: root}}
```

경유 호스트에 아무것도 설치되지 않지만(`-W`) 느리고 Proxmox 를 SSH 경로에 끌어들인다. **VM 으로 올릴 수 있으면 그쪽이 낫다.**

> 정적 경로 + `ip_forward` 로 푸는 방법은 **쓰지 않는다.** Proxmox 호스트가 라우터가 되고, 랩의 브로드캐스트·잘못된 OSPF 광고가 사무실로 나갈 경로가 열린다.

### Proxmox 호스트에 주소를 줄 것인가 (선택)

호스트에서도 랩 노드를 확인하고 싶을 때만. `/etc/network/interfaces` 에 랩당 한 덩이:

```
auto vmbr9.3001
iface vmbr9.3001 inet static
    address 172.30.1.1/24
```

- 브리지 자체는 `make mgmt` 가 만든다. **손으로 넣는 것은 호스트 IP 뿐**이고 그것도 선택이다.
- 주소를 준다면 `dist/host-guard.nft` 을 함께 검토한다 (랩 → 하이퍼바이저 신규 연결 차단).

---

## 5. 코드 갱신 — 콘솔을 반드시 재시작한다

```bash
git pull
sudo systemctl restart my-network-lab
```

- **재시작을 빼면 증상이 헷갈린다.** 교재·화면(Jinja)은 요청마다 다시 읽지만 **라우트와 설계(`design/*.yml`)는 프로세스 기동 시 한 번** 읽는다 → 새 버튼은 보이는데 누르면 `{"detail":"Not Found"}`.
- 확인: `curl -s http://127.0.0.1:8080/healthz` 의 `modules` 수 = `ls -d modules/m*/ | wc -l`.
- **Ansible 역할이 바뀐 갱신이면** 랩에 `[이 모듈 적용]` 을 한 번 더 눌러야 반영된다.
- 브라우저는 강제 새로고침(Ctrl+Shift+R).
- `make console` 로 띄웠다면 그 창에서 Ctrl+C 후 재실행.

## 6. 운영

| 하는 일 | 방법 |
|---|---|
| 상태 점검 | `[관리자 → 설치]` 또는 `make doctor` |
| 서비스 상태 · 로그 | `systemctl status my-network-lab` · `journalctl -u my-network-lab -f` |
| 랩 초기화 | `[이 모듈 적용]` 또는 `make reset LAB=1 STAGE=m4` |
| 랩 삭제 | `[삭제]` 또는 `cd infra/terraform/envs/lab1 && terraform destroy` |
| 계정 목록 | `[관리자 → 계정 관리]` 또는 `make users` |

**백업 — `var/` 하나다.** 계정 · 비밀번호 해시 · 진도 · 시험 기록 · API 토큰이 전부 여기 있다.

```bash
tar czf var-$(date +%F).tar.gz var/     # 0600 으로 보관할 것
```

- tfstate(`infra/terraform/envs/lab*/`)도 잃으면 Terraform 이 기존 VM 을 모른다. 잃었으면 랩을 지우고 다시 만드는 편이 빠르다.
- 서버별 환경변수는 `var/console.env` (있으면 systemd 가 읽는다) — 사내 프록시 등. `chmod 600`.

## 7. 문제 해결

| 증상 | 원인 | 확인 · 조치 |
|---|---|---|
| `make doctor` TCP 연결 실패 | 방화벽 / 주소 오타 | `nc -vz <proxmox> 8006` |
| `must provide either username and password, an API token...` | 토큰을 못 찾았다 | `[연결 설정]` 에 입력. `make doctor` 의 "CLI 자격 증명" |
| 401 / 403 | privsep 켠 토큰에 ACL 없음 | `pveum user token modify terraform@pve lab --privsep 0` |
| 브리지 생성만 실패 | 역할에 `Sys.Modify` 없음 | `./infra/proxmox-setup.sh` 재실행 |
| "노드가 없다" | `node_name` 오타 | Proxmox 에서 `hostname -s` |
| VM 은 생겼는데 Ansible 전부 UNREACHABLE | 관리망 도달 불가 | [4. 관리망](#4-관리망-연결--1회). `ip route get 172.30.1.11` |
| 다른 랩 노드가 서로 보인다 | 브리지 `vlan_aware` OFF | `make doctor` 가 오류로 잡는다. `make mgmt` 재실행 |
| VM 이 기동하지 않는다 (bridge not found) | 관리망 브리지 미생성 | `[관리망 브리지 만들기]` |
| 특정 노드만 `Permission denied (publickey)` | 공개키 누락 상태로 cloud-init 실행됨 | `ssh_public_keys` 채우고 VM 재생성 |
| 새 버튼이 `{"detail":"Not Found"}` | 콘솔을 재시작하지 않았다 | [5. 코드 갱신](#5-코드-갱신--콘솔을-반드시-재시작한다) |
| 터미널에서 `ansible-playbook: command not found` | 정상이다 (venv 안에 있다) | `make config` 를 쓴다 |
| 서비스가 뜨자마자 죽는다 | 포트 충돌 / 권한 | `journalctl -u my-network-lab -n 50` |

로그는 콘솔 화면(SSE 실시간)과 `journalctl` 두 곳에 남는다. Terraform · Ansible 원문이 그대로 흐른다.

## 8. 폐쇄망

```bash
# 인터넷 되는 PC 에서
curl -LO https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip
pip download -d wheels -r console/requirements.txt
ansible-galaxy collection download -r infra/ansible/requirements.yml -p galaxy
terraform providers mirror ./tf-mirror

# 운영 서버에서
TERRAFORM_ZIP=~/terraform_1.9.8_linux_amd64.zip ./install.sh --no-apt
console/.venv/bin/pip install --no-index --find-links wheels -r console/requirements.txt
```

`~/.terraformrc` 로 프로바이더 미러(`registry.terraform.io/bpg/*`)를 가리킨다.

## 9. 삭제

```bash
for n in 1 2 3; do (cd infra/terraform/envs/lab$n && terraform destroy -auto-approve); done
sudo systemctl disable --now my-network-lab
sudo rm /etc/systemd/system/my-network-lab.service && sudo systemctl daemon-reload
```

- 관리망 브리지(`vmbr9`)는 랩 destroy 로 사라지지 않는다. 전부 없애려면 **운영 서버 NIC 을 먼저 떼고** `cd infra/terraform/envs/mgmt && terraform destroy`.
- Proxmox 호스트에 손으로 넣은 것은 (줬다면) 관리망 브리지 IP 뿐이다.

---

## 부록 A. CLI 대안 표

콘솔이 뜨지 않을 때나 자동화에 넣을 때. GUI 와 **같은 명령**이다.

| GUI | CLI |
|---|---|
| `[관리자 → 설치]` 점검 | `make doctor` |
| `[관리망 브리지 만들기]` | `make mgmt LABS=9` |
| `[랩 생성]` | `make deploy LAB=1` |
| `[이 모듈 적용]` | `make config LAB=1 STAGE=m1` |
| `[검증]` | `make verify LAB=1 STAGE=m1` |
| `[계정 관리]` | `python3 tools/console-user.py add trainee01 --lab 1` |
| `[점프 계정 적용]` | `make jumpaccess` → `sudo ./dist/jump-access.sh` |
| 교재 · 부록 파일로 뽑기 | `make gen LAB=1` (인쇄 · 오프라인 배포용. 화면은 이 파일 없이도 나온다) |

`make deploy` · `make mgmt` 도 콘솔과 같은 토큰을 쓴다 — `tools/with-pve-env.py` 가 `var/console.db` 에서 읽어
**실행 순간에만** 환경변수로 넘긴다. 따로 export 할 필요가 없다.
