LAB ?= 1
STAGE ?= m10
LABS ?= 9          # 관리망 VLAN 을 몇 개 랩만큼 준비할 것인가 (최대 9. 브리지는 늘 1개)
VMID ?=            # 운영 서버 VM 의 VMID (dist/ops-server.md 의 qm 명령에 박힌다)
OPSNET ?= 1        # 운영 서버의 관리망 트렁크 NIC 번호 (netN). 랩 수와 무관하게 1개다
PY := python3
# Terraform 은 API 토큰이 있어야 한다. 토큰은 var/console.db 에만 있으므로
# CLI 타깃도 콘솔과 같은 경로로 주입한다 (파일로 내보내지 않는다).
PVE := $(PY) $(CURDIR)/tools/with-pve-env.py --

VENV := console/.venv
# Ansible 은 venv 안에만 설치한다 (console/requirements.txt). 시스템에 깔지 않는다.
# 웹 콘솔(jobs.py)도 같은 규칙으로 고르므로, CLI 와 콘솔이 같은 ansible 을 쓴다.
APB := $(shell test -x $(VENV)/bin/ansible-playbook && echo $(CURDIR)/$(VENV)/bin/ansible-playbook || echo ansible-playbook)

.PHONY: help doctor check gen docs modules appendix opsvm mgmt ipam deploy config verify \
        reset break fix scenarios console console-setup service pack users clean jumpaccess \
        mgmt-net mgmt-net-dry consoleaccess

help:
	@echo "▸ 보통은 make 를 칠 일이 없다 — 웹 콘솔 [관리자 → 설치] 화면이 같은 일을 한다."
	@echo "  아래는 콘솔 없이 터미널에서 같은 일을 할 때 쓴다."
	@echo ""
	@echo "make doctor         배포 사전 점검 (도구 · 설정 · Proxmox · 관리망)"
	@echo "make check          설정 검사 (대역 충돌 · 용량 · 공개 안전성)"
	@echo "make gen LAB=1      설계 -> Terraform/Ansible/문서 전부 생성"
	@echo "make docs           문서만 생성 (dist/)"
	@echo "make ipam           계산된 주소 계획 출력"
	@echo "make mgmt LABS=9    관리망 브리지 생성 (최초 1회. 전 랩 공용 · VLAN 으로 분리)"
	@echo "make deploy LAB=1   Terraform apply (VM · 랩 링크 브리지)"
	@echo "make config LAB=1 STAGE=m1   Ansible 설정 적용"
	@echo "make verify LAB=1 STAGE=m1   단계별 도달성 검증"
	@echo "make reset  LAB=1 STAGE=m1   랩 초기화"
	@echo "make modules                 모듈 교재 렌더링 (dist/modules/)"
	@echo "make appendix                부록 렌더링 (dist/appendix/)"
	@echo "make mgmt-net                운영 서버를 관리망에 연결 (1회. NIC 부착 + VLAN 설정)"
	@echo "make opsvm VMID=9100         위를 손으로 할 때의 절차 문서 (dist/ops-server.md)"
	@echo "make jumpaccess              교육생 점프 계정 생성 절차 (dist/jump-access.*)"
	@echo "make consoleaccess           교육생 Proxmox 콘솔 계정 절차 (dist/console-access.sh)"
	@echo "make scenarios               장애 주입 시나리오 목록"
	@echo "make break LAB=1 SCENARIO=m01-01   장애 주입"
	@echo "make fix   LAB=1 SCENARIO=m01-01   복구"
	@echo "make console-setup           웹 콘솔 의존성 설치 (최초 1회)"
	@echo "make console                 웹 콘솔 실행 (기본 :8080)"
	@echo "make service                 systemd 서비스로 등록 (부팅 시 자동 기동)"
	@echo "make pack                    다른 서버로 옮길 tarball 생성"
	@echo "make users                   콘솔 계정 목록"

doctor:
	@$(PY) tools/preflight.py --lab $(LAB)

check:
	@$(PY) tools/validate-site.py --publish

gen: check
	@mkdir -p dist
	@$(PY) tools/gen-tfvars.py    --lab $(LAB)
	@$(PY) tools/gen-inventory.py --lab $(LAB) --stage $(STAGE)
	@$(PY) tools/gen-ssh-config.py --lab $(LAB) > dist/ssh-config-lab$(LAB)
	@echo "generated dist/ssh-config-lab$(LAB)"
	@$(MAKE) --no-print-directory docs

docs: modules appendix
	@mkdir -p dist
	@$(PY) tools/render-labmap.py
	@$(PY) tools/render-access.py
	@$(PY) tools/render-host-guard.py
	@$(PY) tools/render-opsvm.py --vmid $(VMID)

modules:
	@$(PY) tools/render-modules.py --lab $(LAB)

appendix:
	@$(PY) tools/render-appendix.py --lab $(LAB)

# 운영 서버를 관리망에 연결하는 절차. VMID 를 주면 명령이 그대로 복사된다.
opsvm:
	@$(PY) tools/render-opsvm.py --labs $(LABS) --vmid $(VMID) --net $(OPSNET)

# 운영 서버를 관리망에 연결한다 — 1회. 자기 VM 을 찾아 트렁크 NIC 을 붙이고
# VLAN 서브인터페이스까지 만든다. netplan 쓰는 부분에서만 sudo 를 쓴다.
mgmt-net:
	@$(PY) tools/setup-mgmt-net.py $(if $(LABS),--labs $(LABS),) $(if $(VMID),--vmid $(VMID),)

mgmt-net-dry:
	@$(PY) tools/setup-mgmt-net.py --dry-run $(if $(LABS),--labs $(LABS),)

# 교육생 점프 계정 — 셸 없는 ProxyJump 전용. 콘솔에 등록된 키에서 만든다.
jumpaccess:
	@$(PY) tools/gen-jumpaccess.py $(if $(LAB),--lab $(LAB),)

# 교육생 Proxmox 콘솔 계정 — 자기 랩 VM 화면만 열 수 있다.
# SSH 가 죽었을 때의 최후 경로(M0 실습 5)를 성립시킨다.
consoleaccess:
	@$(PY) tools/gen-console-access.py $(if $(LAB),--lab $(LAB),)

# 관리망 브리지 — **최초 1회**. 랩을 지워도 이 브리지는 남는다 (운영 서버 NIC 이 꽂혀 있다).
mgmt:
	@$(PY) tools/gen-mgmt.py --labs $(LABS)
	cd infra/terraform/envs/mgmt && $(PVE) terraform init -input=false
	cd infra/terraform/envs/mgmt && $(PVE) terraform apply

scenarios:
	@sed -n "/^| \`/p" scenarios/README.md

ipam:
	@$(PY) tools/show-ipam.py --lab $(LAB)

deploy:
	cd infra/terraform/envs/lab$(LAB) && $(PVE) terraform init -input=false
	cd infra/terraform/envs/lab$(LAB) && $(PVE) terraform apply

config:
	cd infra/ansible && $(APB) -i inventory/lab$(LAB) playbooks/site.yml -e lab_stage=$(STAGE)

verify:
	cd infra/ansible && $(APB) -i inventory/lab$(LAB) playbooks/verify.yml -e lab_stage=$(STAGE)

reset:
	cd infra/ansible && $(APB) -i inventory/lab$(LAB) playbooks/reset.yml -e lab_stage=$(STAGE)

break:
	cd infra/ansible && $(APB) -i inventory/lab$(LAB) ../../scenarios/$(SCENARIO).yml -e scenario_action=break

fix:
	cd infra/ansible && $(APB) -i inventory/lab$(LAB) ../../scenarios/$(SCENARIO).yml -e scenario_action=fix

PORT ?= 8080

console-setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r console/requirements.txt
	$(VENV)/bin/ansible-galaxy collection install -r infra/ansible/requirements.yml
	@$(PY) tools/console-user.py list >/dev/null 2>&1 || true
	@echo "설치 완료. 콘솔을 띄운 뒤 admin/admin 으로 로그인 → 비밀번호 변경 → [연결 설정] 확인"

users:
	@$(PY) tools/console-user.py list

service:
	@./install.sh --service --no-apt

pack:
	@./tools/pack.sh

console:
	@test -x $(VENV)/bin/uvicorn || { echo "먼저 'make console-setup' 을 실행할 것"; exit 1; }
	$(VENV)/bin/uvicorn app:app --app-dir console --host 0.0.0.0 --port $(PORT)

# 생성물만 지운다. var/ (계정 DB · 진행 상태) 는 건드리지 않는다.
clean:
	rm -rf dist infra/ansible/inventory/lab* infra/terraform/envs/*/lab.auto.tfvars.json
	@echo "envs/mgmt 는 지우지 않는다 — 관리망 브리지의 tfstate 다"
