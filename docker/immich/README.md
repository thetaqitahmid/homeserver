# Immich on dockerhost

The stack in `docker/immich.yaml` follows the official Immich v3.3.3 release Compose file:
https://github.com/immich-app/immich/releases/tag/v3.3.3

## Storage and networking

| Purpose | Host path |
| :--- | :--- |
| Originals, thumbnails, encoded videos, and automatic database dumps | `/media/immich` |
| PostgreSQL | `/home/taqi/docker/immich/postgres` |
| Machine learning models | `/home/taqi/docker/immich/model-cache` |
| Valkey append-only persistence | `/home/taqi/docker/immich/redis` |
| Runtime Compose file | `/home/taqi/docker/immich/compose.yaml` |
| Runtime environment and database password | `/home/taqi/docker/immich/.env` |

Only the server joins the existing `t3_proxy` network. No Immich ports are published on the host. Traefik routes `immich.docker.tahmidcloud.com` to port 2283 and uses the existing `dns-cloudflare` ACME resolver. PostgreSQL, Valkey, and machine learning remain on the stack network.

The VM has 8 GiB RAM and four virtual CPUs. Its root disk had about 23 GiB available after installation. Monitor free space as PostgreSQL, model downloads, and Docker images grow.

## Initial setup

Run on dockerhost, with this repository checked out:

```sh
umask 077
mkdir -p /home/taqi/docker/immich
cp docker/immich.env.example /home/taqi/docker/immich/.env
openssl rand -hex 32
```

Replace the example database password with the generated value. Never commit the runtime environment file. Set the timezone if needed.

Verify that `/media/immich` is the intended mounted filesystem, not an empty directory on the root disk:

```sh
mountpoint /media/immich
findmnt /media/immich
df -h /media/immich
cp docker/immich.yaml /home/taqi/docker/immich/compose.yaml
```

Back up `/etc/exports`, remove only the `/media/immich` export, and run `sudo exportfs -ra`. Leave the `/media/flexdrive` export intact.

Install the Docker mount dependency before enabling automatic startup:

```sh
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo install -m 644 docker/immich/docker-mount.conf /etc/systemd/system/docker.service.d/immich-mount.conf
sudo systemctl daemon-reload
```

This affects the entire Docker daemon: Docker will not start if `/media/immich` is unavailable. It protects automatic container restarts from writing media into the VM root filesystem. It does not guard against manually unmounting the disk while Docker is already running.

Deploy:

```sh
cd /home/taqi/docker/immich
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 300
docker compose ps
```

## Deployment findings

The Immich server, machine learning, PostgreSQL, and Valkey are healthy. Machine learning requires x86-64-v2 CPU instructions. VM 103 now uses the Proxmox `x86-64-v2-AES` CPU model, applied with a full shutdown and start, which resolved the previous NumPy CPU compatibility error. Automatic VM startup is enabled, and the QEMU guest agent is installed and responding.

For a fresh VM, select a compatible CPU model before deployment. Changing the CPU model on an existing VM requires a full shutdown and start and interrupts its other Docker workloads.

Validate the stack after deployment:

```sh
cd /home/taqi/docker/immich
docker compose up -d --wait --wait-timeout 300
docker compose logs --tail 100 immich-machine-learning
```

The existing Traefik bind source for `traefik-rules.yaml` was an empty directory rather than a file. It was backed up and replaced with the repository rules. The rules also needed `stsSeconds` instead of `stsMaxAge`, and a named `tls-opts` definition matching the static configuration.

Because the original Traefik Compose files were absent on the VM, its existing runtime configuration was preserved in `/home/taqi/docker/traefik/compose.json` and used to recreate the container with the corrected bind mount. This file and `inspect-before-immich.json` contain existing credentials and must remain private. Both were created with restrictive permissions.

Direct access through Docker Traefik passes certificate verification, returns HTTP 200 for the homepage, and returns `{"res":"pong"}` for the API:

```sh
curl --fail --resolve immich.docker.tahmidcloud.com:443:192.168.1.135 \
  https://immich.docker.tahmidcloud.com/api/server/ping
```

Normal DNS resolves to the public address, where testing received a Traefik default certificate rather than Docker Traefik's valid wildcard certificate. Portainer's Docker hostname has the same problem. The public entry point must route `.docker.tahmidcloud.com` TLS traffic to `192.168.1.135:443`. The repository HAProxy example under `infra/haproxy` describes this architecture, but its live configuration was not changed. Alternatively, use a local DNS override for LAN access.

Create the first administrator account once routing is correct. No account was created during deployment.

## Backups

Back up the entire `/media/immich` filesystem, including completed database dumps in `backups/`, to a separate destination. Automatic database dumps do not protect against failure of that disk. Verify that a recent dump completed before backing up the media. A separate dump is unnecessary if the built in backups are current and successful. For a consistent backup, stop the Immich server while creating the dump and capturing the media; if it stays running, follow the database first, media second ordering recommended by Immich.

Preserve the private environment file securely outside Git and record the repository commit and deployed image versions with each backup. Restore with compatible application and database images before upgrading. Do not rely on copying a live PostgreSQL data directory as a consistent database backup. Test restoration into an isolated deployment.
