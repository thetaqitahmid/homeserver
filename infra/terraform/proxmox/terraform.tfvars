# Proxmox configuration               =
pm_ssh_public_key_path      = "/home/taqi/.ssh/homeserver.pub"
pm_ssh_private_key_path     = "/home/taqi/.ssh/homeserver"

vms = [
  # {
  #   name        = "vm6"
  #   node_name   = "homeserver1"
  #   vm_id       = 105
  #   ip_address  = "192.168.1.151/24"
  #   gateway     = "192.168.1.1"
  #   dns_servers = ["1.1.1.1"]
  #   cores       = 2
  #   memory      = 4096
  #   disk_size   = 20
  # },
  # {
  #   name        = "vm7"
  #   node_name   = "homeserver2"
  #   vm_id       = 205
  #   ip_address  = "192.168.1.161/24"
  #   gateway     = "192.168.1.1"
  #   dns_servers = ["1.1.1.1"]
  #   cores       = 2
  #   memory      = 4096
  #   disk_size   = 20
  # },

  {
    name        = "vm8"
    node_name   = "homeserver3"
    vm_id       = 301
    ip_address  = "192.168.1.172/24"
    gateway     = "192.168.1.1"
    dns_servers = ["1.1.1.1"]
    cores       = 2
    memory      = 4096
    disk_size   = 50
  },
  {
    name        = "vm9"
    node_name   = "homeserver3"
    vm_id       = 302
    ip_address  = "192.168.1.173/24"
    gateway     = "192.168.1.1"
    dns_servers = ["1.1.1.1"]
    cores       = 2
    memory      = 4096
    disk_size   = 50
  },
  {
    name        = "vm10"
    node_name   = "homeserver3"
    vm_id       = 303
    ip_address  = "192.168.1.174/24"
    gateway     = "192.168.1.1"
    dns_servers = ["1.1.1.1"]
    cores       = 2
    memory      = 2048
    disk_size   = 20
  },
  {
    name        = "vm11"
    node_name   = "homeserver3"
    vm_id       = 304
    ip_address  = "192.168.1.175/24"
    gateway     = "192.168.1.1"
    dns_servers = ["1.1.1.1"]
    cores       = 2
    memory      = 4096
    disk_size   = 20
  }
]

nodes = ["homeserver1", "homeserver2", "homeserver3"]