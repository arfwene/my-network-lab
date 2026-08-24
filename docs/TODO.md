# 나중 할 일

확인은 됐지만 지금 고치지 않기로 한 것들. 우선순위 순.
고칠 때는 이 파일에서 지우고 커밋 메시지에 무엇을 지웠는지 남길 것.

---

## 1. `uninstall` 이 vmbr9 를 실제로는 안 지운다 — **확인됨**

*발견: 2026-08-24, 테스트 운영 서버에서 걷어내기 실전 1회*

**증상**
`tools/uninstall.sh` 는 `vmbr9 제거됨` 을 찍고 정상 종료했는데,
Proxmox 호스트에는 **vmbr9 가 import 됐을 때 그대로** 남아 있었다.

**왜 놓쳤나 (이게 진짜 결함)**
`tools/uninstall.sh:124-130` 은 `terraform destroy` 의 **종료 코드만** 보고 성공을 찍는다.

```sh
( cd "$MGMT_ENV" && ... terraform destroy ... ) \
  && ok "vmbr9 제거됨" \
  || warn "관리망 브리지 파괴 실패 — Proxmox 웹에서 직접 지울 것"
```

tfstate 가 비는 것과 호스트에서 브리지가 사라지는 것은 **다른 사건**이다.
지금 코드는 앞의 것만 보고 뒤의 것을 단정한다.
그래서 관리자는 "다 지워졌다" 고 믿고 재설치에 들어가고,
`vmbr9` 가 남아 있으면 재설치 때 `pve.preflight` 가 "남의 브리지" 로 오인하거나
comment 소유 표시가 안 맞아서 막힌다.

**원인 후보 (아직 미검증 — 서버에서 확인할 것)**
1. **Proxmox 의 네트워크 변경은 staged 다.** API 쓰기는 `/etc/network/interfaces.new` 에 들어가고,
   웹에서 [Apply Configuration] 하거나 `ifreload -a` 를 해야 실제 config 가 된다.
   destroy 는 성공했지만 적용이 안 된 상태일 수 있다. — **가장 유력**
2. **브리지가 사용 중이다.** 운영 서버 VM 의 트렁크 NIC 이 vmbr9 에 꽂혀 있으면
   커널이 브리지를 못 내린다. 걷어내기 순서상 VM NIC 제거가 브리지 파괴보다 뒤에 온다.
3. **손으로 만든 브리지를 import 한 경우.** provider 가 API 삭제를 no-op 로 흘리고
   state 만 비웠을 수 있다.

**확인 절차** (Proxmox 호스트에서)
```sh
ip -br link show vmbr9              # 커널에 살아 있나
grep -n -A6 vmbr9 /etc/network/interfaces
ls -l /etc/network/interfaces.new   # 있으면 = 미적용 변경이 대기 중 (후보 1 확정)
bridge link | grep vmbr9            # 꽂힌 게 있나 (후보 2 확정)
```

**고칠 방향**
- `uninstall.sh` 에 **파괴 후 검증**을 넣는다. exit code 가 아니라 상태를 본다.
  API 로 `/nodes/{node}/network` 를 다시 읽어 vmbr9 가 없는지 확인하고,
  남아 있으면 `ok` 가 아니라 `warn` + 다음에 할 일을 찍는다.
- 순서를 바꾼다: **운영 서버 VM 의 트렁크 NIC 제거 → 브리지 파괴.**
  지금은 NIC 제거가 `proxmox-cleanup.sh` 안내로만 있고 브리지 파괴 뒤에 온다.
- `interfaces.new` 가 남는 경우를 안내한다 — Proxmox 웹 [Apply Configuration] 을
  누르라고 명시. 이 프로젝트는 호스트 네트워크를 자동으로 건드리지 않는다는 원칙이 있으므로
  **자동 `ifreload` 는 하지 않는다.** 안내까지가 우리 몫이다.
- 같은 원칙으로 `proxmox-cleanup.sh` 의 vmbr9 안내(`tools/uninstall.sh:229`)도
  "아직 있다" 판정을 `/etc/network/interfaces` grep 하나에 의존하지 않게 한다.

---

## 2. 공개 저장소 히스토리에 사내 주소가 남아 있다 — **미결, 결정 필요**

현재 트리는 깨끗하다(`grep -rn '172\.16\.21' .` → 없음). **커밋 히스토리에만** 남아 있다.

| 커밋 | |
|---|---|
| `858cac5` | 초기 커밋 — 신입 네트워크 엔지니어 교육용 랩 |
| `888f3cc` | CLI 자격 증명 주입 · 예시 파일에서 사내 주소 제거 |

저장소는 **공개**다(`git@github.com:arfwene/my-network-lab.git`).
남아 있는 것은 RFC1918 사설 대역 하나뿐이라 그 자체로 침투 경로는 아니지만,
사내 대역 구조는 드러난다.

**선택지 (셋 중 하나를 고르면 된다 — 내 판단으로 정할 일이 아니다)**
1. `git filter-repo` 로 지우고 force push — 히스토리가 바뀌므로 clone 한 사람은 다시 받아야 한다.
   포크·캐시·GitHub 이벤트 API 에는 남을 수 있다.
2. 저장소를 private 으로 — 가장 확실하고 가장 싸다. 공개가 목적이 아니었다면 이쪽.
3. 그대로 둔다 — 사설 대역이고 이미 트리에서는 지웠다고 판단하는 경우.

---

## 3. 랩 노드 `lab` 계정 비밀번호가 전 랩 공용 — **설계상 미결**

`console/db.py:274 lab_console_password()` 에는 `lab_id` 인자가 없다.
랩 1 교육생과 랩 3 교육생이 **같은 노드 비밀번호**를 받는다.

지금은 Proxmox 콘솔 ACL 이 막고 있다 — `lab{N}-console` 계정은 자기 랩 VMID 에만
`VM.Console` 이 있으므로(`tools/gen-console-access.py`) 비밀번호를 알아도 남의 화면을 못 연다.
**심층 방어가 한 겹 얇을 뿐 뚫린 상태는 아니다.**

**왜 지금 안 고쳤나**
cloud-init 은 VM 최초 기동 때 **한 번만** 돈다. 비밀번호를 랩별로 나누려면
기존 랩 VM 을 전부 재생성해야 한다.

**언제 하면 되나**
어차피 랩을 다시 만들 계획이 있을 때. 지금 걷어내고 재설치하는 중이라면 **그때가 적기다.**
바꿀 것: `lab_console_password()` → `lab_console_password(lab_id)`,
설정 키 `lab.console_password` → `lab{N}.console_password`
(`lab_pve_account()` 가 이미 같은 패턴을 쓴다 — 그대로 따라가면 된다).

---

## 4. 로드맵 잔여

| Step | | 상태 |
|---|---|---|
| 8 | 웹 콘솔 v2/v3 — 장애 주입 UI · 진도 기록 · 웹 터미널 | 선택 |
| 9 | AWS 이식 실제 구현 + M11 확장 모듈 | 선택 · 보류 |
