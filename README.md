# my-network-lab

신입 네트워크 엔지니어 교육용 실습 랩. Proxmox VE 위에 **13노드 네트워크를 코드로 세우고**,
웹 콘솔에서 교재 · 실습 · 검증 · 장애 진단 · 시험까지 한 화면에서 진행한다.

- 배포 절차 → **[docs/DEPLOY.md](docs/DEPLOY.md)**
- 커리큘럼 설계 근거 → [PLAN.md](PLAN.md)
- 웹 콘솔 내부 구조 → [console/README.md](console/README.md)

---

## 1. 대상

| 역할 | 무엇을 하나 | 쓰는 도구 |
|---|---|---|
| **교육생** (신입 네트워크 엔지니어) | 교재를 읽고 노드에 SSH 해서 직접 설정한다. 검증·장애 진단·캡스톤 | 웹 브라우저 + SSH |
| **강사 / 랩 관리자** | 랩 배포 · 계정 발급 · 진도 승인 · 해설 조회 | 웹 콘솔 (터미널은 최초 구축 때만) |

전제 조건 — 교육생: 리눅스 기본 명령, SSH 접속. 사전 네트워크 지식은 요구하지 않는다.

## 2. 무엇이 들어 있나

| 구성 | 내용 |
|---|---|
| 랩 토폴로지 | VM 13대 — PC 2 · 스위치 2 · 라우터 4 · 경계 장비 1 · 서버 3(web/ftp/dns) · 외부망 1 + 링크 브리지 14개 |
| 커리큘럼 | 12개 모듈 · 총 38시간. 교재 · 과제 · 퀴즈 · 자동 검증 · 강사용 해설 |
| 장애 시나리오 | 37개. 주입 / 복구 버튼 하나로 되돌린다 |
| 평가 | 모듈별 검증 · 중간 점검 2회 · 캡스톤 시험(제한 시간 · 무작위 장애) |
| 부록 | 치트시트 · 트러블슈팅 플로우차트 · 용어집 · 벤더 CLI 매핑(FRR/Linux ↔ Cisco IOS) |

**한 랩이 곧 1인분이다.** 랩끼리 VLAN 으로 갈라 서로 보이지 않는다.

## 3. 커리큘럼

| 모듈 | 주제 | 시간 | 직접 만드는 단계 |
|---|---|---|---|
| M0 | 오리엔테이션 — 접속하고, 망가뜨리고, 되돌리기 | 1h | |
| M1 | L1/L2 와 스위칭 | 3h | |
| M2 | IP 주소와 L3 의 등장 | 4h | ● |
| M3 | 정적 라우팅 | 3h | ● |
| M4 | 동적 라우팅 — OSPF | 4h | ● |
| M5 | 게이트웨이 이중화 — VRRP | 3h | ● |
| M6 | IPv6 | 3h | ● |
| M7 | 전송 계층 — 포트와 세션 | 3h | |
| M8 | 패킷 분석 — tcpdump 심화 | 3h | |
| M9 | 응용 계층 — HTTP · DNS · FTP | 4h | ● |
| M10 | NAT 와 방화벽 | 3h | ● |
| M11 | 트러블슈팅 방법론 + 캡스톤 | 4h | |

- **직접 만드는 단계(●)** — 랩이 한 단계 앞까지만 설정된 채로 올라온다. 교재 3장이 그 차이를 손으로 채우는 과정이고, 검증은 완성본을 본다.
- 나머지 단계 — 완성된 설정이 올라오고, 실습은 그것을 관찰하고 망가뜨려 보는 쪽이다.

## 4. 배포 요약

절차 전체와 상세는 **[docs/DEPLOY.md](docs/DEPLOY.md)**. 어디서 무엇을 하는지만 옮기면:

| # | 단계 | 어디서 | 방식 |
|---|---|---|---|
| 1 | 운영 서버 설치 (`./install.sh --service`) | 랩 운영 서버 | **CLI** |
| 2 | 사내 값 입력 (`config/site.local.yml`) | 랩 운영 서버 | **CLI** |
| 3 | API 토큰 발급 (`./infra/proxmox-setup.sh`) | Proxmox 호스트 · root | **CLI** |
| 4 | 골든 템플릿 생성 (`build-golden-template.sh`) | Proxmox 호스트 · root | **CLI** |
| 5 | 연결 설정 · 관리망 브리지 · 설치 점검 | 웹 콘솔 `[관리자 → 설치]` | **GUI** |
| 6 | 랩 생성 · 설정 적용 · 검증 | 웹 콘솔 `[랩]` | **GUI** |
| 7 | 계정 발급 · 진도 승인 · 장애 주입 | 웹 콘솔 `[관리자]` | **GUI** |

- **1~4 만 터미널이다.** 그 뒤 일상 운영에 `make` 를 칠 일은 없다.
- `make` 타깃은 GUI 와 **같은 일을 하는 대안**이다 (자동화 · 콘솔이 뜨지 않을 때).
- 코드를 갱신하면 **콘솔 재시작이 필요하다** — 라우트와 설계 파일은 프로세스 기동 시 한 번 읽는다.

### 설계만 읽어 볼 때 (Proxmox 불필요)

```bash
cp config/site.local.yml.example config/site.local.yml
make check          # 대역 충돌 · 용량 · 공개 안전성
make gen LAB=1      # tfvars · 인벤토리 · 교재 · 부록 생성 → dist/
make ipam           # 계산된 주소 계획
```

기본값(RFC5737 문서 전용 대역)만으로 전체가 계산된다.

## 5. 설계 원칙

| 원칙 | 의미 |
|---|---|
| 환경 값은 한 파일에 | 주소 블록 · 접속 정보는 `config/site.yml`. 실제 값은 `site.local.yml` (git 제외) |
| 설계에 절대 주소 없음 | `design/*.yml` 은 배선과 배분 규칙만. 주소는 `tools/labdesign.py` 가 계산한다 |
| 생성물은 `dist/` 로 | 실제 값이 박힌 산출물은 커밋하지 않는다. 언제든 다시 만든다 |
| 배선은 전체, 설정은 단계별 | Terraform 은 항상 전체 토폴로지. `lab_stage` 가 설정 범위를 정한다 |
| 설정은 SSH 로만 | 노드 설정에 Proxmox 기능을 쓰지 않는다 (Ansible only) |
| 관리망 분리 | `mgmt0` 는 랩과 라우팅으로 이어지지 않는다. 랩이 죽어도 접속은 살아 있다 |
| 호스트를 자동으로 안 건드림 | Proxmox 호스트 설정 변경은 관리자가 명시적으로 한다 |
| 1분 안에 초기화 | 되돌리기가 싸야 교육생이 실험한다 |

### 주소는 어디서 오는가

```
config/site.yml        design/ipam.yml          tools/labdesign.py
  lab_block:       +   site-a = /19 index 0   =   10.10.0.0/19
  10.10.0.0/16         vlan10 = /24 index 10      10.10.10.0/24
                       pc1    = offset 11         10.10.10.11
```

`lab_block` 한 줄을 바꾸면 Terraform · Ansible · FRR · DNS 존 · 방화벽 · 교재가 전부 따라 바뀐다. 확인은 `make ipam`.

## 6. 디렉토리

```
config/       우리 환경 값        site.yml (공개 안전 기본값) · site.local.yml (git 제외)
design/       랩 설계             topology.yml · ipam.yml · routing.yml   ← 절대 주소 없음
tools/        생성기              labdesign.py (주소 계산) · validate-site.py · gen-*.py
infra/
  template/     골든 템플릿 빌드 (Proxmox 호스트에서 실행)
  terraform/    VM · 브리지 프로비저닝
  ansible/      노드 설정 (역할별)
modules/      모듈 교재          mXX/{meta.yml, README.md.j2, tasks, answers, assessment.yml}
scenarios/    장애 주입          mXX-NN.yml (break / fix 겸용)
console/      웹 콘솔            FastAPI · 의존성 최소 · CDN 미사용
docs/         문서               DEPLOY.md · HANDOVER.md · appendix/ · templates/
deploy/       systemd 유닛 템플릿
install.sh    운영 서버 1회 설치
dist/         생성물 (git 제외)   교재 · 부록 · 랩 지도 · SSH 설정 · ops-server.md
var/          운영 데이터 (git 제외 · 백업 대상)  계정 DB · 진행 상태 · 시험 기록 · API 토큰
```

## 7. 다중 랩 (1인 1랩)

| 자원 | 규칙 | lab3 예시 |
|---|---|---|
| 링크 브리지 | `vmbr{lab_id}{id}` | `vmbr3101` |
| 관리망 | 전 랩 공용 브리지 1개를 VLAN 으로 분리 (1회 생성) | `vmbr9` VLAN `3003` |
| VMID | `lab_id * 100 + 노드번호` | `301` |
| VM 이름 | `lab{lab_id}-{노드}` | `lab3-pc1` |
| 관리망 주소 | `networks.management` 의 lab_id 번째 /24 | `172.30.3.11` |
| 랩 서비스망 | **전 랩 동일** — 브리지가 다르므로 섞이지 않는다 | `10.10.10.11` |

- 서비스망을 일부러 통일한다 → 교재 · 정답 · 검증을 랩마다 나눌 필요가 없다.
- 용량 기준: 랩당 약 9 GB. 호스트 RAM 64 GB 에서 **6랩** 권장.

## 8. 백업 · 초기화

| 무엇 | 명령 |
|---|---|
| 백업 (이것 하나면 된다) | `tar czf var-$(date +%F).tar.gz var/` — **0600 보관. API 토큰이 들어 있다** |
| 랩 초기화 | 웹 콘솔 `[이 모듈 적용]` 또는 `make reset LAB=1 STAGE=m4` |
| 공개 저장소 내보내기 전 | `make check` — 사내 값 유출 · 대역 충돌 검사 |
