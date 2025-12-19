# Bisect Bot Infrastructure - Hetzner Cloud
# ==========================================
#
# This Terraform configuration provisions all Hetzner infrastructure.
#
# Usage:
#   cd infrastructure/terraform
#   terraform init
#   terraform apply -var-file=../secrets.yml

terraform {
  required_version = ">= 1.0.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.45"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
  }
}

# -----------------------------------------------------------------------------
# Variables (loaded from secrets.yml)
# -----------------------------------------------------------------------------
variable "hcloud_token" {
  description = "Hetzner Cloud API token"
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "SSH public key for server access"
  type        = string
}

variable "domain" {
  description = "Domain name for the application"
  type        = string
}

variable "admin_email" {
  description = "Admin email for SSL certificates"
  type        = string
  default     = "admin@example.com"
}

variable "server_type" {
  description = "Hetzner server type (cx22, cx32, cx42)"
  type        = string
  default     = "cx32"
}

variable "location" {
  description = "Hetzner datacenter (fsn1, nbg1, hel1)"
  type        = string
  default     = "fsn1"
}

variable "environment" {
  description = "Environment name (prod, staging, dev)"
  type        = string
  default     = "prod"
}

# Database variables (passed through to Ansible inventory)
variable "database_url" {
  type      = string
  sensitive = true
}

variable "database_url_direct" {
  type      = string
  sensitive = true
  default   = ""
}

# GitHub variables (passed through to Ansible inventory)
variable "github_app_id" {
  type = string
}

variable "github_app_slug" {
  type = string
}

variable "github_client_id" {
  type = string
}

variable "github_client_secret" {
  type      = string
  sensitive = true
}

variable "github_webhook_secret" {
  type      = string
  sensitive = true
}

variable "github_private_key" {
  type      = string
  sensitive = true
}

# Application secrets (passed through to Ansible inventory)
variable "secret_key" {
  type      = string
  sensitive = true
}

variable "encryption_key" {
  type      = string
  sensitive = true
}

# Optional settings
variable "worker_replicas" {
  type    = number
  default = 2
}

variable "bisect_timeout_seconds" {
  type    = number
  default = 1800
}

variable "autoscale_min_workers" {
  type    = number
  default = 2
}

variable "autoscale_max_workers" {
  type    = number
  default = 10
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------
provider "hcloud" {
  token = var.hcloud_token
}

# -----------------------------------------------------------------------------
# SSH Key
# -----------------------------------------------------------------------------
resource "hcloud_ssh_key" "deploy" {
  name       = "bisect-bot-${var.environment}"
  public_key = var.ssh_public_key

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }
}

# -----------------------------------------------------------------------------
# Firewall
# -----------------------------------------------------------------------------
resource "hcloud_firewall" "web" {
  name = "bisect-bot-${var.environment}"

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  # SSH
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # HTTP
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # HTTPS
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # Outbound
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }
}

# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------
resource "hcloud_server" "app" {
  name        = "bisect-bot-${var.environment}"
  server_type = var.server_type
  image       = "ubuntu-24.04"
  location    = var.location
  ssh_keys    = [hcloud_ssh_key.deploy.id]

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }

  firewall_ids = [hcloud_firewall.web.id]

  # Cloud-init for initial bootstrapping
  user_data = <<-EOF
    #cloud-config
    package_update: true
    package_upgrade: true
    packages:
      - curl
      - git
      - python3
      - python3-pip

    runcmd:
      # Install Docker
      - curl -fsSL https://get.docker.com | sh
      - systemctl enable docker
      - systemctl start docker

      # Create deploy user
      - useradd -m -s /bin/bash -G docker,sudo deploy
      - mkdir -p /home/deploy/.ssh
      - cp /root/.ssh/authorized_keys /home/deploy/.ssh/
      - chown -R deploy:deploy /home/deploy/.ssh
      - chmod 700 /home/deploy/.ssh
      - chmod 600 /home/deploy/.ssh/authorized_keys
      - echo "deploy ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/deploy

      # Create app directory
      - mkdir -p /opt/bisect-bot
      - chown deploy:deploy /opt/bisect-bot
  EOF

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }
}

# -----------------------------------------------------------------------------
# Generate Ansible Inventory & Vault
# -----------------------------------------------------------------------------
resource "local_file" "ansible_inventory" {
  content = <<-EOT
    # Auto-generated by Terraform - do not edit manually
    [bisect_bot]
    app ansible_host=${hcloud_server.app.ipv4_address}

    [bisect_bot:vars]
    ansible_user=deploy
    ansible_python_interpreter=/usr/bin/python3
  EOT

  filename        = "${path.module}/../ansible/inventory/hosts"
  file_permission = "0644"
}

resource "local_file" "ansible_vault" {
  content = <<-EOT
    # Auto-generated by Terraform - do not edit manually
    # These are the secrets for Ansible deployment

    # Domain
    vault_domain: "${var.domain}"
    vault_base_url: "https://${var.domain}"
    vault_admin_email: "${var.admin_email}"

    # Database
    vault_database_url: "${var.database_url}"
    vault_database_url_direct: "${var.database_url_direct}"

    # GitHub App
    vault_github_app_id: "${var.github_app_id}"
    vault_github_app_slug: "${var.github_app_slug}"
    vault_github_client_id: "${var.github_client_id}"
    vault_github_client_secret: "${var.github_client_secret}"
    vault_github_webhook_secret: "${var.github_webhook_secret}"
    vault_github_private_key: |
    ${indent(4, var.github_private_key)}

    # Application
    vault_secret_key: "${var.secret_key}"
    vault_encryption_key: "${var.encryption_key}"

    # Settings
    worker_replicas: ${var.worker_replicas}
    bisect_timeout_seconds: ${var.bisect_timeout_seconds}
    autoscale_min_workers: ${var.autoscale_min_workers}
    autoscale_max_workers: ${var.autoscale_max_workers}
  EOT

  filename        = "${path.module}/../ansible/group_vars/vault.yml"
  file_permission = "0600"
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------
output "server_ip" {
  description = "Server public IP address"
  value       = hcloud_server.app.ipv4_address
}

output "server_ipv6" {
  description = "Server IPv6 address"
  value       = hcloud_server.app.ipv6_address
}

output "ssh_command" {
  description = "SSH command to connect to server"
  value       = "ssh deploy@${hcloud_server.app.ipv4_address}"
}

output "next_steps" {
  description = "Next steps after terraform apply"
  value       = <<-EOT

    ✅ Infrastructure created successfully!

    Server IP: ${hcloud_server.app.ipv4_address}

    NEXT STEPS:

    1. Update your DNS to point ${var.domain} to ${hcloud_server.app.ipv4_address}

    2. Wait 2-3 minutes for server initialization, then deploy:

       cd ../ansible
       ansible-playbook site.yml

    3. Verify deployment:
       curl https://${var.domain}/health

  EOT
}
