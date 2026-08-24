# my-network-lab

신입 네트워크 엔지니어 교육용 랩. Proxmox VE 위에 13노드 네트워크를 코드로 구축한다.

- **커리큘럼 · 설계 근거** → [PLAN.md](PLAN.md)
- **배포 (서버에 올려서 실제로 돌리기)** → [docs/DEPLOY.md](docs/DEPLOY.md)
- **랩 지도 (교육생 상시 참조)** → 웹 콘솔 **[랩 지도]** (설계 파일에서 그때그때 만든다)
- **접근 방법 · 격리 설계** → `dist/access.md` (원본: `docs/templates/access.md.j2`)

---

## 빠른 시작

**설계를 읽고 고쳐 보기만 할 때** — Proxmox 도 Terraform 도 필요 없다.

```bash
cp config/site.local.yml.example config/site.local.yml   # 우리 환경 값 (git 제외)
$EDITOR config/site.local.yml
make check          # 대역 충돌 · 용량 · 공개 안전성 검사
make gen LAB=1      # Terraform 변수 · Ansible 인벤토리 · 문서 생성
make ipam           # 계산된 주소 계획 확인
```

기본값(RFC5737 문서 전용 대역)만으로도 전체가 계산된다. 아무것도 고치지 않고 산출물을 볼 수 있다.

**실제로 랩을 띄울 때** — 리눅스 서버 한 대에 올린다. 자세한 절차는 [docs/DEPLOY.md](docs/DEPLOY.md).

터미널에서 하는 일은 네 줄이고, **나머지는 웹 콘솔 [관리자 → 설치] 화면에서 끝난다.**

```bash
# ① 운영 서버에서
./install.sh --service            # 패키지 · Terraform · venv · SSH 키 · systemd 등록
$EDITOR config/site.local.yml     # Proxmox 주소 · 노드 이름 · 사내 금지 대역

# ② Proxmox 호스트에서 root 로 (다른 호스트라 콘솔이 대신 할 수 없다)
./infra/proxmox-setup.sh                                    # 권한 · API 토큰
./infra/template/build-golden-template.sh --storage local-lvm   # 골든 템플릿
```

그다음 브라우저로 `:8080` → `admin/admin` → 비밀번호 변경 → **[연결 설정]** 에 토큰을 넣고
확인을 누르면 **[설치]** 화면으로 넘어간다. 그 화면이 남은 것을 전부 보여 준다.

| | |
|---|---|
| 무엇이 준비됐나 | `make doctor` 와 **같은 검사**를 화면에서 돌린다 |
| 콘솔이 할 수 있는 것 | 버튼 — 관리망 브리지 · 교육생 접속 파일 · 문서 생성 |
| root 가 필요한 것 | 복사할 명령 — `sudo make mgmt-net`, `sudo ./dist/jump-access.sh` 등 |

오류가 0 이 되면 랩 화면에서 **[랩 생성]**. `make` 를 칠 일은 없다.
오류가 남아 있는 채로 배포하지 않는다 — 여기서 걸리는 게 배포 도중에 걸리는 것보다 싸다.

### 필요한 것

| 어디에 | 무엇이 |
|---|---|
| 랩 운영 서버 | Ubuntu 22.04+ / Debian 12+ · Python 3.10+ · sudo 되는 일반 계정 (`install.sh` 가 나머지를 깐다) |
| Proxmox 호스트 | VE 8.x · API 토큰 · 골든 템플릿(VMID 9000) · 랩당 RAM 약 9GB |
| 사이 | 운영 서버 → Proxmox API `8006`, 운영 서버 → 랩 관리망 `22` |

Terraform·Ansible 을 손으로 깔 필요는 없다. Ansible 은 `console/.venv` 안에만 들어가고,
`make config` 같은 CLI 타깃도 그 venv 를 쓴다 — 콘솔과 터미널이 같은 ansible 을 쓴다.

---

## 설계 원칙

| 원칙 | 의미 |
|---|---|
| 환경 값은 한 파일에 | 주소 블록·접속 정보는 `config/site.yml` 에만 있다. 실제 값은 `site.local.yml`(git 제외). |
| 설계는 한 곳에 | 배선·배분 규칙·라우팅은 `design/*.yml` 에만 있다. **절대 주소는 어디에도 없다.** |
| 생성물은 `dist/` 로 | 실제 환경 값이 박힌 산출물은 저장소에 올리지 않는다. 언제든 다시 만들 수 있다. |
| 배선은 전체, 설정은 단계별 | Terraform 은 항상 전체 토폴로지를 만든다. `lab_stage` 가 설정 범위를 정한다. 실제 현장처럼 "케이블은 꽂혀 있지만 설정이 없다". |
| 설정은 SSH 로만 | 노드 설정을 Proxmox 기능으로 하지 않는다. Ansible 이 SSH 로 넣는다 → AWS 이식 시 그대로 재사용. |
| 관리망은 분리 | `mgmt0` 는 랩과 라우팅으로 연결되지 않는다. 랩이 죽어도 접속은 살아 있다. 랩끼리는 VLAN 으로 갈린다. |
| 1분 안에 초기화 | 교육생이 실험을 두려워하지 않으려면 되돌리기가 싸야 한다. |

---

## 디렉토리

```
config/           ← 우리 환경 (여기부터 고친다)
  site.yml          주소 블록 · 접속 정보 · 금지 대역   [공개 안전한 기본값]
  site.local.yml    실제 값 (git 제외)
design/           ← 랩 설계 (절대 주소 없음)
  topology.yml      노드 · 링크 · VLAN
  ipam.yml          블록을 어떻게 자를 것인가 (배분 규칙)
  routing.yml       정적 / OSPF / 경계 라우팅 (상징적 표기)
tools/            설계 → 산출물 생성기
  labdesign.py      주소 계산 엔진 (단일 진실원)
  validate-site.py  대역 충돌 · 공개 안전성 검사
infra/
  template/         골든 템플릿 빌드 (Proxmox 호스트에서 실행)
  terraform/        VM · 브리지 프로비저닝
  ansible/          노드 설정 (역할별)
docs/
  DEPLOY.md         배포 절차 (서버 준비 → Proxmox 연결 → 첫 랩)
  templates/        문서 템플릿
  appendix/         부록 4종 템플릿 (치트시트 · 플로우차트 · 용어집 · 벤더 CLI)
deploy/           systemd 유닛 템플릿
install.sh        운영 서버 1회 설치 스크립트
dist/             ← 생성물 (git 제외, make clean 대상)
                    랩 지도 · 접근 문서 · SSH 설정 · 모듈 교재 · 부록
                    ops-server.md (운영 서버 관리망 연결, 1회) · host-guard.nft
var/              ← 운영 데이터 (git 제외, make clean 제외). 계정 DB · 진행 상태 · 시험 기록
modules/          모듈별 교재 (mXX/{meta.yml, README.md.j2, tasks, answers, verify.sh})
scenarios/        장애 주입 (mXX-NN.yml, break/fix 겸용)
console/          웹 콘솔 (FastAPI · 의존성 최소)
```

### 주소는 어디서 오는가

```
config/site.yml        design/ipam.yml           tools/labdesign.py
  lab_block:        +    site-a = /19 index 0   =   10.10.0.0/19
  10.10.0.0/16           vlan10 = /24 index 10      10.10.10.0/24
                         pc1    = offset 11         10.10.10.11
```

`lab_block` 한 줄을 바꾸면 Terraform·Ansible·FRR 설정·DNS 존·방화벽 규칙·문서가 **전부** 따라 바뀐다.
확인: `make ipam`

---

## 최초 구축 (관리자, 1회)

```bash
# 0. Proxmox 호스트에서 API 토큰 준비 (권한 확인까지 해 준다)
./infra/proxmox-setup.sh

# 1. 골든 템플릿 생성 — 랩 노드는 인터넷에 못 나가므로 패키지를 전부 여기 넣는다
#    libguestfs-tools 는 Debian main 에 있다. 하이퍼바이저에 저장소를 더하고
#    싶지 않으면 이미지는 운영 서버에서 만들고 Proxmox 는 등록만 한다:
#      (운영 서버)  ./infra/template/build-golden-template.sh --image-only --out /tmp/lab.img
#      (Proxmox)   ./infra/template/build-golden-template.sh --from-image /var/lib/vz/template/lab/lab.img
apt update && apt install -y libguestfs-tools     # 호스트에서 다 할 경우
./infra/template/build-golden-template.sh --storage local-lvm

# 2. (선택) Proxmox 호스트에 랩 관리망 IP 부여
#    /etc/network/interfaces 에 vmbr9.<VLAN> 의 address 172.30.{N}.1/24 추가
#    없어도 랩은 돈다 — 호스트에서 노드를 확인하고 싶을 때만 준다

# 3. 랩 운영 서버 준비 — Proxmox 호스트가 아닌 별도 리눅스 서버(같은 Proxmox 위의 VM 권장)
#    Terraform · Ansible · 웹 콘솔이 전부 여기서 돈다.
./install.sh --service

# 4. 관리망 브리지 + 운영 서버 트렁크 NIC — 여기까지가 1회 작업이다
#    브리지는 웹에서 만든다: [연결 설정] 에서 랩 개수를 정하고 [확인하고 저장] →
#    점검이 "vmbr9 가 없다" 를 잡으면 그 자리의 [지금 만들기] 버튼.
make opsvm VMID=<운영서버 VMID> LABS=9   # → dist/ops-server.md 의 명령을 그대로 실행
```

> 셸에서 하고 싶다면 `make mgmt LABS=9` 도 그대로 된다. 같은 일을 한다.

**4번을 한 번 해 두면 그 뒤로는 랩을 몇 번 만들고 지우든 Proxmox 호스트도
운영 서버의 VM 설정도 건드리지 않는다.** 관리망은 브리지 하나를 랩별 VLAN 으로 나눈 것이라,
운영 서버에 붙는 NIC 은 랩 수와 무관하게 **하나**(트렁크)다.

Proxmox 호스트 위에서 직접 돌려도 동작하지만 권하지 않는다.
하이퍼바이저가 흔들리면 랩 전체가 같이 죽고, "호스트 설정을 자동으로 바꾸지 않는다"는
이 저장소의 원칙은 실행 주체가 호스트 밖에 있을 때 지켜진다. → [docs/DEPLOY.md](docs/DEPLOY.md)

## 랩 1세트 배포

```bash
make doctor                      # 사전 점검부터. 여기서 걸리는 게 배포 중에 걸리는 것보다 싸다
make gen    LAB=1                # 설계 → tfvars · 인벤토리 · 교재 · 부록 · SSH 설정
make deploy LAB=1                # terraform apply (브리지 14 + VM 13)
make config LAB=1 STAGE=m1       # Ansible — 해당 단계 설정만
make verify LAB=1 STAGE=m1
```

<details><summary>make 없이 직접 실행할 때</summary>

```bash
LAB=1

# 설계 → Terraform 변수 / Ansible 인벤토리 생성
python3 tools/gen-tfvars.py    --lab $LAB
python3 tools/gen-inventory.py --lab $LAB

# 브리지 + VM 생성 (배선은 항상 전체 토폴로지)
cd infra/terraform/envs/lab$LAB    # main.tf 는 gen-tfvars 가 배치한다
export PROXMOX_VE_API_TOKEN='terraform@pve!lab=...'   # 토큰은 환경변수로
terraform init && terraform apply

# 설정 적용 — 단계를 지정한다
cd ../../../ansible
ansible-playbook -i inventory/lab$LAB playbooks/site.yml -e lab_stage=m1

# 검증
ansible-playbook -i inventory/lab$LAB playbooks/verify.yml -e lab_stage=m1

# 교육생 접속 설정 배포
python3 ../../tools/gen-ssh-config.py --lab $LAB > ssh-config-lab$LAB
```

</details>

## 웹 콘솔

브라우저에서 교재를 읽고 버튼으로 모듈을 적용한다. (점프 호스트에서 실행)

```bash
make console-setup                                    # 최초 1회 (install.sh 가 이미 했다면 생략)
python3 tools/console-user.py add trainee01 --lab 1    # 계정 발급
make console                                          # http://<운영서버>:8080
```

부팅 시 자동 기동은 `./install.sh --service` (= `make service`).
상태는 `systemctl status my-network-lab`, 로그는 `journalctl -u my-network-lab -f`.

| 종류 | 랩 | 계정 관리 | 해설 |
|---|---|---|---|
| **관리자** | 전체 | ○ | ○ |
| **사용자** | 배정된 랩 하나 | ✗ | ✗ |

사용자 계정에는 **랩 생성·실행·삭제 권한만** 준다.

### 접속 키

교육생은 콘솔 **[접속 키]** 에서 자기 SSH 공개키를 직접 등록한다.
등록한 키는 **배정된 랩의 노드에만** 들어간다 (`var/console.db` → 인벤토리 → Ansible).

`config/site.yml` 의 `access.ssh_public_keys` 에는 **운영 서버 키만** 넣는다.
거기 넣은 키는 cloud-init 으로 전 랩 전 노드에 박히고, 바꾸려면 VM 을 다시 만들어야 한다.

```bash
python3 tools/console-user.py key user01 --file ~/.ssh/id_ed25519.pub   # 관리자가 대신 등록
python3 tools/console-user.py keys --lab 1                              # 그 랩에 배포될 키
```

최초 기동 시 관리자 계정 `admin / admin` 이 만들어지고, **첫 로그인에서 비밀번호 변경을 강제**한다.
비밀번호는 8자 이상 + 특수문자 1개 이상, 로그인 5회 실패 시 5분 잠금 (`config/site.yml` 에서 조정).

계정은 `var/console.db` (SQLite) 에 있고 비밀번호는 pbkdf2 해시로만 저장한다.
세션 쿠키에는 이름만 담아 **권한 회수가 즉시 반영**된다. 관리는 `/admin` 화면 또는 `tools/console-user.py`.

CDN 을 쓰지 않는다 — 토폴로지는 서버에서 SVG 로 직접 그리고, 프론트엔드는 `app.js` 한 파일이다.
자세한 내용은 [console/README.md](console/README.md).

## 모듈 진행

```bash
# M1 → M2 로 넘어갈 때: 설정만 다시 올린다. VM 은 그대로.
ansible-playbook -i inventory/lab1 playbooks/site.yml -e lab_stage=m2
```

### 캡스톤 (M10)

마지막 모듈은 **제한 시간 시험**이다. 웹 콘솔에서 `[캡스톤 시작]` 을 누르면
랩이 초기화되고 **서버가 무작위로 고른 장애**가 주입된다 (무엇인지 알려주지 않는다).

시간이 끝나면 **그 순간의 검사 결과가 확정 성적**이 되고, 그 뒤에 고쳐도 바뀌지 않는다.
못 고친 것은 **인계 보고서**에 남기는 것까지가 과제다.

설정은 `config/site.yml` 의 `console.capstone` 에 있다 (제한 시간 · 장애 개수 · 출제 범위).
운영 방법과 장애 대응표는 M10 의 **해설 탭**(관리자 전용)에 있다.

## 부록

과정 중에 곁에 두고, 수료 후에 현장에서 꺼내 보는 문서 4종이다.
웹 콘솔의 **[부록]** 에서 보고, 인쇄용 사본은 `dist/appendix/` 에 생성된다.

| 문서 | 내용 |
|---|---|
| 한 장 치트시트 | 3-step 순서대로 정리한 명령 모음 + 이 랩의 주소 · 자주 틀리는 것 |
| 트러블슈팅 플로우차트 | 신고 문장에서 원인까지 가는 결정 트리 (글자로 그려 인쇄해도 안 깨진다) |
| 용어집 | 이 과정에 나온 용어를 한 줄로 + **흔한 오해** |
| 벤더 CLI 매핑 | FRR·Linux ↔ Cisco IOS. **회사 실장비 열은 비워 두었다** |

```bash
make appendix        # dist/appendix/ 로 렌더링 (make gen 에 포함돼 있다)
```

부록에도 **주소를 하드코딩하지 않는다.** 치트시트에 적힌 IP 가 랩과 다르면
쓸모없는 정도가 아니라 **틀린 곳으로 안내하기** 때문이다.

## 백업

**`var/` 하나다.** 계정·비밀번호 해시·진도·시험 기록·API 토큰이 전부 여기 있다.
나머지는 `config/` 에서 언제든 다시 만들어진다.

```bash
tar czf var-$(date +%F).tar.gz var/     # 0600 으로 보관할 것 — 토큰이 들어 있다
```

## 초기화 (교육생이 망가뜨렸을 때)

```bash
ansible-playbook -i inventory/lab1 playbooks/reset.yml -e lab_stage=m4
```

---

## 설계를 바꿀 때

`config/` 또는 `design/` 을 고치고 다시 생성한다. **생성물을 직접 고치지 않는다.**

```bash
make gen LAB=1
```

## 공개 저장소로 내보내기 전

```bash
make check     # = tools/validate-site.py --publish
```

검사 항목:
- 랩 대역끼리, 그리고 사무실 대역과 겹치지 않는가
- `forbidden` 에 등록한 사내 사용 중 대역과 겹치지 않는가
- 대역 용량이 충분한가 (구역 배분 · 랩 개수 · 공인 블록)
- **`site.local.yml` 의 사내 값이 다른 파일로 새지 않았는가** ← 공개 전 필수
- `.gitignore` 가 `site.local.yml` 을 막고 있는가

## 다중 랩 (1인 1랩)

| 자원 | 규칙 | lab 3 예시 |
|---|---|---|
| 브리지 | `vmbr{lab_id}{id}` | `vmbr3101` |
| 관리망 | **랩 자원 아님.** 전 랩 공용 브리지 1개를 VLAN 으로 분리 (`envs/mgmt` 가 1회 생성) | `vmbr9` VLAN `3003` |
| VMID | `lab_id * 100 + 노드번호` | `301` |
| VM 이름 | `lab{lab_id}-{노드}` | `lab3-pc1` |
| 관리망 주소 | `networks.management` 의 lab_id 번째 /24 | 기본값이면 `172.30.3.11` |
| 랩 서비스망 | **전 랩 동일** (`networks.lab_block`) | 기본값이면 `10.10.10.11` |

랩 서비스망을 일부러 동일하게 둔다. 브리지가 달라 절대 섞이지 않고,
교재·정답·검증 스크립트를 하나로 유지할 수 있다.

호스트 RAM 64GB 기준 **6랩** 권장 (랩당 약 9GB).
