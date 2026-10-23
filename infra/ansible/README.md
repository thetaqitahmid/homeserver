# Ansible Playbook for Proxmox VM Management

This Ansible playbook automates the creation, deletion, and configuration of
virtual machines (VMs) on a Proxmox server.

## Prerequisites

- Ansible installed on the local machine
- Ansible community.general.proxmox_kvm module
- Access to a Proxmox server with API access enabled
- Python `proxmoxer` library installed (`pip install proxmoxer`)

## Setup

1. Clone this repository:
    ```sh
    git clone https://github.com/TheTaqiTahmid/proxmox_ansible_automation
    ```

2. Update the `inventory` file with your Proxmox server details:
    ```yaml
    all:
      hosts:
        proxmox:
          ansible_host: your_proxmox_ip
          ansible_user: your_proxmox_user
          ansible_password: your_proxmox_password
    ```
    In the current example implementation in `inventories/hosts.yaml`, there are
    multiple groups depending on the types of hosts.

3. Add group-related variables to the group file under the `group_vars` directory
   and individual host-related variables to the files under the `host_vars`
  directory. Ansible will automatically pick up these variables.

4. Add the following secrets to the ansible-vault:
   - proxmox_api_token_id
   - proxmox_api_token
   - ansible_proxmox_user
   - ansible_vm_user
   - proxmox_user
   - ansible_ssh_private_key_file
   - ciuser
   - cipassword

  One can create the secret file using the following command:
  ```sh
  ansible-vault create secrets/vault.yml
  ```

  To encrypt and decrypt the file, use the following commands:
  ```sh
  ansible-vault encrypt secrets/vault.yml
  ansible-vault decrypt secrets/vault.yml
  ```
  The password for vault file can be stored in a file or can be provided during
  the encryption/decryption process. The password file location can be specified
  in the `ansible.cfg` file.

## Playbooks

### Create VM

To create the VMs, run the following command:
```sh
ansible-playbook playbooks/create-vms.yaml
```
The playbook can be run against specific Proxmox instance using:
```sh
ansible-playbook playbooks/create-vms.yaml --limit proxmox1
```

### Delete VM

To delete existing VMs, run the following command:
```sh
ansible-playbook playbooks/destroy-vms.yaml
```

Similarly the destory playbook can be run against specific Proxmox instance using:
```sh
ansible-playbook playbooks/destroy-vms.yaml --limit proxmox1
```

### Configure VM

To configure an existing VM, run the following command:
```sh
ansible-playbook playbooks/configure-vms.yaml
```

The configuration can be limited to individual VMs using limits:
```sh
ansible-playbook playbooks/configure-vms.yaml --limit vm6
```

## Variables

The playbooks use the following variables, which can be customized in the
`group_vars/proxmox.yml` file:

- `vm_id`: The ID of the VM
- `vm_name`: The name of the VM
- `vm_memory`: The amount of memory for the VM
- `vm_cores`: The number of CPU cores for the VM
- `vm_disk_size`: The size of the VM disk

## Immich backups

The `immich_backup` role configures encrypted Restic backups from dockerhost to the Hetzner Storage Box. The role does not initialize a repository or enable timers by default; dockerhost explicitly opts into timers in its host variables. See [deployment, credentials, testing, and recovery](../../docker/immich/restore.md) before running it.

```sh
ansible-playbook playbooks/configure-immich-backup.yaml --syntax-check
ansible-playbook playbooks/configure-immich-backup.yaml --limit dockerhost --ask-become-pass
```

The retired WireGuard role, playbook, and dedicated inventory settings have been removed. This does not stop or uninstall WireGuard on any host.

## Author

- Taqi Tahmid (mdtaqitahmid@gmail.com)
