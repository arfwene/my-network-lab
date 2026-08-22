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
