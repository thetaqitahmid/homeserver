[![status-badge](https://ci.tahmidcloud.com/api/badges/1/status.svg)](https://ci.tahmidcloud.com/repos/1)

# 🏠 Homeserver Setup Guide: Kubernetes on Proxmox

```
© 2025 Taqi Tahmid
```

## 🌟 Highlights

- Fully automated setup using Ansible
- Production-grade Kubernetes (K3s) cluster
- High-availability Proxmox configuration
- Popular self-hosted applications ready to deploy

## 📁 Repository Structure

- `ansible/` - Automated provisioning with Ansible playbooks. [Ansible Guide](ansible/README.md)
- `kubernetes/` - K8s manifests and Helm charts. [Kubernetes Guide](kubernetes/README.md)
- `docker/` - Legacy docker-compose files (Kubernetes preferred). [Docker Guide](docker/README.md)

## 🚀 Running Services

- ✨ AdGuard Home - Network-wide ad blocking
- 🐳 Private Docker Registry
- 🎬 Jellyfin Media Server
- 🌐 Portfolio Website
- 📷 Immich - Photo and Video Management
- 🗄️ PostgreSQL Database
- 📦 Pocketbase Backend
- 🍵 Gitea Git Server and Actions for CI/CD
- 📦 MinIO Object Storage - S3-compatible storage
- 🔑 LLDAP - Lightweight LDAP Authentication Server

### 📋 Coming Soon

- Nextcloud
- Authentik - OpenID Connect and OAuth2 server (will replace LLDAP)
- Monitoring Stack with Prometheus and Grafana

## 💻 Hardware Setup

- 2x Mini PCs with Intel N100 CPUs
- 16GB RAM each
- 500GB SSDs
- 2x 1 TB SATA SSDs
- 1Gbps networking
- Proxmox Cluster Configuration

## 🛠️ Installation Steps

### 1. Setting up Proxmox Infrastructure

#### Proxmox Base Installation

- Boot mini PCs from Proxmox USB drive
- Install on SSD and configure networking
- Set up cluster configuration
  > 📚 Reference: [Official Proxmox Installation Guide](https://pve.proxmox.com/wiki/Installation)

#### 3. Cloud Image Implementation

Cloud images provide:

- 🚀 Pre-configured, optimized disk images
- 📦 Minimal software footprint
- ⚡ Quick VM deployment
- 🔧 Cloud-init support for easy customization

These lightweight images are perfect for rapid virtual machine deployment in
your homelab environment.

#### Proxmox VM Disk Management

**Expanding VM Disk Size:**

1. Access Proxmox web interface
2. Select target VM
3. Navigate to Hardware tab
4. Choose disk to resize
5. Click Resize and enter new size (e.g., 50G)

**Post-resize VM Configuration:**

```bash
# Access VM and configure partitions
sudo fdisk /dev/sda
# Key commands:
# p - print partition table
# d - delete partition
# n - create new partition
# w - write changes
sudo mkfs.ext4 /dev/sdaX
```

#### Physical Disk Passthrough

Pass physical disks (e.g., NVME storage) to VMs:

```bash
# List disk IDs
lsblk |awk 'NR==1{print $0" DEVICE-ID(S)"}NR>1{dev=$1;printf $0" ";system("find /dev/disk/by-id -lname \"*"dev"\" -printf \" %p\"");print "";}'|grep -v -E 'part|lvm'

# Add disk to VM (example for VM ID 103)
qm set 103 -scsi2 /dev/disk/by-id/usb-WD_BLACK_SN770_1TB_012938055C4B-0:0
qm set 103 -scsi3 /dev/disk/by-id/wwn-0x5001b448c8488b85

# Verify configuration
grep 5C4B /etc/pve/qemu-server/103.conf
```

#### Proxmox How to Join a Cluster with Live VM

Proxmox does not allow nodes to join a cluster with guest VMs running or existing.
The workaround is to backup existing configuration files, join the cluster, and
then restore the files.

```bash
mkdir -p /root/guest-backup/qemu /root/guest-backup/lxc
cp /etc/pve/qemu-server/*.conf /root/guest-backup/qemu/
cp /etc/pve/lxc/*.conf /root/guest-backup/lxc/
rm /etc/pve/qemu-server/*.conf
rm /etc/pve/lxc/*.conf

# After that, join the node from GUI

# Finally restore the configuration files
cp /root/guest-backup/qemu/*.conf /etc/pve/qemu-server/
cp /root/guest-backup/lxc/*.conf /etc/pve/lxc/
```

> 📚 Reference: [Proxmox Disk Passthrough Guide](<https://pve.proxmox.com/wiki/Passthrough_Physical_Disk_to_Virtual_Machine_(VM)>)

### 2. Kubernetes Cluster Setup

#### K3s Cluster Configuration

Setting up a 4-node cluster (2 master + 2 worker):

**Master Node 1:**

```bash
curl -sfL https://get.k3s.io | sh -s - server --cluster-init --disable servicelb
```

**Master Node 2:**

```bash
export TOKEN=<token>
export MASTER1_IP=<ip>
curl -sfL https://get.k3s.io | sh -s - server --server https://${MASTER1_IP}:6443 --token ${TOKEN} --disable servicelb
```

**Worker Nodes:**

```bash
export TOKEN=<token>
export MASTER1_IP=<ip>
curl -sfL https://get.k3s.io | K3S_URL=https://${MASTER1_IP}:6443 K3S_TOKEN=${TOKEN} sh -
```

**Quick Test:**

```bash
# Deploy test nginx
kubectl create namespace nginx
kubectl create deployment nginx --image=nginx -n nginx
kubectl expose deployment nginx --port=80 --type=LoadBalancer -n nginx

# Cleanup after testing
kubectl delete namespace nginx
```

## Upgrade K3s cluster

Ref: https://github.com/k3s-io/k3s-upgrade

### Deploying the K3s Upgrade Controller

First deploy the k3s upgrade controller

```bash
kubectl apply -f https://raw.githubusercontent.com/rancher/system-upgrade-controller/master/manifests/system-upgrade-controller.yaml
```

Check that the controller is running. If not, check if the serviceaccount is
bound to the correct role.

```bash
kubectl get pods -n kube-system
kubectl create clusterrolebinding system-upgrade \
    --clusterrole=cluster-admin \
    --serviceaccount=system-upgrade:system-upgrade
```

### Label the nodes

First label the selected node with `k3s-upgrade=true` label. This is
needed to select the node for upgrade.

```bash
kubectl label node <node-name> k3s-upgrade=true
```

It is best practice to upgrade node one by one. Thus, the cluster will
still be operational during the upgrade. And, for any issues, it is possible
to rollback the upgrade.

### Create the upgrade plan

Then create the upgrade plan. The plan will be created in the `system-upgrade`
namespace. You can change the namespace by using the `--namespace` flag.

```bash
kubectl apply -f /home/taqi/homeserver/kubernetes/k3s-upgrade/plan.yaml
```

The plan will fitst try to cordon and drain the node. If it fails, check
the logs of the plan.

The longhorn CSI pods might node be drained. In that case, you can
cordon the node and drain it manually.
Ref: https://github.com/longhorn/longhorn/discussions/4102

```bash
kubectl drain vm4 --ignore-daemonsets \
    --delete-emptydir-data \
    --pod-selector='app!=csi-attacher,app!=csi-provisioner'
```

## 🤝 Contributing

Contributions welcome! Feel free to open issues or submit PRs.

## 📝 License

MIT License - feel free to use this as a template for your own homelab!
