# =============================================================================
#  랩 1세트 = 격리된 브리지 N개 + VM 13대
#  브리지는 Proxmox 호스트 전역 자원이므로 랩마다 vmbr{lab_id}{id} 로 분리한다.
# =============================================================================

# --- 링크(=랜선) ------------------------------------------------------------
# 물리 NIC 을 붙이지 않는다. 붙이는 순간 랩 트래픽이 사내망으로 샌다.
resource "proxmox_virtual_environment_network_linux_bridge" "link" {
  for_each = { for b in var.bridges : b.name => b }

  node_name = var.pve_node
  name      = each.value.name
  comment   = each.value.comment
  autostart = true
  # ports 미지정 = 물리 NIC 미연결 (의도된 격리)
}

# --- 노드 -------------------------------------------------------------------
resource "proxmox_virtual_environment_vm" "node" {
  for_each = { for n in var.nodes : n.name => n }

  node_name   = var.pve_node
  vm_id       = each.value.vmid
  name        = each.value.vm_name
  description = "[lab${var.lab_id}] ${each.value.role} — ${each.value.desc}"
  tags        = ["my-network-lab", "lab${var.lab_id}", each.value.role]

  clone {
    vm_id = var.template_vmid
    full  = false # linked clone — 디스크 사용량을 크게 줄인다
    # 여러 대를 한꺼번에 복제하면 pveproxy 가 연결을 끊는 일이 있다
    # (HTTP 596 Broken pipe). 한 번 더 시도하면 대개 넘어간다.
    # 동시 실행 자체는 console/jobs.py 의 TF_PARALLELISM 이 낮춰 둔다.
    retries = 3
  }

  agent { enabled = true }
  cpu {
    cores = each.value.cores
    type  = "host"
  }
  memory { dedicated = each.value.memory }

  # net0 = 관리망, net1.. = 랩 링크. 순서가 곧 게스트 안의 인터페이스 순서다.
  dynamic "network_device" {
    for_each = each.value.nics
    content {
      bridge      = network_device.value.bridge
      mac_address = network_device.value.mac
      model       = "virtio"
      firewall    = false
      # 관리망만 태그가 있다. 랩 링크는 null → 태그 없음.
      # 태깅을 브리지가 하므로 게스트 안에서는 랩마다 설정이 똑같다.
      vlan_id = network_device.value.vlan_id
    }
  }

  # cloud-init 은 관리망(net0)만 설정한다.
  # 랩 인터페이스는 Ansible 이 netplan 으로 설정한다 — 그래야 AWS 로 그대로 옮겨진다.
  initialization {
    datastore_id = var.datastore_id
    ip_config {
      ipv4 {
        address = "${each.value.mgmt_ip}/${each.value.mgmt_prefixlen}"
        gateway = var.mgmt_gateway
      }
    }
    user_account {
      username = var.lab_user
      keys     = var.ssh_public_keys
      # 콘솔(화면) 접속용. SSH 는 키로만 받는다 — sshd 설정이 비밀번호를 막는다.
      # 비어 있으면 비밀번호 없이 만들어져 콘솔로 들어갈 수 없다.
      password = var.lab_password
    }
  }

  # network_device 를 ignore_changes 에 두지 않는다.
  #   전에는 "실습 중 임시 재배선을 되돌리지 않게" 두었는데, 이 저장소에서
  #   Proxmox 쪽 배선을 건드리는 것은 아무것도 없다 — 장애 주입도 게스트 안에서
  #   `ip link set down` 을 할 뿐이다. 반면 이걸 두면 **설계에 포트를 더해도
  #   이미 있는 랩에는 영영 안 붙는다.** M5 가 sw1·r3 에 트렁크를 하나씩 더
  #   다는데, 랩을 지웠다 다시 만들지 않고는 방법이 없어진다.
  #   설계가 진실이고, 손으로 바꾼 배선은 다음 [랩 생성] 에 원복된다.

  depends_on = [proxmox_virtual_environment_network_linux_bridge.link]
}
