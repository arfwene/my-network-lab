#!/bin/bash
# M2 검증 — VLAN 으로 갈린 두 대역이 라우터를 거쳐 통하는가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M2 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m2
