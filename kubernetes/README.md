# Setup K3s Kubernetes Cluster

## MetalLB Load Balancer Setup

MetalLB is a load balancer implementation for bare metal Kubernetes clusters.
It provides a way to expose services externally by assigning them an IP address
from a pool of addresses. This is particularly useful for clusters that do not
have a cloud provider load balancer. In this setup, MetalLB is used to provide
a load balancer for the k3s cluster. The MetalLB configuration is applied
from a YAML file that defines the IP address pool and the configuration for
the load balancer.

```bash
# Install MetalLB
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.13.7/config/manifests/metallb-native.yaml

# Verify installation
kubectl get pods -n metallb-system

# Apply configuration
kubectl apply -f /home/taqi/homeserver/k3s-infra/metallb/metallbConfig.yaml
```

## Configure Kube-VIP for Load Balancing

> **Note:**
> This workflow is used to setup kube-vip after k3s is installed.
> For a new installation, refer to the kube-vip documentation.
> https://kube-vip.io/docs/usage/k3s/

Kube-VIP is used to provide a virtual IP address for the k3s cluster. This acts
as a load balancer for the controller nodes and provides a single IP address
for accessing the k3s API server. The kube-vip is deployed as a DaemonSet in the
`kube-system` namespace.

```bash
# Install kube-vip
source .env
helm repo add kube-vip https://kube-vip.github.io/helm-charts
helm repo update
helm upgrade --install kube-vip kube-vip/kube-vip \
  -f kube-vip/values.yaml \
  --namespace kube-system \
  --set config.address=$VIP_ADDRESS
```

After deploying kube-vip, we need to modify the startup script for the control
plane nodes to add the `tls-san` flag to the k3s server command. This is necessary
to ensure that the k3s server uses the virtual IP address for the API server.

```bash
sudo vim /etc/systemd/system/k3s.service
# The ExecStart line should look like this:
ExecStart=/usr/local/bin/k3s \
    server \
        '--cluster-init' \
        '--disable' \
        'servicelb' \
        '--tls-san' \
        '$VIP_ADDRESS'

# Then reload the systemd configuration and restart k3s
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

Finally,update the kubeconfig file to use the virtual IP address for the API
server.

## Configure Traefik Ingress Controller

The Traefik ingress controller is deployed along with K3s. To modify the
default values,

```bash
helm upgrade --install traefik traefik/traefik \
  -f traefik/traefik-values.yaml \
  -n kube-system \
  --set ingressRoute.dashboard.enabled=true \
  --set ingressRoute.dashboard.matchRule='Host(`dashboard.traefik`)' \
  --set ingressRoute.dashboard.entryPoints={websecure} \
  --set providers.kubernetesGateway.enabled=true \
  --set gateway.namespacePolicy=All
```

For security reason, the Traefik dashboard is removed after creation for now.

### Additional Ingress Controller for Internal Access

An additional ingress controller is deployed for internal access to services.
This ingress controller is used to access services that are not exposed to the
internet. I have used the `ingress-nginx` controller for this purpose.

The initial plan was to use the `traefik` ingress controller for both but due
to short circuit issues with the external `traefik` ingress controller, I have
switched to using `ingress-nginx` for internal access.

```bash
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace
```

The LoadBalancer service IP for the internal ingress controller is added to
the adGuard DNS server to resolve the internal services.

To utilize the internal ingress controller, add the following
`ingressClassName: nginx` under ingress spec.

# Configure Cert Manager for automating SSL certificate handling

Cert manager handles SSL certificate creation and renewal from Let's Encrypt.

```bash
helm repo add jetstack https://charts.jetstack.io --force-update
helm repo update

helm install \
  cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.15.3 \
  --set crds.enabled=true \
  --set prometheus.enabled=false \
  --set webhook.timeoutSeconds=4 \
```

Next, deploy the certificate Issuer. Issuers, and ClusterIssuers,
are Kubernetes resources that represent certificate authorities (CAs) that are
able to generate signed certificates by honoring certificate signing requests.
All cert-manager certificates require a referenced issuer that is in a ready
condition to attempt to honor the request.
[Ref](https://cert-manager.io/docs/concepts/issuer/).

The template for ClusterIssuer is in the cert-manager directory. A single
wildcard-cert will be created and used for all ingress subdomains. Create a new
certificate and cert in cert directory and copy the secret manually to all the
namespaces.

First add the DNS servers to the coreDNS config:

```bash
export KUBE_EDITOR=nvim
# Change the forward section with . 1.1.1.1 1.0.0.1
kubectl -n kube-system edit configmap coredns
```

Next, deploy the ClusterIssuer, WildcardCert, and secrets using helm chart.

```bash
source .env
helm install cert-handler cert-manager-config-helm-chart \
  --atomic --set secret.apiToken=$CLOUDFLARE_TOKEN \
  --set clusterIssuer.email=$EMAIL \
  --set wildcardCert.dnsNames[0]=$DNSNAME

# Copy the wildcard certificate to other namespaces
kubectl get secret wildcard-cert-secret --namespace=cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: <namespace>/' | kubectl apply -f -
```

If for some reason certificate secret `wildcard-cert-secret` is not generated,
the issue can be related to cloudflare API token is wrong, the token secret is
missing, the Issuer or ClusterIssuer is not ready etc.

Here are some troubleshoot commands to test:

```bash
kubectl get clusterissuer
kubectl describe clusterissuer
kubectl get certificate -n cert-manager
kubectl get certificateRequest -n cert-manager
kubectl describe challenges -n cert-manager
kubectl describe orders -n cert-manager
```

Alternatively, it is possible to generate service specific certs
in desired namespaces by deploying the Certificate resource in the namespace.

# Deploy Private Docker Registry (Deprecated)

> **Note:** The docker registry helm chart is deprecated in favor of gitea image
> registry.

Create a new namespace called docker-registry and deploy the private
docker-registry.

First create docker credentials with htpasswd:

```bash
htpasswd -cB registry-passwords USERNAME

kubectl create namespace docker-registry
kubectl create secret generic registry-credentials \
  --from-file=.secrets/registry-passwords \
  -n docker-registry
```

Next, deploy the docker registry with helm chart. In the PVC, the following
annotation is added to ensure that the PVC is not deleted when the helm
chart is deleted. This is useful to retain the images in the registry even
after the helm chart is deleted.

`helm.sh/resource-policy: "keep"`

```bash
source .env
helm install registry docker-registry-helm-chart/ \
  --set host=$DOCKER_REGISTRY_HOST \
  --set ingress.tls.host=$REGISTRY_HOST \
  --atomic
```

# Deploy Portfolio Website from Private Docker Registry

First, create the namespace and create a secret to access the private docker
registry.

```bash
kubectl create namespace my-portfolio

source .env
kubectl create secret docker-registry docker-registry-credentials \
  --docker-server="$DOCKER_REGISTRY_HOST" \
  --docker-username="$DOCKER_USER" \
  --docker-password="$DOCKER_PASSWORD" \
  -n my-portfolio

# use envsubst to substitute the environment variables in the manifest
envsubst < my-portfolio/portfolioManifest.yaml | \
  kubectl apply -n my-portfolio -f -
```

# Expose External Services via Traefik Ingress Controller

External services hosted outside the kubernetes cluster can be exposed using
the kubernetes traefik reverse proxy.

A nginx http server is deployed as a proxy that listens on port 80
and redirects requests to the proxmox local IP address. The server has an
associated clusterIP service which is exposed via ingress. The nginx proxy can
be configured to listen to other ports and forward traffic to other external
services running locally or remotely.

```bash
source .env
kubectl create namespace external-services
envsubst '${PROXMOX_IP} ${PROXMOX_HOST}' < external-service/proxmox.yaml | \
  kubectl apply -n external-services -f -
```

# Create Shared NFS Storage for Plex and Jellyfin

A 1TB NVME SSD is mounted to one of the original homelab VMs. This serves as an
NFS mount for all k3s nodes to use as shared storage for plex and jellyfin
containers.

## On the host VM:

```bash
sudo apt update
sudo apt install nfs-kernel-server
sudo chown nobody:nogroup /media/flexdrive

# Configure mount on /etc/fstab to persist across reboot
sudo vim /etc/fstab
# Add the following line. Change the filsystem if other than ntfs
# /dev/sdb2    /media/flexdrive    ntfs    defaults    0    2

# Configure NFS exports by editing the NFS exports file
sudo vim /etc/exports
# Add the following line to the file
# /media/flexdrive 192.168.1.113/24(rw,sync,no_subtree_check,no_root_squash)

# Apply the exports config
sudo exportfs -ra

# Start and enable NFS Server
sudo systemctl start nfs-kernel-server
sudo systemctl enable nfs-kernel-server
```

## On all the K3s VMs:

```
sudo apt install nfs-common
sudo mkdir /mnt/media
sudo mount 192.168.1.113:/media/flexdrive /mnt/media
# And test if the contents are visible
# After that unmount with the following command as mounting will be taken care
# by k8s
sudo umount /mnt/media
```

# Deploy Jellyfin Container in K3s

Jellyfin is a media server that can be used to organize, play, and stream
audio and video files. The Jellyfin container is deployed in the k3s cluster
using the NFS shared storage for media files. Due to segregated nature of the
media manifest files, it has not been helm charted.

```bash
source .env
kubectl create namespace media
kubectl get secret wildcard-cert-secret --namespace=cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: media/' | kubectl apply -f -

# Create a new storageclass called manual to not use longhorn storageclass
kubectl apply -f media/storageclass-nfs.yaml

# Create NFS PV and PVC
envsubst < media/pv.yaml | kubectl apply -n media -f -
kubectl apply -f media/pvc.yaml -n media

# Deploy Jellyfin
envsubst < media/jellyfin-deploy.yaml | kubectl apply -n media -f -
```

## Enable LDAP Authentication

In order to enable LDAP authentication for Jellyfin, the LDAP
plugin must be installed. The LDAP plugin is not included in the
Jellyfin helm chart. The plugin must be installed manually by
from the GUI.

1. Go to the Jellyfin web UI and login as admin.
2. Go to the Plugins section and click on the "Catalog" tab.
3. Search for the "LDAP" plugin and click on the "Install" button.
4. After the plugin is installed, go to the "Dashboard" section and click on
   the "LDAP" tab.
5. Configure the LDAP settings as follows:
   - LDAP Server:
     - Host: 192.168.1.144
     - Port: 3890
     - LDAP Bind User: UID=admin,OU=people,DC=homelab,DC=local
     - Bind Password:
     - LDAP Base DN for searches: DC=homelab,DC=local
     - LDAP Search Filter: (memberOf=CN=jellyfin_users,OU=groups,DC=homelab,DC=local)
     - LDAP Search Attribute: uid, cn, mail, displayName
     - LDAP Uid Attribute: uid
     - LDAP Username Attribute: CN
     - LDAP Password Attribute: userPassword
     - LDAP Admin Bind DN: dc=homelab,dc=local
     - LDAP Admin Filter: (memberOf=CN=jellyfin_users,OU=groups,DC=homelab,DC=local)

## Transfer media files from one PVC to another (Optional)

To transfer media files from one PVC to another, create a temporary pod to copy
files from one PVC to another. The following command will create a temporary
pod in the media namespace to copy files from one PVC to another.

```bash
# Create a temporary pod to copy files from one PVC to another
k apply -f temp-deploy.yaml -n media
# Copy files from one PVC to another
kubectl exec -it temp-pod -n media -- bash
cp -r /mnt/source/* /mnt/destination/
```

# Create Storage Solution

Longhorn is a distributed block storage solution for Kubernetes that is built
using containers. It provides a simple and efficient way to manage persistent
volumes. Longhorn is deployed in the k3s cluster to provide storage for the
containers. For security reasons, the longhorn UI is not exposed outside the
network. It is accessible locally via port-forwarding or loadbalancer.

In order to use Longhorn, the storage disk must be formatted and mounted on
each VM. The following commands format the disk and mount it on /mnt/longhorn
directory. For deployment, the longhorn helm chart is used to install longhorn
in the longhorn-system namespace.

```bash
# On each VM
sudo mkfs.ext4 /dev/sda4
sudo mkdir /mnt/longhorn
sudo mount /dev/sda4 /mnt/longhorn

# Add entry to /etc/fstab to persist across reboot
echo "/dev/sda4 /mnt/longhorn ext4 defaults 0 2" | sudo tee -a /etc/fstab
```

Deploy the longhorn helm chart.
Ref: https://github.com/longhorn/charts/tree/v1.8.x/charts/longhorn

```bash
helm repo add longhorn https://charts.longhorn.io
helm repo update

kubectl create namespace longhorn-system
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system  \
  -f values.yaml

kubectl -n longhorn-system get pods

# Access longhorn UI
kubectl -n longhorn-system port-forward svc/longhorn-frontend 8080:80
# Or make it permanent by setting the longhorn-frontend service type to
# LoadBalancer.
kubectl -n longhorn-system edit svc longhorn-frontend
```

## If the /mnt/longhorn is not shown

Ref: https://longhorn.io/docs/1.8.1/nodes-and-volumes/nodes/default-disk-and-node-config/

kubectl -n longhorn-system get nodes.longhorn.io
kubectl -n longhorn-system edit nodes.longhorn.io <node-name>

````
Add the following block under disks for all nodes:

```bash
    custom-disk-mnt-longhorn:           # New disk for /mnt/longhorn
      allowScheduling: true
      diskDriver: ""
      diskType: filesystem
      evictionRequested: false
      path: /mnt/longhorn                # Specify the new mount path
      storageReserved: 0                 # Adjust storageReserved if needed
      tags: []
````

## Setting the number of replicas

To set the number of replicas, edit the longhorn-storageclass configmap and
set the numberOfReplicas to the desired number.

```bash
# Set number of replica count to 1
kubectl edit configmap -n longhorn-system longhorn-storageclass
  set the numberOfReplicas: "1"
```

## Multiple storage classes for different replica counts with Longhorn

To create multiple storage classes with different replica counts, create
multiple storage class yaml files with different replica counts and apply
them. The storage class name must be different for each storage class.

```bash
# Create a new storage class with 2 replicas
kubectl apply -n longhorn-system -f longhorn-storageclass-2-replica.yaml
# Create a new storage class with 3 replicas
kubectl apply -n longhorn-system -f longhorn-storageclass-3-replica.yaml
```

# Configure AdGuard Adblocker

AdGuard is deployed in the K3S cluster for network ad protection.
A loadbalancer service is used for DNS resolution and clusterIP
and ingress for the WEBUI.

The adguard initial admin port is 3000 which is bound to the loadbalancer IP
from the local network. The AdGuard UI is accessible from the ingress
domain on the internet.

```bash
kubectl create namespace adguard
kubectl get secret wildcard-cert-secret --namespace=cert -o yaml \
  | sed 's/namespace: cert/namespace: adguard/' | kubectl apply -f -

source .env
helm upgrade --install adguard \
  --namespace adguard \
  --create-namespace \
  --atomic \
  -f adguard-helm-chart/values.yaml \
  --set ingress.hosts[0].host=$ADGUARD_HOST \
  --set host=$ADGUARD_HOST \
  --atomic adguard-helm-chart
```

# Pocketbase Database and Authentication Backend

Pocketbase serves as the database and authentication backend for
various side projects.

```bash
# Create namespace and copy the wildcard cert secret
kubectl create namespace pocketbase
kubectl get secret wildcard-cert-secret --namespace=cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: pocketbase/' | kubectl apply -f -

# Deploy pocketbase using helm chart
helm install pocketbase \
  --set ingress.host=$POCKETBASE_HOST \
  --set ingress.tls.hosts[0]=$DNSNAME \
  --atomic pocketbase-helm-chart
```

It may be required to create initial user and password for the superuser.
To do that, exec into the pod and run the following command:

```bash
pocketbase superuser create email password
```

# qBittorrent with Wireguard

qBittorrent is deployed with wireguard to route traffic through a VPN tunnel.
The following packages must be installed on each node:

```bash
# On each k3s node
sudo apt update
sudo apt install -y wireguard wireguard-tools linux-headers-$(uname -r)
```

The qBittorrent is deplyoyed via helm chart. The qBittorrent deployment uses
the `media-nfs-pv` common NFS PVC for downloads. The helm chart contains both
qBittorrent and wireguard. For security, qBittorrent is not exposed outside the
network via ingress. It is accessible locally via loadbalancer IP address.

```bash
source .env
helm upgrade --install \
  qbittorrent qbittorrent-helm-chart/ \
  -n media \
  --set ingress.host=$QBITTORRENT_HOST \
  --set wireguard.address=$WIREGUARD_IP \
  --set wireguard.peerPublicKey=$WIREGUARD_PEER_PUBLIC_KEY \
  --set wireguard.presharedKey=$WIREGUARD_PRESHARED_KEY \
  --set wireguard.endpoint=$WIREGUARD_ENDPOINT \
  --set wireguard.dns=$WIREGUARD_DNS \
  --set wireguard.privateKey=$WIREGUARD_PRIVATE_KEY \
  --atomic
```

After deployment, verify qBittorrent is accessible on the loadbalancer IP and
port. Login to the qBittorrent UI with default credentials from the deployment
log. Change the user settings under settings/WebUI. Configure the network
interface (wg0) in settings/Advanced and set download/upload speeds in
settings/speed.

Also verify the VPM is working by executing the following command on the
qBittorrent pod:

```bash
curl ipinfo.io
```

# PostgreSQL Database (Deprecated)

`Bitnami PostgreSQL helm chart is removed in favor of CloudNativePG operator.`
The PostgreSQL database uses the bitnami postgres helm chart with one primary
and one replica statefulset, totaling 2 postgres pods.

```bash
# Add the Bitnami repo if not already added
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install PostgreSQL with these values
source .env
helm install my-postgres \
  bitnami/postgresql -f values.yaml \
  --set global.postgresql.auth.username=$POSTGRES_USER \
  --set global.postgresql.auth.password=$POSTGRES_PASSWORD \
  --set global.postgresql.auth.postgresPassword=$POSTGRES_PASSWORD \
  --atomic \
  -n postgres
```

## Connect to the Database

```bash
psql -U $POSTGRES_USER -d postgres --host 192.168.1.145 -p 5432
```

## Backup and Restore PostgreSQL Database

```bash
# To backup§
# Dump format is compressed and allows parallel restore
pg_dump -U $POSTGRES_USER -h 192.168.1.145 -p 5432 -F c \
  -f db_backup.dump postgres

# To restore
pg_restore -U $POSTGRES_USER -h 192.168.1.145 -p 5432 -d postgres db_backup.dump
```

## pgAdmin

pgAdmin provides GUI support for PostgreSQL database management. Deploy using
pgadmin.yaml manifest under postgres directory. The environment variables are
substituted from the .env file.

```bash
source .env
envsubst < postgres/pgadmin.yaml | kubectl apply -n postgres -f -
```

# Gitea Git Server

Reference:
https://gitea.com/gitea/helm-chart/
https://docs.gitea.com/installation/database-prep

Gitea is a self-hosted Git service that is deployed in the k3s cluster. The
Gitea deployment uses existing posrgres database for data storage. The Gitea
service is exposed via ingress and is accessible from the internet.

Configure a new user, database, and schema for Gitea in the postgres database.

```bash
CREATE ROLE gitea WITH LOGIN PASSWORD 'dummypassword';

CREATE DATABASE giteadb
WITH OWNER gitea
TEMPLATE template0
ENCODING UTF8
LC_COLLATE 'en_US.UTF-8'
LC_CTYPE 'en_US.UTF-8';

\c giteadb
CREATE SCHEMA gitea;
GRANT USAGE ON SCHEMA gitea TO gitea;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA gitea TO gitea;
ALTER SCHEMA gitea OWNER TO gitea;
```

Next, deploy the Gitea helm chart with the following values:

```bash
source .env
kubectl create namespace gitea
kubectl get secret wildcard-cert-secret --namespace=cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: gitea/' | kubectl apply -f -

# The configMap contains the app.ini file values for gitea
envsubst < gitea/configMap.yaml | kubectl apply -n gitea -f -

helm upgrade --install gitea gitea-charts/gitea -f gitea/values.yaml \
  --namespace gitea \
  --version 11.0.1 \
  --atomic \
  --set ingress.hosts[0].host=$GITEA_HOST \
  --set ingress.tls[0].hosts[0]=$GITEA_HOST  \
  --set gitea.admin.username=$GITEA_USER \
  --set gitea.admin.password=$GITEA_PASSWORD \
  --set gitea.admin.email=$GITEA_EMAIL \
  --set gitea.config.database.PASSWD=$POSTGRES_PASSWORD \
  --set gitea.config.database.HOST=$POSTGRES_URL
```

To scale the gitea Runner replicas, edit the `gitea-act-runner` statefulset
and set the replicas to the desired number.

```bash
kubectl edit statefulset gitea-act-runner -n gitea
```

## Configure LDAP for Gitea

Ref: https://github.com/lldap/lldap/blob/main/example_configs/gitea.md

To configure LDAP authentication for Gitea, the LDAP server must be
deployed in the k3s cluster.

LDAP config is done via the Gitea GUI. Here is the LDAP configuration

```text
Host: 192.168.1.144
Port: 3890
Bind DN: uid=admin,ou=people,dc=homelab,dc=local
Bind Password: <admin password>
User Search Base: ou=people,dc=homelab,dc=local
User Filter: (&(memberof=cn=gitea_user,ou=groups,dc=homelab,dc=local)(|(uid=%[1]s)(mail=%[1]s)))
Admin Filter: (memberOf=CN=gitea_admin,OU=groups,DC=homelab,DC=local)
User Name Attribute: uid
First Name Attribute: givenName
Last Name Attribute: sn
Email Attribute: mail
```

# Authentication Middleware Configuration for Traefik Ingress Controller

The Traefik Ingress Controller provides robust authentication capabilities
through middleware implementation. This functionality enables HTTP Basic
Authentication for services that do not include native user authentication
mechanisms.

To implement authentication, a Traefik middleware must be configured within
the target namespace. The process requires creating a secret file containing
authentication credentials (username and password). These credentials must
be base64 encoded before being integrated into the secret manifest file.

Execute the following commands to configure the authentication:

```bash
htpasswd -c traefik_auth username

echo traefik_auth | base64

source .env
envsubst < traefik-middleware/auth_secret.yaml | kubectl apply -n my-portfolio -f -
kubectl apply -f traefik-middleware/auth.yaml -n my-portfolio
```

Following middleware deployment, the authentication must be enabled by adding
the appropriate annotation to the service's Ingress object specification:

```
traefik.ingress.kubernetes.io/router.middlewares: my-portfolio-basic-auth@kubernetescrd
```

# LLDAP Authentication Server

LDAP is a protocol used to access and maintain distributed directory information.
To provide central authentication for all services, an LDAP server is deployed in the
k3s cluster. LLDAP is a lightweight LDAP server that is easy to deploy and manage.
The LLDAP server is deployed using the helm chart and is accessible via the ingress
controller.

```bash
source .env

kubectl create namespace ldap
kubectl get secret wildcard-cert-secret --namespace=cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: ldap/' | kubectl apply -f -

helm upgrade --install ldap \
  lldap-helm-chart/ -f lldap-helm-chart/values.yaml \
  --set ingress.hosts.host=$LDAP_HOST \
  --set ingress.tls[0].hosts[0]=$LDAP_HOST \
  --set secret.lldapUserName=$LLDAP_ADMIN_USER \
  --set secret.lldapJwtSecret=$LLDAP_JWT_SECRET \
  --set secret.lldapUserPass=$LLDAP_ADMIN_PASSWORD \
  --atomic \
  -n ldap
```

# Minio Object Storage

MinIO is a High Performance Object Storage. It is compatible with Amazon S3.
It is deployed in the k3s cluster using the helm chart.

The minio deployment is divided into two parts: the MinIO operator and the
MinIO tenant. The MinIO operator is responsible for managing the MinIO
deployment and the MinIO tenant is responsible for managing the MinIO
buckets and objects. The MinIO operator is deployed in the `minio-operator`
namespace and the MinIO tenant is deployed in the `minio` namespace.

## Deploy MinIO Operator

For deploying the MinIO operator, the MinIO operator helm chart is used.
The default values are sufficient for the operator deployment.

```bash
helm repo add minio https://operator.min.io/
helm repo update
helm install \
  --namespace minio-operator \
  --create-namespace \
  minio-operator minio/operator
```

## Deploy MinIO Tenant

The MinIO tenant is deployed in the `minio` namespace. The default values
are overridden with local values-tenant.yaml file. The minio console is exposed
via internal ingress controller (nginx). Thus, it is only accessible from the
internal network.

```bash
source .env
helm upgrade --install minio-tenant \
  minio/tenant \
  --namespace minio \
  --create-namespace \
  -f minio/values-tenant.yaml \
  --set tenant.configSecret.accessKey=$MINIO_ROOT_USER \
  --set tenant.configSecret.secretKey=$MINIO_ROOT_PASSWORD \
  --set ingress.console.host=$MINIO_HOST \
  --set ingress.console.tls[0].hosts[0]=$MINIO_HOST \
  --atomic
```

# Deploy Database with CloudNativePG operator

Ref: https://cloudnative-pg.io/documentation/current/backup/#main-concepts
CloudNativePG is a Kubernetes operator that manages PostgreSQL clusters.
First, deploy the operator in the `cloudnative-pg` namespace.

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg \
  --namespace cnpg-system \
  --create-namespace \
  cnpg/cloudnative-pg
```

Next, deploy the PostgreSQL cluster in the `postgres` namespace with backup
configured towards the minio object storage.

```bash
source .env
kubectl create namespace immich
# First create the secret for minio access
envsubst < cloud-native-pg/secrets.yaml | kubectl apply -n immich -f -

# Then deploy the postgres cluster
envsubst < cloud-native-pg/cloudnative-pg.yaml | kubectl apply -n immich -f -

# Deploy the backup schedule
kubectl apply -f cloud-native-pg/backup.yaml -n immich
```

## Barnman Cloud Plugin Implementation

The cloudnative PG does not require the barman utility to be installed within
the image anymore for backup and recovery. Instead barman plugin and the
objectStore needs to be deployed in the destination namespace.

Ref: https://cloudnative-pg.io/plugin-barman-cloud/docs/usage/

```bash
# Barman Cloud Plugin
kubectl apply -f \
https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.6.0/manifest.yaml

# Object Store
envsubst < cloud-native-pg/objectStore.yaml | kubectl apply -n immich -f -
```

## Recovery from Backup

Ref: https://cloudnative-pg.io/documentation/1.20/recovery/
To recover the PostgreSQL cluster from a backup using cloudnative-pg,
there are two ways.

1. Recovery from volume snapshot - requires cnpg plugin to take the snapshot
   with kubectl.
2. Recovery from backup stored in object storage - requires the backup to be
   stored in the object storage.

To recover from a backup stored in the object storage, apply the backup-recovery.yaml template with the desired values.

```bash
source .env
envsubst < cloud-native-pg/backup-recovery.yaml | kubectl apply -n immich -f -
```

## Create a new PostgreSQL cluster from existing Database

To create a new PostgreSQL cluster from an existing database, you can use the
`create-cluster-main.yaml` as template. This template allows you to create a new
PostgreSQL cluster from an existing database by specifying the necessary
configurations and parameters in the YAML file.

This below example shows how I created a new PostgreSQL cluster from my existing
main postgres database. The new cluster is created in the `postgres` namespace.
The existing postgres database will be deprecated and removed in the future.

```bash
source .env
envsubst < cloud-native-pg/secrets.yaml | kubectl apply -n postgres -f -
envsubst < cloud-native-pg/create-cluster-main.yaml | kubectl apply -n postgres -f -
kubectl apply -f cloud-native-pg/pg-main-backup.yaml -n postgres
```

# Immich Self-hosted Photo and Video Backup Solution

Immich is a self-hosted photo and video backup solution that is deployed in
the k3s cluster. The Immich deployment uses the existing postgres database
for data storage. The Immich service is exposed via ingress and is accessible
from the internet.

## Database

To use the existing postgres database, first create a new user and database
for Immich in the postgres database.

```bash
# Log into the postgres pod
kubectl exec -it -n immich pg-backup-1 -- psql -U postgres


# Then run the following commands in the psql shell
CREATE ROLE immich WITH LOGIN PASSWORD 'dummypassword';
ALTER ROLE immich WITH SUPERUSER;
CREATE DATABASE immichdb
WITH OWNER immich
TEMPLATE template0
ENCODING UTF8
LC_COLLATE 'en_US.UTF-8'
LC_CTYPE 'en_US.UTF-8';

# Install pgvecto.rs extension
\c immichdb
CREATE EXTENSION vectors;
```

Currently, instead of Cloudnative-PG database, a single instance of postgres
statefulset with recommended postgres + extensions image from Immich is used.

```bash
source .env
envsubst < immich/database.yaml | kubectl apply -n immich -f -
```

## Persistant Storage for media

Next, create or verify local disk for immich backup. For my setup, I have a
dedicated server with a large disk that I want to use for immich media storage.
The disk is mounted at /media/immich via NFS.

```bash
ssh dockerhost

sudo mkdir -p /media/immich
sudo mkfs.ext4 /dev/sdd
sudo mount /dev/sdd /media/immich
echo "/dev/sdd /media/immich ext4 defaults 0 2" | sudo tee -a /etc/fstab

echo "/media/immich    192.168.1.135/24(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
sudo exportfs -a
```

After that, create a PV and PVC for the immich backup storage.

```bash
source .env
envsubst < immich/persistence.yaml | kubectl apply -n immich -f -
```

Finally, deploy the Immich helm chart with the following values:

```bash
source .env
helm upgrade --install \
    --namespace immich immich oci://ghcr.io/immich-app/immich-charts/immich\
    -f immich/immich-helm-chart/values.yaml \
    --set controllers.main.containers.main.env.DB_USERNAME=$IMMICH_DB_USER \
    --set controllers.main.containers.main.env.DB_PASSWORD=$IMMICH_DB_PASSWORD \
    --set controllers.main.containers.main.env.DB_DATABASE_NAME=$IMMICH_DB_NAME \
    --set server.ingress.main.hosts[0].host=$IMMICH_HOST \
    --set server.ingress.main.tls[0].hosts[0]=$IMMICH_HOST \
    --rollback-on-failure
```

# Cron Jobs for Periodic Tasks

## Update DNS Record

This cronjob updates current public IP address to the DNS record in Cloudflare.
The script to update DNS record is added to the cronjob as configmap and then
mounted as a volume in the cronjob pod. The script uses the Cloudflare API
to update the DNS record with the current public IP address.

Currently the cronjob is scheduled to run every hour.

```bash
kubectl create namespace cronjobs --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic cloudflare-dns-token \
  --from-literal=api-token=$CLOUDFLARE_TOKEN \
  -n cronjobs
kubectl apply -f cronjobs/update-dns/update_dns_config.yaml -n cronjobs
kubectl apply -f cronjobs/update-dns/update_dns_cronjob.yaml -n cronjobs
```

# Woodpecker CI

Woodpecker is a lightweight CI/CD server that is deployed in the k3s cluster.

Since Woodpecker uses Oauth2 for authentication, it requires a Gitea
application to be created for Woodpecker to use for authentication.

First, create a new application in Gitea for Woodpecker. The path to create the
application is:
`https://<your-gitea-domain>/user/settings/applications/`

The application should have the following settings:

- **Application Name**: Woodpecker
- **Redirect URI**: https://<your-woodpecker-domain>/authorize

```bash
source .env
helm repo add woodpecker https://woodpecker-ci.org/
helm repo update
helm upgrade --install woodpecker woodpecker/woodpecker \
  -f woodpecker-ci/values.yaml \
  --version 3.2.1 \
  --namespace woodpecker \
  --create-namespace \
  --set server.ingress.hosts[0].host=$WOODPECKER_HOST \
  --set server.ingress.tls[0].hosts[0]=$WOODPECKER_HOST \
  --set server.env.WOODPECKER_HOST=https://$WOODPECKER_HOST \
  --set server.secrets[0].data.WOODPECKER_GITEA_URL=https://$GITEA_HOST \
  --set server.secrets[0].data.WOODPECKER_GITEA_CLIENT=$WOODPECKER_CLIENT_ID \
  --set server.secrets[0].data.WOODPECKER_GITEA_SECRET=$WOODPECKER_CLIENT_SECRET \
  --atomic
```

# Monitoring

To monitor the Kubernetes cluster, the kube-prometheus stack is deployed.
This stack includes Prometheus Node Exporter, kube-state-metrics, Alertmanager,
and Grafana. It provides a comprehensive set of default Grafana dashboards for
tracking key system metrics such as CPU, memory, I/O, and network usage.

The dashbaords are loaded as kubernetes configMaps. To create a persistant
custom dashboard, one can create a configmap within the monitoring namespace
following the example of existing dashboards. One way to generate dashboard,
is to create the dashboard from the Grafana UI and then export the JSON file
and embed it to the configmap.

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
source .env
helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
  -f kube-prometheus-stack/values.yaml \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminUser=$GRAFANA_ADMIN \
  --set grafana.adminPassword=$GRAFANA_PASSWORD \
  --set grafana.ingress.hosts[0]=$GRAFANA_HOST \
  --set grafana.ingress.tls[0].hosts[0]=$GRAFANA_HOST \
  --atomic
```

# Paperless-ngx

Paperless-ngx is a document management system that allows you to manage your
documents in a digital way. It provides features such as document scanning,
OCR (Optical Character Recognition), and a web interface for easy access
to your documents. The main advantage is finding documents quickly and easily
using powerful search capabilities.

```bash
source .env
envsubst < paperless-ngx/manifest.yaml | kubectl apply -f -
```

# OpenCloud File Sharing (PosixFS, NFS-backed)

OpenCloud runs as a single container (`opencloudeu/opencloud-rolling`) in its
own `opencloud` namespace. User files are stored as plain files via the
PosixFS driver in collaborative mode on a Longhorn-backed volume:

## Deploy (same norm as paperless)

```bash
source .env
envsubst < opencloud/pvc.yaml | kubectl apply -f -
envsubst < opencloud/opencloud-deploy.yaml | kubectl apply -f -
```

Verify:

```bash
kubectl -n opencloud get pvc,pods,svc,ingress
kubectl -n opencloud logs deploy/opencloud -c opencloud --tail=50
```

# Claimctl

The chart is published to GHCR as an OCI artifact, so this directory only
holds the values overlay (`claimctl/values.yaml`).

```bash
source .env
helm upgrade --install claimctl \
    oci://ghcr.io/thetaqitahmid/claimctl/helm/claimctl \
    --version 0.1.0 \
    --namespace claimctl --create-namespace --wait \
    --values claimctl/values.yaml \
    --set ingress.hosts[0].host=$CLAIMCTL_HOST \
    --set ingress.tls[0].hosts[0]=$CLAIMCTL_HOST \
    --set ingress.tls[0].secretName="claimctl-tls"
```
