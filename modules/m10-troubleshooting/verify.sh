#!/bin/bash
# M10 검증 — 랩 전체(M1~M9)가 설계대로 동작하는가
#   캡스톤은 이 상태에서 출발한다. 여기서 실패가 있으면 시험을 시작하지 말 것.
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
cd infra/ansible
echo "== M10 검증 (lab $LAB) =="
ansible-playbook -i "inventory/lab$LAB" playbooks/verify.yml -e lab_stage=m10
