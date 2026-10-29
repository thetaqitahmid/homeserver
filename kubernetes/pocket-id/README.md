# Pocket ID

Application manifests live in `pocket-id/`. The thin wrapper at
`flux/apps/pocket-id/kustomization.yaml` references this directory, matching
the existing OpenCloud layout.

Pocket ID v2.19.1 is pinned to the upstream multiarchitecture image digest.
It runs as UID/GID 1000 with SQLite and filesystem uploads on a 1Gi Longhorn
RWO volume mounted at `/app/data`. Longhorn must provide a block volume, not
an NFS/RWX share: SQLite requires reliable filesystem locking. One replica and
`Recreate` avoid overlapping writers during upgrades; upgrades cause downtime.

## Private prerequisites

Run commands from the `kubernetes/` directory. Cluster access, Longhorn,
Traefik, the `acme-issuer` ClusterIssuer, and the existing Flux installation
are required. Choose the permanent hostname before registering passkeys,
configure its DNS to reach Traefik, and set it privately:

```bash
export POCKET_ID_HOST='id.your-domain.example'
kubectl patch configmap ingress-hostnames --namespace=flux-system --type=merge \
  --patch "{\"data\":{\"POCKET_ID_HOST\":\"${POCKET_ID_HOST}\"}}"
```

Do not apply the example hostname ConfigMap over the live one: that would
replace other applications' private hostnames.

Generate the encryption key only for a new installation. This command never
replaces an existing local file, writes no trailing newline, and does not print
the key. The `.secrets/` directory is ignored by Git.

```bash
python - <<'PY'
import os
import pathlib
import secrets

path = pathlib.Path('.secrets/pocket-id-encryption-key')
path.parent.mkdir(mode=0o700, exist_ok=True)
if not path.exists():
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(secrets.token_urlsafe(32))
PY
kubectl apply -f pocket-id/namespace.yaml
kubectl create secret generic pocket-id-secrets --namespace=pocket-id \
  --from-file=ENCRYPTION_KEY=.secrets/pocket-id-encryption-key
```

Secret creation intentionally refuses to overwrite an existing Secret. For
an existing installation, preserve its existing encryption key instead of
generating a replacement. Store a secure copy outside this machine. Losing
it makes encrypted database fields unusable. No Secret manifest or value
belongs in Git. Secret changes alone do not restart the Deployment, and
rotation requires Pocket ID's `encryption-key-rotate` procedure.

## Publish and deploy

Validate with the repository's Flux tests, review and commit the intended
changes, then push to the `master` branch consumed by `GitRepository/homelab`.
Flux cannot deploy unpublished local files. Once published and prerequisites
are ready:

```bash
flux reconcile source git homelab --namespace=flux-system
flux reconcile kustomization homelab --namespace=flux-system --with-source
flux reconcile kustomization app-pocket-id --namespace=flux-system
kubectl rollout status deployment/pocket-id --namespace=pocket-id --timeout=10m
kubectl get pvc,pods,ingress,certificate --namespace=pocket-id
flux get kustomization app-pocket-id --namespace=flux-system
```

Confirm the certificate is Ready, then immediately visit
`https://<your-hostname>/setup` to create the first admin and register a
passkey. Restrict public access until initial setup is complete. Verify the
OIDC discovery endpoint at
`https://<your-hostname>/.well-known/openid-configuration` and test a client
login before relying on Pocket ID for other applications. Client secrets
are created when configuring confidential OIDC clients, not at deployment.

SMTP, LDAP, MaxMind, and static API credentials are not configured. Plain
HTTP OIDC callback URLs are disabled. Proxy headers are deliberately not
trusted by default; audit IPs and rate limits will see the proxy address.
Before enabling trusted headers, verify Traefik's actual source addresses and
set `TRUST_PROXY` to those addresses/CIDRs in the Deployment. Do not blindly
trust every caller on the cluster network.

## Backups and recovery

Back up the whole `/app/data` volume and the encryption key together. Use a
consistent SQLite backup method or stop Pocket ID during a filesystem copy;
copying only the database while it is writing can omit WAL data. Keep backups
outside the cluster. Restore the original key before starting with restored
data. The Flux child has pruning disabled and deletion policy `Orphan`, but
these are not substitutes for backups. Never scale above one replica.

Upstream references:
[installation](https://pocket-id.org/docs/setup/installation),
[configuration and key rotation](https://pocket-id.org/docs/configuration/environment-variables).
