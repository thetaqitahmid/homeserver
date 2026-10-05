terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.78.2"
    }
  }
}

provider "proxmox" {
  endpoint  = var.pm_api_url
  api_token = var.pm_api_token
  insecure  = var.pm_insecure

  ssh {
    agent       = false
    username    = var.pm_user
    private_key = file(var.pm_ssh_private_key_path)
  }
}

data "local_file" "ssh_public_key" {
  filename = var.pm_ssh_public_key_path
}

resource "proxmox_virtual_environment_vm" "ubuntu_vm" {
  for_each = { for vm in var.vms : vm.name => vm }

  name      = each.value.name
  node_name = each.value.node_name
  vm_id     = each.value.vm_id

  stop_on_destroy = true
  keyboard_layout = "fi"

  initialization {
    ip_config {
      ipv4 {
        address = each.value.ip_address
        gateway = each.value.gateway
      }
    }

    dns {
      servers = each.value.dns_servers
    }

    datastore_id = "local"

    user_account {
      username = var.vm_user_name
      password = var.vm_user_password
      keys     = [trimspace(data.local_file.ssh_public_key.content)]
    }
  }

  cpu {
    cores   = each.value.cores
    sockets = 1
  }

  memory {
    dedicated = each.value.memory
  }

  disk {
    datastore_id = "local"
    file_id      = proxmox_virtual_environment_download_file.ubuntu_cloud_image[each.value.node_name].id
    interface    = "virtio0"
    iothread     = true
    discard      = "on"
    size         = each.value.disk_size
  }

  network_device {
    bridge = "vmbr0"
  }
  depends_on = [proxmox_virtual_environment_download_file.ubuntu_cloud_image]

  lifecycle {
    prevent_destroy = true
  }
}

resource "proxmox_virtual_environment_download_file" "ubuntu_cloud_image" {
  for_each     = toset(var.nodes)
  content_type = "iso"
  datastore_id = "local"
  node_name    = each.key
  overwrite    = false

  file_name = "noble-server-cloudimg-amd64-${each.key}.img"
  url       = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

data "proxmox_virtual_environment_hosts" "hosts" {
  for_each  = toset(var.nodes)
  node_name = each.key

  depends_on = [proxmox_virtual_environment_vm.ubuntu_vm]
}
