#!/bin/bash
# M0 검증 — 랩이 정상 기동되어 접속 가능한 상태인가
set -euo pipefail
cd "$(dirname "$0")/../../.."
LAB=${LAB:-1}
echo "== M0 검증 (lab $LAB) =="
cd infra/ansible
ansible -i "inventory/lab$LAB" all -m ping --one-line
echo
echo "모든 노드가 SUCCESS 면 통과. 실패한 노드는 Proxmox 콘솔로 확인할 것."
