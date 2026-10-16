# Flux CD application adoption

The cluster entry point now includes AdGuard Home, the portfolio site,
OpenCloud, and Stirling PDF. No workload changes have been applied by creating
these files. OpenCloud's admin Secret was manually provisioned from `.env`.
The root and application reconciliations all have pruning disabled. Application
reconciliations use `deletionPolicy: Orphan` to preserve resources if removed.
All four application Kustomizations currently set `spec.suspend: false`, so
application reconciliation is enabled. The root remains active.

## Included applications

| Application | Existing namespace and release | Version policy |
|---|---|---|
| AdGuard Home | `adguard` / `adguard` | Local chart `0.1.0`; running image digest |
| Portfolio | `my-portfolio` / ordinary manifests | Running image digest |
| OpenCloud | `opencloud` / ordinary manifests | Running image digest for init and server |
| Stirling PDF | `stirling-pdf` / `stirling-pdf` | Chart `3.1.0`, image `3.1.0` |

The three deployments using `latest` were checked against their running pods'
`imageID` fields. Their observed digests are pinned in the Flux overlays, so
adoption does not pull a new `latest` image. OpenCloud's shared manifest now
uses a pinned image and Secret references; the other manual deployment paths
remain unchanged. Digest pins may be platform specific; recheck them
before moving workloads to a different CPU architecture.

AdGuard's chart comes from this GitRepository. Flux ignores `version` for a
Git chart source; `0.1.0` records the current chart version, while the actual
chart content is controlled by the reconciled Git revision. `reconcileStrategy:
Revision` ensures committed chart edits are packaged. Review chart changes as
release changes. Stirling's Helm repository chart version is an exact pin.

## Private hostname configuration

Create a private copy of `flux/ingress-hostnames.example.yaml` outside the
repository, replace all four example hostnames, and apply it:

```sh
kubectl apply -f /private/path/ingress-hostnames.yaml
```

The ConfigMap must be named `ingress-hostnames` in `flux-system` and contain
nonempty `ADGUARD_HOST`, `PORTFOLIO_HOST`, `OPENCLOUD_HOST`, and
`STIRLING_PDF_HOST` keys. The
example file is deliberately not included in the cluster entry point. Do not
commit private hostname values. All four child reconciliations require this
ConfigMap through `postBuild.substituteFrom`; they fail if it does not exist.
The watch label triggers reconciliation when hostnames change.

Enable `StrictPostBuildSubstitutions=true` in the kustomize-controller's
`--feature-gates` argument through your Flux controller installation configuration
before enabling these apps. Preserve any other feature gates already configured.
This rejects absent keys rather than silently replacing them with empty strings;
it does not validate empty or malformed hostnames. Verify every key explicitly.

## Adoption prerequisites

These definitions adopt existing installations, not a complete fresh cluster.
Existing namespaces, ingress controllers, the `acme-issuer` ClusterIssuer,
Longhorn storage classes and application PVCs must already be present. Back up
persistent data before adoption.

Stirling PDF continues to reference the existing `stirling-pdf-admin` Secret
with `username` and `password` keys in `stirling-pdf`. Its presence was verified
without reading credentials. Provision it using the root README on recovery.
No Secret values are committed or substituted. Portfolio's manifest does not
currently reference a registry pull Secret; confirm the pinned GHCR image is
pullable on the target nodes, and preserve any node or ServiceAccount registry
authentication already in use.

OpenCloud uses `opencloud-admin` in namespace `opencloud`, with key `password`,
for `IDM_ADMIN_PASSWORD` in both init and server containers. This Secret was
created manually from `.env` after confirming the password matched the live
Deployment. No workload was applied or restarted. For recovery, create the
namespace and recreate the Secret before reconciliation (Fish shell):

```fish
source .env
kubectl create namespace opencloud --dry-run=client -o yaml | kubectl apply -f -
if set -q OPENCLOUD_ADMIN_PASSWORD; and test -n "$OPENCLOUD_ADMIN_PASSWORD"
    printf '%s' "$OPENCLOUD_ADMIN_PASSWORD" | kubectl create secret generic opencloud-admin \
        --namespace opencloud --from-file=password=/dev/stdin
end
```

When adopting a Deployment that previously used inline passwords, Kubernetes
may reject Flux's dry run because the old `value` survives alongside the new
`valueFrom`. An explicit empty `value` in Git was insufficient during the live
adoption. A one-time strategic merge patch removed `value` from both password
entries and set their Secret references after verifying the Secret matched the
live password. This triggered a successful rollout; subsequent Flux
reconciliation succeeded. The legacy last-applied annotation was also removed
because it could retain inline credentials. For another cluster, perform this
migration deliberately before adoption; do not delete the Deployment or enable
forced resource replacement to work around it.

The Secret creation command refuses to overwrite an existing Secret. The
password is neither a command line argument nor written to a temporary file. Changing this Secret is
not an account password reset; OpenCloud initializes its account on first use.
The live `opencloud-data` (20Gi) and `opencloud-config` (5Gi) claims were checked
against the manifests: both use Longhorn and `ReadWriteOnce`. These claims are
included with pruning disabled; preserve their data and back them up separately.

AdGuard's existing `adguard/adguard` release is the adoption target, not the
historical release with the same name in `default`. The local chart still
renders its existing workload, Service, ingress, and PVC names.

## Configure Flux in the cluster

Run the following steps from the Kubernetes directory containing this README's
parent `README.md`. Commands in this section use Bash; the OpenCloud Secret
recovery example above uses Fish because `.env` is a Fish configuration file.
Do not source that file in Bash.

This guide uses `flux install` followed by this repository's existing source
and root Kustomization. This avoids generating a second bootstrap source or
root reconciliation. Do not also run `flux bootstrap github` against this path:
its generated files can conflict with this layout. If Flux already exists,
inspect its installation owner and sources first rather than replacing them.

### 1. Install the CLI and check cluster access

Install `kubectl` and the Flux CLI using the
[official CLI installation instructions](https://fluxcd.io/flux/installation/#install-the-flux-cli).
For example, with Homebrew:

```sh
brew install fluxcd/tap/flux
kubectl config current-context
kubectl get nodes
flux version --client
flux check --pre
```

Confirm that the current context is the intended homelab cluster and that you
have permission to install CRDs, ClusterRoles, and controllers. Select a stable
Flux release compatible with the cluster's Kubernetes version. Review the
[release notes](https://github.com/fluxcd/flux2/releases) and use its exact
`vX.Y.Z` version below, not a floating `latest` reference.

### 2. Install pinned controllers with strict substitution

Choose a persistent directory outside this Git checkout for the controller
installation bundle, and replace the example version before running:

```sh
export FLUX_VERSION='vX.Y.Z'
export FLUX_INSTALL_DIR="$HOME/.config/homelab-flux"
mkdir -p "$FLUX_INSTALL_DIR"
flux install --namespace=flux-system --version="$FLUX_VERSION" --export \
  > "$FLUX_INSTALL_DIR/gotk-components.yaml"
```

Create `$FLUX_INSTALL_DIR/kustomization.yaml` with the following content:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - gotk-components.yaml
patches:
  - target:
      group: apps
      version: v1
      kind: Deployment
      name: kustomize-controller
    patch: |-
      - op: add
        path: /spec/template/spec/containers/0/args/-
        value: --feature-gates=StrictPostBuildSubstitutions=true
```

Inspect the exported controller arguments first. If that release already sets
`--feature-gates`, merge `StrictPostBuildSubstitutions=true` into the existing
argument rather than adding a duplicate. Then render, inspect, and install:

```sh
kubectl kustomize "$FLUX_INSTALL_DIR"
kubectl apply -k "$FLUX_INSTALL_DIR"
kubectl wait --for=condition=Established --timeout=2m \
  crd/gitrepositories.source.toolkit.fluxcd.io \
  crd/kustomizations.kustomize.toolkit.fluxcd.io \
  crd/helmreleases.helm.toolkit.fluxcd.io
kubectl wait --namespace=flux-system --for=condition=Available \
  deployment --all --timeout=5m
flux check
```

The default install includes source-controller, kustomize-controller,
helm-controller, and notification-controller. Image automation controllers are
not needed for these pinned applications. Controllers are maintained by this
installation bundle, not the repository's root reconciliation. Keep the bundle
for recovery, and retain its strict substitution patch when upgrading Flux.

### 3. Prepare hostnames, credentials, and storage

Complete [Adoption prerequisites](#adoption-prerequisites) before enabling the
Git source. The Flux installation creates `flux-system`, so you can now apply
your private ConfigMap as described in
[Private hostname configuration](#private-hostname-configuration):

```sh
kubectl apply -f /private/path/ingress-hostnames.yaml
kubectl get configmap ingress-hostnames --namespace=flux-system
kubectl get secret opencloud-admin --namespace=opencloud
kubectl get secret stirling-pdf-admin --namespace=stirling-pdf
kubectl get clusterissuer acme-issuer
kubectl get storageclass longhorn longhorn-2x
kubectl get pvc --namespace=adguard
kubectl get pvc --namespace=opencloud
kubectl get pvc --namespace=stirling-pdf
```

Verify all four hostname keys contain real, nonempty hostnames and that DNS
points to the intended ingress controller. Check that the referenced Secrets
have their required keys without printing their values. Recreate missing
Secrets using the recovery instructions; do not put them into Git. Confirm
storage is healthy and take backups. These definitions do not provision the
cluster's networking, certificate issuer, or storage platform.

## Deploy the applications

### 1. Validate and publish the configuration

The Git source is configured as follows:

```text
Repository: https://github.com/TheTaqiTahmid/homeserver.git
Branch:     master
Path:       ./kubernetes/flux/clusters/homelab
```

Flux source paths are relative to the Git repository root, not the local
`kubernetes/` working directory. The root path is
`./kubernetes/flux/clusters/homelab`, application paths begin with
`./kubernetes/flux/apps/`, and AdGuard's chart path is
`./kubernetes/adguard-helm-chart`. Local `kubectl -k` commands in this guide
still run from the `kubernetes/` directory and therefore omit that prefix.

The public HTTPS source needs no Git credential Secret. If the repository
becomes private, provision Git authentication and configure
`GitRepository/homelab.spec.secretRef` before continuing. Controllers also need
network access to GitHub, the Helm repository, and the image registries.

Install Python with PyYAML for the tests, then validate locally:

```sh
python -m unittest discover -s flux/tests -v
kubectl kustomize flux/clusters/homelab
kubectl kustomize flux/apps/adguard
kubectl kustomize flux/apps/my-portfolio
kubectl kustomize flux/apps/stirling-pdf
kubectl kustomize flux/apps/opencloud
git diff --check
```

Review and commit the intended configuration files, including the application
manifests and local chart outside `flux/`, and push them to `master` in the
configured remote. Do not blindly stage unrelated local edits or private files.
Flux reads the remote branch, not your working tree; uncommitted or unpushed
changes will not be deployed.

All application Kustomizations in `flux/apps/reconciliations.yaml` currently
set `spec.suspend: false`, enabling all four applications. For staged adoption,
set `spec.suspend: true` on the applications you want to defer before committing
and pushing. Set each back to `false` in Git when ready. This avoids a race
where all apps begin reconciling before an imperative suspend command.

### 2. Enable the source and root reconciliation

Only after the remote configuration and prerequisites are ready, run:

```sh
kubectl apply -k flux/clusters/homelab
flux reconcile source git homelab --namespace=flux-system
flux reconcile kustomization homelab --namespace=flux-system --with-source
flux get kustomizations --namespace=flux-system
```

The root creates `app-adguard`, `app-my-portfolio`, `app-opencloud`, and
`app-stirling-pdf`. Each child reads its hostname values from the private
ConfigMap. AdGuard and Stirling are adopted through HelmRelease resources;
OpenCloud and the portfolio site are applied as ordinary Kubernetes manifests.
A child configured with `spec.suspend: true` will remain suspended.

### 3. Reconcile and verify each enabled application

Run these only for the apps you have enabled:

```sh
flux reconcile kustomization app-adguard --namespace=flux-system --with-source
flux reconcile kustomization app-my-portfolio --namespace=flux-system --with-source
flux reconcile kustomization app-opencloud --namespace=flux-system --with-source
flux reconcile kustomization app-stirling-pdf --namespace=flux-system --with-source
flux get kustomizations --namespace=flux-system
flux get helmreleases --all-namespaces
kubectl rollout status deployment/adguard-home --namespace=adguard --timeout=10m
kubectl rollout status deployment/portfolio-app --namespace=my-portfolio --timeout=5m
kubectl rollout status deployment/opencloud --namespace=opencloud --timeout=10m
kubectl rollout status deployment/stirling-pdf --namespace=stirling-pdf --timeout=15m
kubectl get ingress --all-namespaces
```

Expect `Ready=True` for enabled child Kustomizations and both HelmReleases.
Check application URLs, TLS certificates, login behavior, and existing data.
A digest change in a pod template can cause a rollout even when the underlying
image is unchanged. Do not continue manual Helm upgrades or `kubectl apply` of
application manifests after Flux takes ownership.

## Updates, troubleshooting, and recovery

For future configuration or version changes, review the diff, commit, and push
to `master`. Flux polls Git every minute and reconciles applications every ten
minutes; use the reconcile commands above to request an immediate update.
To change hostnames, update the private ConfigMap, then reconcile the affected
child. To rotate a Secret, update it privately and follow the application's
restart and credential rotation requirements; a Secret change alone does not
necessarily restart pods or reset accounts.

If an application does not become ready, inspect status and controller events:

```sh
flux get sources all --all-namespaces
flux get kustomizations --namespace=flux-system
flux get helmreleases --all-namespaces
kubectl describe kustomization app-opencloud --namespace=flux-system
kubectl describe helmrelease adguard --namespace=adguard
kubectl describe helmrelease stirling-pdf --namespace=stirling-pdf
flux logs --level=error --all-namespaces
```

Common causes include missing hostname keys, missing Secret keys, unavailable
charts or images, storage that cannot attach, and an unready certificate issuer.

For `kustomization path not found`, check the fetched Git revision and confirm
that `spec.path` exists relative to the repository root. If the root itself has
a wrong path, pushing a fix alone cannot repair it: the root cannot render the
commit that would update its own configuration. After pushing the corrected
paths, reapply the entry point from the local `kubernetes/` directory:

```sh
kubectl apply -k flux/clusters/homelab
flux reconcile source git homelab --namespace=flux-system
flux reconcile kustomization homelab --namespace=flux-system --with-source
```

This repairs the live root path and updates the child paths as well.
Logs can contain application details; review them before sharing.

For an emergency pause, suspend the root first and then the affected child:

```sh
flux suspend kustomization homelab --namespace=flux-system
flux suspend kustomization app-opencloud --namespace=flux-system
```

Suspending a Kustomization does not stop a HelmRelease it already created.
For a Helm app, suspend its HelmRelease too, for example:

```sh
flux suspend helmrelease adguard --namespace=adguard
```

Record longer pauses in Git so the root cannot undo them when resumed. To
roll back configuration, revert the relevant Git commit and reconcile again;
a Git revert is not a database or volume rollback. Restore data from backups
when required. Resume the affected HelmRelease or child with `flux resume`,
then resume `homelab` once the configuration is safe.

Pruning is disabled and child deletion policies are `Orphan`, but Helm upgrades
can still modify or remove chart resources. Do not delete HelmReleases as a
pause mechanism: helm-controller can uninstall their releases. Controller
installation files, private hostname configuration, manually provisioned
Secrets, and data backups must remain available outside the cluster for recovery.

References: [Flux install](https://fluxcd.io/flux/cmd/flux_install/),
[Kustomization substitution and suspension](https://fluxcd.io/flux/components/kustomize/kustomizations/),
and [HelmRelease lifecycle](https://fluxcd.io/flux/components/helm/helmreleases/).

## Deferred applications

Claimctl still requires deliberate database credential and encryption Secret
handling. qBittorrent requires VPN secrets. Monitoring requires admin credential
handling. Jellyfin needs NFS configuration and storage adoption. Platform
controllers are excluded to avoid changing cluster networking, certificates,
or storage ownership in this
first batch. Their live image versions have not been changed by this work.
