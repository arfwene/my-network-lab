#!/bin/bash
# M5 검증 — 두 라우터가 하나의 게이트웨이로 동작하는가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M5 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m5
