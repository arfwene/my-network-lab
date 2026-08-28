# 이 변수들은 tools/gen-tfvars.py 가 design/*.yml 에서 생성한다.
# 손으로 수정하지 말 것 — 설계를 고치고 다시 생성한다 (PLAN 6.5 R1).

variable "lab_id" {
  description = "랩 번호 (1~9). 브리지 이름과 VMID 에 반영된다."
  type        = number
}

variable "lab_stage" {
  description = "이 시점까지의 모듈 단계 (m1~m11). 해당 단계에 등장하는 노드/링크만 만든다."
  type        = string
  default     = "m11"
}

variable "template_vmid" {
  description = "골든 템플릿 VMID (infra/template/build-golden-template.sh 로 생성)"
  type        = number
}

variable "mgmt_cidr"    { type = string }
variable "mgmt_gateway" { type = string }

variable "bridges" {
  description = "생성할 Linux 브리지 (물리 NIC 미연결 = 격리된 랜선)"
  type = list(object({
    name    = string
    comment = string
  }))
}

variable "nodes" {
  description = "생성할 VM"
  type = list(object({
    name           = string
    vm_name        = string
    vmid           = number
    cores          = number
    memory         = number
    disk           = number
    role           = string
    desc           = string
    mgmt_ip        = string
    mgmt_prefixlen = number
    mgmt_mac       = string
    nics = list(object({
      bridge  = string
      mac     = string
      purpose = string
      vlan_id = number # 관리망만 태그가 있다. 랩 링크는 null.
    }))
  }))
}

# ---- 환경 의존 값 (envs/*/terraform.tfvars 에서 지정) ----
# 콘솔 접속용 비밀번호. 파일에 두지 않는다 — 실행 시 TF_VAR_lab_password 로 들어온다.
variable "lab_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "pve_node"     { type = string }
variable "datastore_id" {
  type    = string
  default = "local-lvm"
}
variable "ssh_public_keys" {
  description = "교육생/관리자 SSH 공개키"
  type        = list(string)
}
variable "lab_user" {
  description = "랩 노드 로그인 계정"
  type        = string
  default     = "lab"
}
