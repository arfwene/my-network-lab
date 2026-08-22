#!/bin/bash
# M4 검증 — OSPF 인접이 서고 이중 경로가 동작하는가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M4 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m4
