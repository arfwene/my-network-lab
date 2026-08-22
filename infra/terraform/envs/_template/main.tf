# 랩 인스턴스 (모든 랩 공통).
# 이 파일과 lab.auto.tfvars.json 은 tools/gen-tfvars.py 가 각 envs/lab<N>/ 로 생성한다.
# 직접 수정하지 말 것 — 원본은 infra/terraform/envs/_template/main.tf 다.
#
# 자격 증명은 파일에 두지 않는다. 환경변수로 넘긴다:
#   export PROXMOX_VE_API_TOKEN='terraform@pve!lab=xxxxxxxx-...'
# (엔드포인트·노드명 등 비밀이 아닌 값은 config/site.yml 에서 자동으로 채워진다)

terraform {
  required_version = ">= 1.6"
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      # v1.0 에서 리소스 이름이 바뀐다고 예고돼 있다
      # (proxmox_virtual_environment_* -> proxmox_*). 올릴 때는 의도적으로 올린다.
      version = ">= 0.66, < 1.0"
    }
  }
}

provider "proxmox" {
  endpoint = var.pve_endpoint
  insecure = var.pve_insecure
  # api_token 은 환경변수 PROXMOX_VE_API_TOKEN 에서 읽는다.
  #
  # SSH 블록을 두지 않는다.
  #   이 랩이 만드는 자원(리눅스 브리지 · linked clone VM · cloud-init)은 전부 API 로 만든다.
  #   전에는 ssh { agent = true } 를 박아 뒀는데, 웹 콘솔을 systemd 서비스로 돌리면
  #   SSH_AUTH_SOCK 이 없어 agent 를 찾지 못한다. 필요할 때만 환경변수로 켠다:
  #     PROXMOX_VE_SSH_USERNAME=root
  #     PROXMOX_VE_SSH_PRIVATE_KEY="$(cat ~/.ssh/id_ed25519)"    또는  PROXMOX_VE_SSH_AGENT=true
  #   (서비스로 돌린다면 var/console.env 에 넣는다 — docs/DEPLOY.md 참고)
}

# --- gen-tfvars.py 가 config/site.yml 에서 채우는 접속 값 ---
variable "pve_endpoint"  { type = string }
variable "lab_user"      { type = string }
variable "pve_insecure" {
  type    = bool
  default = true
}
variable "pve_node"      { type = string }
variable "datastore_id"  { type = string }
variable "ssh_public_keys" { type = list(string) }

# 랩 노드 콘솔 접속용 비밀번호.
#   tfvars 에 넣지 않는다 (생성물은 재생성·복사된다).
#   실행 시 환경변수로만 들어온다:  TF_VAR_lab_password
#   콘솔과 make 타깃이 var/console.db 에서 읽어 주입한다.
variable "lab_password" {
  type      = string
  sensitive = true
  default   = ""
}

# --- gen-tfvars.py 가 채우는 값 ---
variable "lab_id"        { type = number }
variable "lab_stage"     { type = string }
variable "template_vmid" { type = number }
variable "mgmt_cidr"     { type = string }
variable "mgmt_gateway"  { type = string }
variable "bridges" {
  type = list(object({ name = string, comment = string }))
}
variable "nodes" {
  type = list(object({
    name = string, vm_name = string, vmid = number, cores = number,
    memory = number, disk = number, role = string, desc = string,
    mgmt_ip = string, mgmt_prefixlen = number, mgmt_mac = string,
    nics = list(object({ bridge = string, mac = string, purpose = string, vlan_id = number }))
  }))
}

module "lab" {
  source = "../../modules/lab"

  lab_id        = var.lab_id
  lab_stage     = var.lab_stage
  template_vmid = var.template_vmid
  mgmt_cidr     = var.mgmt_cidr
  mgmt_gateway  = var.mgmt_gateway
  bridges       = var.bridges
  nodes         = var.nodes

  pve_node        = var.pve_node
  datastore_id    = var.datastore_id
  ssh_public_keys = var.ssh_public_keys
  lab_user        = var.lab_user
  lab_password    = var.lab_password
}

output "nodes"      { value = module.lab.nodes }
output "next_steps" { value = module.lab.next_steps }
