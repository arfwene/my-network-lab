#!/bin/bash
# M7 검증 — 서비스 포트가 열려 있고 세션이 정상 수립되는가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M7 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m7
