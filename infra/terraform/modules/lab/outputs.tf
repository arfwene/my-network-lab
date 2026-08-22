output "lab_id" { value = var.lab_id }

output "nodes" {
  description = "노드 -> 관리 IP"
  value       = { for n in var.nodes : n.name => n.mgmt_ip }
}

output "bridges" {
  description = "생성된 브리지"
  value       = [for b in var.bridges : b.name]
}

output "next_steps" {
  value = <<-EOT
    1) 인벤토리 생성 : python3 tools/gen-inventory.py --lab ${var.lab_id}
    2) 설정 적용     : cd infra/ansible && ansible-playbook -i inventory/lab${var.lab_id} playbooks/site.yml -e lab_stage=${var.lab_stage}
    3) 접속 설정 배포 : python3 tools/gen-ssh-config.py --lab ${var.lab_id} > ssh-config-lab${var.lab_id}
  EOT
}
