#!/bin/bash
# M6 검증 — 전 구간 dual-stack 이 동작하는가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M6 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m6
