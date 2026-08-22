# =============================================================================
#  관리망(OOB) 브리지 — 전 랩 공용.  **한 번만 apply 한다.**
# =============================================================================
#  이 파일과 mgmt.auto.tfvars.json 은 tools/gen-mgmt.py 가 envs/mgmt/ 로 생성한다.
#  직접 수정하지 말 것 — 원본은 infra/terraform/envs/_mgmt/main.tf 다.
#
#  왜 랩 자원이 아닌가
#    운영 서버(Terraform·Ansible·콘솔)는 모든 랩의 관리망에 발을 걸쳐야 한다.
#    브리지를 랩마다 두면 랩을 만들 때마다 운영 서버에 NIC 을 붙여야 하고,
#    랩을 지우면 `terraform destroy` 가 그 NIC 이 꽂힌 브리지를 지우려 든다.
#
#    그래서 브리지는 **하나**만 두고 랩을 VLAN 으로 나눈다.
#      · 랩 노드 net0  → tag = vlan_base + lab_id   (Terraform 이 랩마다 붙인다)
#      · 운영 서버 net → 태그 없음 = 트렁크          (한 번 붙이고 끝)
#    랩이 늘어도 줄어도 Proxmox 도 VM 하드웨어도 건드리지 않는다.
#
#  격리
#    태깅은 브리지가 한다. 게스트는 untagged 프레임만 보므로 다른 VLAN 을 주입할 수 없다.
#    물리 NIC 을 붙이지 않으므로 이 L2 는 호스트 밖으로 나가지 않는다.
#
#  자격 증명은 랩과 동일하게 환경변수로 넘긴다:
#    export PROXMOX_VE_API_TOKEN='terraform@pve!lab=xxxxxxxx-...'

terraform {
  required_version = ">= 1.6"
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = ">= 0.66"
    }
  }
}

provider "proxmox" {
  endpoint = var.pve_endpoint
  insecure = var.pve_insecure
  # SSH 블록을 두지 않는 이유는 랩 쪽 main.tf 와 같다 (headless 실행).
}

variable "pve_endpoint" { type = string }
variable "pve_node"     { type = string }
variable "pve_insecure" {
  type    = bool
  default = true
}

variable "mgmt_bridge" { type = string }
variable "comment"     { type = string }
variable "vlans" {
  description = "이 브리지에서 쓰는 랩별 관리 VLAN (문서·출력용)"
  type        = list(object({ lab_id = number, vlan = number, cidr = string }))
}

resource "proxmox_virtual_environment_network_linux_bridge" "mgmt" {
  node_name = var.pve_node
  name      = var.mgmt_bridge
  comment   = var.comment
  autostart = true

  # 랩별 VLAN 을 태그로 나누기 위해 필요하다. 이게 없으면 tag 가 무시된다.
  vlan_aware = true

  # ports 미지정 = 물리 NIC 미연결 (의도된 격리)
}

output "mgmt_bridge" { value = var.mgmt_bridge }

output "vlans" {
  description = "랩 -> 관리 VLAN"
  value       = { for v in var.vlans : "lab${v.lab_id}" => "VLAN ${v.vlan} (${v.cidr})" }
}

output "next_steps" {
  value = <<-EOT
    1) 운영 서버에 트렁크 NIC 을 붙인다 (Proxmox 호스트에서 1회) : dist/ops-server.md
    2) 운영 서버 안에서 VLAN 서브인터페이스를 만든다             : dist/ops-server.md
    3) 랩 배포                                                  : make gen LAB=1 && make deploy LAB=1
  EOT
}
