#!/bin/bash
# M7 검증 — 랩 전 구간이 정상인가 (캡처 실습의 전제)
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M7 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m7
