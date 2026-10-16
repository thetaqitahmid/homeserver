# Flux CD migration readiness report

## Scope and evidence

This report covers only components observed as currently deployed in the
connected Kubernetes cluster when the report was prepared, checked against the
deployment instructions and manifests in this repository. The cluster was
queried with `kubectl` for workloads and pods. A workload being present does not
prove that it is healthy or that every configuration detail is represented in
this repository.

The working tree already had local modifications in `README.md` and
`opencloud/opencloud-deploy.yaml`, and an untracked `stirling-pdf/` directory.
Those changes were not altered for this report. Review and preserve them before
any migration or reconciliation.

## Overall assessment

Flux is a good fit, but this repository is not ready for a cluster-wide initial
reconciliation. Most applications are deployed through Helm, shell substitution,
or one-off `kubectl` operations; a number of runtime settings, dependencies, and
bootstrap steps need to be made declarative first. Migrate incrementally,
beginning with a low-risk application. Import or adopt existing resources
carefully: applying a HelmRelease or manifest over an existing installation can
cause upgrades, replacement, or drift, and enabling pruning can delete
resources.

### Readiness summary

| Component | Observed namespace | Evidence in repository | Readiness |
|---|---|---|---|
| K3s platform: CoreDNS, local-path provisioner, metrics-server | kube-system | Cluster-managed | Bootstrap/platform boundary; not ordinary Flux-managed workloads |
| kube-vip | kube-system | `kube-vip/values.yaml`, README Helm commands | Needs chart release definition and node/bootstrap boundaries documented |
| Traefik | kube-system | `traefik/traefik-values.yaml`, README Helm commands | Near-ready after pinning chart/source and validating cluster-specific settings |
| ingress-nginx | ingress-nginx | README install command only | Needs pinned chart configuration and values committed |
| MetalLB | metallb-system | `metallb/metallbConfig.yaml`, README references a different path | Needs correct in-repository configuration and pinned installation source/version |
| cert-manager | cert-manager | README install command and cert-manager config chart | Needs pinned chart, CRD lifecycle, and secret/certificate workflow defined |
| Longhorn | longhorn-system | `longhorn/values.yaml` and storage-class manifests | Needs pinned chart and explicit preservation/ownership of existing storage resources |
| AdGuard Home | adguard | `adguard-helm-chart/` and README Helm steps | Needs hostname/certificate inputs externalized and chart release pinned |
| Jellyfin | media | `media/` manifests and README `envsubst` steps | Needs NFS host/path inputs made declarative; decide whether copied cert is still needed |
| qBittorrent | media | `qbittorrent-helm-chart/`, README Helm steps | Needs values/secrets reviewed and chart dependencies pinned |
| kube-prometheus-stack | monitoring | `kube-prometheus-stack/values.yaml`, README Helm steps | Needs admin credentials referenced from Secret, ingress host externalized, and chart pinned |
| Portfolio app | my-portfolio | `my-portfolio/portfolioManifest.yaml`, README image pull secret and substitution steps | Needs image pull Secret lifecycle and hostname/config substitution moved out of shell workflow |
| CronJob DNS updater | cronjobs | README install steps; running jobs observed | Needs its CronJob/config manifests checked in and Cloudflare token provided through Secret/ExternalSecret |
| Claimctl and PostgreSQL | claimctl | `claimctl/values.yaml`, README Helm steps | Needs chart source/version and PostgreSQL persistence/secret behavior explicitly represented |
| OpenCloud | opencloud | `opencloud/*.yaml`, README uses `envsubst` | Needs each substituted value replaced with ConfigMap/Secret references or Flux substitution; review pending local edits |
| Stirling PDF | stirling-pdf | `stirling-pdf/values.yaml`, README Helm steps | Needs host provided outside Git and initial admin Secret provisioned before HelmRelease; verify the current untracked files are intended for Git |
| System Upgrade Controller | system-upgrade | No matching deployment configuration identified in inspected files | Find and capture the live installation source/configuration before managing it |

Other applications described in the README or represented by files but not
observed running during the cluster check are intentionally excluded. Examples
include Gitea, Woodpecker, Immich, Paperless, MinIO, and several
database/operator workloads. Recheck cluster state before using this report as
an inventory.

## Cross-cutting changes before Flux adoption

### 1. Establish a declarative layout

Create a Flux bootstrap directory and separate platform dependencies from
applications. For example, use a cluster entry point, a platform directory for
CRDs and cluster services, and one directory or Flux Kustomization per
application. Give each Helm-managed application a `HelmRelease`; define chart
sources as `HelmRepository` or `OCIRepository`, pin chart versions, and keep
values in reviewed files. Use dependency ordering (`dependsOn`) where an app
depends on a controller or CRD.

### 2. Replace shell substitution

Remove `envsubst` from the deployment path. Put non-sensitive settings in Helm
values, ConfigMaps, or Kustomize patches. For private ingress FQDNs, a Flux
`postBuild.substituteFrom` ConfigMap is appropriate; create it in the namespace
of the Flux Kustomization (commonly `flux-system`) and use distinct placeholders
such as `${OPENCloud_INGRESS_FQDN}` consistently. Validate that all placeholders
are present because an absent substitution can render an empty value. For
application runtime settings, prefer native chart values or Kubernetes
ConfigMap/Secret references over rendering YAML in a shell.

Review substitutions in particular for Jellyfin NFS PVs, OpenCloud
PVC/deployment, Paperless if re-enabled, PostgreSQL/CloudNativePG resources,
Gitea config if re-enabled, and Stirling PDF values.

### 3. Define secret management before reconciliation

The `.env` and `.secrets/` paths are ignored by Git, which is useful, but
ignored files are not a secret-management solution for a fresh cluster. Do not
commit secret values or inject them in Helm command-line `--set` arguments.
Choose manually provisioned Kubernetes Secrets or OpenBao plus External Secrets
Operator. Ensure Flux resources reference generated/existing Secrets, document
initial provisioning and recovery, and verify K3s datastore encryption-at-rest
and RBAC. Candidates visible in active deployment instructions include
monitoring admin credentials, portfolio registry credentials, DNS updater
Cloudflare token, and any chart/application database or OAuth credentials found
in live values.

### 4. Protect stateful data and establish ownership

Inventory PVCs, PVs, StorageClasses, databases, and backup/restore procedures
before turning on `prune`. Ensure HelmRelease uninstall or resource pruning
cannot accidentally remove data-bearing resources. For existing Helm
installations, inspect release names, namespaces, chart versions, and values;
adopt with matching identities and settings, then reconcile without accidental
chart upgrades. Avoid having both Flux and manual Helm commands manage the same
release.

### 5. Make external prerequisites explicit

Host-level NFS mounts/exports, node labels and systemd configuration, K3s
installation/upgrades, DNS records, and out-of-cluster storage prerequisites are
not automatically solved by Flux. Document these as bootstrap prerequisites or
manage them through an appropriate host-configuration system. Confirm required
namespaces and CRDs are established before dependent resources.

### 6. Bootstrap and recovery

Keep Flux bootstrap instructions and credentials outside the cluster. If Git
hosting is self-hosted in the cluster, confirm recovery access remains available
when the cluster is unavailable. Start without broad pruning, inspect rendered
resources and diffs, and enable pruning only for application scopes after
validation.

## Component-specific migration notes

### Platform and cluster networking

* **K3s built-ins:** CoreDNS, local-path provisioner, and metrics-server are
  present. Treat K3s-managed components as platform/bootstrap configuration
  unless there is a deliberate plan to override K3s packaging. CoreDNS was
  manually edited according to the README; capture any required forwarding
  configuration through a supported K3s mechanism rather than a one-off edit.
* **kube-vip:** The chart values exist, but initial server TLS SAN and systemd
  changes are host-level steps in the README. Keep those outside Flux and
  document them as cluster bootstrap requirements. Confirm the current live
  VIP and chart values match before adoption. One kube-vip pod was observed in
  `CrashLoopBackOff`, so investigate and resolve that before migration.
* **Traefik:** Values are present. Pin chart version/repository, verify its
  K3s-managed installation is not simultaneously managed by the K3s packaged
  chart, and carry over the current ingress/provider configuration. Avoid
  competing ownership.
* **ingress-nginx:** Only a one-line install is documented. Add a pinned
  HelmRepository/HelmRelease and explicit values, including service exposure
  and internal load-balancer address policy, after inspecting the live
  release.
* **MetalLB:** A live controller and speakers are running. The README
  references `/home/taqi/homeserver/k3s-infra/metallb/metallbConfig.yaml`,
  while this repository contains `metallb/metallbConfig.yaml`. Resolve the
  path mismatch, capture the installed manifest/chart version, and ensure
  address pools/advertisements match live state before adoption.

### Certificates and storage

* **cert-manager:** Pin the chart and explicitly decide how CRDs are installed
  and upgraded. Convert the certificate configuration chart and Cloudflare
  credentials to declarative resources and Secret references. Replace manual
  wildcard-secret copying between namespaces with certificates issued directly
  in each namespace, or use an explicitly supported replication solution.
  Confirm the wildcard certificate and issuer behavior before applying.
* **Longhorn:** Pin the chart and align values with the existing installation.
  Bring the two StorageClass manifests under management only after comparing
  them with live classes. Preserve PVC/PV data; validate backup and restore.
  The README documents manual edits to a Longhorn ConfigMap, which should be
  represented in supported chart values or a managed resource after checking
  current state.

### Applications

* **AdGuard Home:** Use the existing local chart as the chart source and set
  values through its HelmRelease. Replace copied wildcard TLS secret handling
  with a stable namespace-local certificate/secret plan. Externalize private
  hostnames.
* **Jellyfin and qBittorrent:** Capture current release/manifests and storage
  bindings. Jellyfin uses NFS-backed PV/PVC resources, and the README
  substitutes host/path data; make those settings explicit through suitable
  manifests or patches. Treat NFS server availability and host exports as
  prerequisites. Review qBittorrent VPN and credential values for sensitive
  data before committing.
* **Monitoring:** Create a HelmRelease for the existing kube-prometheus-stack
  installation with the matching release name, namespace, chart version, and
  values. Reference Grafana credentials from a pre-provisioned Secret or
  ExternalSecret. Provide the Grafana hostname through a private ConfigMap
  substitution or another private values source. Protect monitoring PVCs and
  generated dashboards during adoption.
* **Portfolio app:** Convert the manifest to ordinary Kustomize resources or a
  HelmRelease as appropriate. Replace shell substitution with private hostname
  substitution and ConfigMap/Secret references. Provision its private-registry
  pull Secret out of band or through External Secrets Operator.
* **DNS updater CronJob:** Identify the precise currently deployed CronJob
  definition and make it a checked-in manifest. Keep the Cloudflare token in a
  Kubernetes Secret/ExternalSecret and reference it from the job. Avoid
  managing generated Job history as source resources.
* **Claimctl:** Declare the OCI chart source and pinned version in a
  HelmRelease, preserving the current release name and namespace. Check how
  the chart configures its PostgreSQL StatefulSet and PVC, and manage
  credentials through Secret references.
* **OpenCloud:** Convert `opencloud/pvc.yaml` and
  `opencloud/opencloud-deploy.yaml` from `envsubst` templates into manifests
  that use explicit values, ConfigMaps, or Flux substitutions. Preserve the
  existing workload identity and Longhorn claim; validate access modes,
  capacity, and retention before enabling pruning.
* **Stirling PDF:** Use a pinned chart source and HelmRelease referencing
  `stirling-pdf/values.yaml`. Keep the ingress hostname outside Git using Flux
  substitution. The initial `stirling-pdf-admin` Secret is created manually in
  the documented flow; ensure it exists before first release reconciliation,
  or source it from OpenBao through External Secrets Operator. Protect the
  persistent configuration/database volume and confirm chart values match the
  live release.
* **System Upgrade Controller:** It is running but its source and
  configuration are not evident in the inspected repository. Identify the live
  chart/manifests and plan lifecycle carefully; node upgrades can disrupt the
  entire cluster and should not be an incidental first Flux migration.

## Suggested migration sequence

1. Confirm the inventory and resolve the kube-vip pod failure.
2. Decide secret handling, private hostname ConfigMap keys, repository layout,
and bootstrap recovery.
3. Migrate one low-risk stateless app with pruning disabled; compare its
Flux-rendered output to the live resources.
4. Adopt additional applications one at a time, matching current release names,
versions, values, and namespaces.
5. Migrate platform controllers and stateful systems only with explicit CRD,
storage, backup, and recovery plans.
6. Enable pruning selectively after confirming ownership and deletion behavior.

## Limitations

This is a readiness review, not a generated Flux configuration. The observed
cluster inventory is time-sensitive. A `kubectl` workload listing does not
expose the full Helm release history, chart versions, values, all standalone
resources, or host configuration. Inspect those live details before adoption and
rerun the inventory immediately before migration.
