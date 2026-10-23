# Immich backup deployment and recovery

## Design

The `immich_backup` Ansible role backs up `/media/immich`, the private runtime
Compose configuration, and a deployment manifest to an encrypted Restic
repository on the Hetzner Storage Box. It uses SFTP on port 23 with a dedicated
root owned SSH configuration under `/etc/immich-backup`. It does not depend on
root's personal SSH configuration.

Immich stays running. The job requires a successfully readable gzip database
dump matching `backups/immich-db-backup-*.sql.gz` with a modification time
no older than 26 hours. It does not create a duplicate database dump. Dump
freshness and gzip validation do not prove that a SQL restore will succeed;
test recovery
periodically.

The existing database dump occurs at 02:00 UTC. Media backup runs at 03:00 UTC
with up to five minutes of jitter. This follows database first, media second
ordering but is not a strict point in time backup: intervening deletions can
leave database references without files. Strict consistency requires pausing
writes, creating a fresh dump, and capturing the media before allowing writes
again.

Retention keeps 7 daily, 5 weekly, and 12 monthly snapshots. Weekly maintenance
runs every Sunday at 03:00 Finnish local time (`Europe/Helsinki`), automatically
following daylight saving time, with no randomized delay. Finland skips the
03:00 hour when daylight saving time starts in spring, so that Sunday's calendar
occurrence may be skipped. It checks repository metadata before pruning and then
reads a random 1% of repository data. Set `immich_backup_read_data_percent` in
host variables to change the sample size (an integer from 1 to 100). Repeated
random
subsets do not guarantee full coverage. Schedule occasional full reads and
actual restore tests. Backup, maintenance, and manual wrapper commands share a
local lock and fail if another command is active. Snapshot listing bypasses the
local lock, allowing repository access verification during a backup. Restic's
own repository locking remains enabled for all commands.

There are no external notifications. Inspect systemd results regularly. The
first upload is approximately 48 GiB based on the current media contents.
Retention is not a storage quota; monitor the 1 TB Storage Box as the library
grows.

## Credentials

`infra/ansible/secrets/immich-backup.vault.yaml` is encrypted with the existing
Ansible Vault password. It contains a newly generated Restic repository password
and the existing Hetzner SSH private key imported from dockerhost. No plaintext
key or repository password belongs in Git. The entire secrets directory,
including this encrypted vault, is ignored by Git. Keep the vault in a private
password manager or separate private storage and retrieve it before deployment.

Keep an independent recovery copy of the encrypted vault, its unlock password,
and this repository. Do not depend solely on dockerhost or the backup
repository to recover its own credentials. The key is the existing Storage Box
account key,
not a newly restricted subaccount key. Consider a dedicated subaccount later.

To edit secrets from `infra/ansible`:

```sh
ansible-vault edit secrets/immich-backup.vault.yaml
```

Required variables are `immich_backup_repository_password` and optionally
`immich_backup_ssh_private_key`. If the latter is absent, deployment copies
`/home/taqi/.ssh/hetzner` on the target into the private backup configuration
directory. A passphrase protected key needs a separate unattended authentication
design; the role disables SSH agent use.

The pinned ED25519 host key in `inventory/host_vars/dockerhost.yaml` comes from
dockerhost's existing known hosts entry. Confirm its fingerprint against
Hetzner's published host key information before first deployment. The role never
automatically trusts `ssh-keyscan` output or replaces changed host keys.

## Deploy

Run from `infra/ansible` on the controller. Install Ansible if needed and ensure
the configured `~/.ansible_vault_pass` exists with mode 0600. Controller access
currently uses `/home/taqi/.ssh/homeserver`; adjust inventory if reproducing on
a different workstation.

Validate syntax:

```sh
ansible-playbook playbooks/configure-immich-backup.yaml --syntax-check
```

The role defaults to disabled timers. This repository's dockerhost inventory
explicitly enables them for the existing deployment. For a fresh installation,
override that setting to install without starting timers:

```sh
ansible-playbook playbooks/configure-immich-backup.yaml \
  --limit dockerhost --ask-become-pass \
  -e immich_backup_timers_enabled=false
```

Initialize the repository explicitly on dockerhost:

```sh
ssh -t dockerhost 'sudo /usr/local/sbin/immich-backup init'
```

Initialization leaves an accessible existing repository unchanged. If access
fails it attempts `restic init`, which fails rather than overwriting an existing
repository. Do not use this command to work around a lost password or
inaccessible Storage Box.

Run the first backup in a systemd service so closing SSH does not terminate it:

```sh
ssh -t dockerhost 'sudo systemctl start --no-block immich-backup.service'
ssh -t dockerhost 'sudo journalctl -fu immich-backup.service'
```

Exit the journal viewer with Ctrl+C. This does not stop the backup. After
completion, inspect the exit status and snapshots:

```sh
ssh -t dockerhost \
  'sudo systemctl show immich-backup.service -p Result -p ExecMainStatus'
ssh -t dockerhost 'sudo /usr/local/sbin/immich-backup snapshots'
```

After a successful first backup, enable timers in
`inventory/host_vars/dockerhost.yaml` (already enabled for the existing host):

```yaml
immich_backup_timers_enabled: true
```

Deploy again without the first installation's disabling override:

```sh
ansible-playbook playbooks/configure-immich-backup.yaml \
  --limit dockerhost --ask-become-pass
```

The role verifies repository access before enabling timers. By default timers
are disabled, and redeploying with that default stops them. Keep the setting in
inventory once ready rather than relying on a temporary command line override.
Persistent timers may run missed jobs shortly after activation or boot. If
backup and maintenance coincide, one fails on the lock rather than running
concurrently; inspect and rerun it manually.

Check operation:

```sh
ssh -t dockerhost 'sudo systemctl list-timers "immich-backup*"'
ssh -t dockerhost 'sudo journalctl -u immich-backup.service \
  -u immich-backup-maintenance.service --since "7 days ago"'
```

To pause future runs, set `immich_backup_timers_enabled: false` and redeploy.
This does not stop an already running backup or maintenance service. Do not
change the repository password in Vault after initialization without
explicitly managing Restic repository keys first.

## Browse snapshots and retrieve individual files

Run these commands on dockerhost. The wrapper supplies the repository address,
password file, and SSH configuration automatically. These examples do not modify
live Immich files.

### List snapshots and their contents

List completed snapshots, then browse a specific snapshot:

```sh
sudo /usr/local/sbin/immich-backup snapshots
sudo /usr/local/sbin/immich-backup ls SNAPSHOT_ID
sudo /usr/local/sbin/immich-backup ls SNAPSHOT_ID /media/immich/backups
```

Replace `SNAPSHOT_ID` with an ID from the snapshot list. You can also use
`latest` to inspect the newest snapshot:

```sh
sudo /usr/local/sbin/immich-backup ls latest /media/immich/backups
sudo /usr/local/sbin/immich-backup ls --long latest /media/immich/backups
sudo /usr/local/sbin/immich-backup ls --recursive latest /media/immich/library
```

`--long` adds details such as size and permissions. `--recursive` lists nested
directories too. Prefer an explicit snapshot ID for recovery so the selected
backup cannot change between browsing and restoring.

### Restore a single file

First find the exact stored path with `ls`. The photo path below is only an
example; replace it and `SNAPSHOT_ID` with real values from your snapshot:

```sh
file='/media/immich/library/USER_ID/2026/10/photo.jpg'
sudo /usr/local/sbin/immich-backup restore SNAPSHOT_ID \
  --target /srv/immich-file-restore \
  --include "$file"
```

The restored file retains its original path underneath the target directory:

```text
/srv/immich-file-restore/media/immich/library/USER_ID/2026/10/photo.jpg
```

The same approach works for a single database dump: set `file` to its exact
`/media/immich/backups/...sql.gz` path from the snapshot listing. Restoring a
dump to a temporary directory does not import it into PostgreSQL.

### Restore only the database dump directory

```sh
sudo /usr/local/sbin/immich-backup restore SNAPSHOT_ID \
  --target /srv/immich-dumps-restore \
  --include /media/immich/backups
```

The dumps appear under `/srv/immich-dumps-restore/media/immich/backups`. Inspect
restored files before using them. Never restore directly over the live
library, runtime configuration, or database as an initial recovery test.

### Use raw Restic instead of the wrapper

The equivalent snapshot listing command can be run directly. Keep the SSH
command in a variable to make the invocation easier to read:

```sh
sftp_command='ssh -F /etc/immich-backup/ssh_config hetzner-backup -s sftp'
sudo restic \
  --repo sftp:hetzner-backup:restic/immich \
  --password-file /etc/immich-backup/repository_password \
  -o "sftp.command=$sftp_command" \
  snapshots
```

Replace the final `snapshots` argument with `ls SNAPSHOT_ID` or a `restore`
command and its arguments to perform the same operations without the wrapper.
Raw Restic commands retain Restic's repository locking but bypass the wrapper's
local lock. Avoid running restores or checks alongside maintenance; the wrapper
normally prevents those local operations from overlapping.

## Recovery drill

Never initially restore over the live media or PostgreSQL directory.

1. Provision a replacement or isolated host. Mount the destination media
   filesystem. Retrieve the repository and independent vault unlock password.
2. Adjust inventory to target the recovery host. Disable timers and deploy the
   role. The vaulted key avoids needing the original user's SSH directory. This
   role installs backup tooling, not Docker or the Immich application itself.
3. Inspect available snapshots and choose an explicit snapshot ID:

   ```sh
   sudo /usr/local/sbin/immich-backup snapshots
   sudo /usr/local/sbin/immich-backup ls SNAPSHOT_ID
   sudo /usr/local/sbin/immich-backup restore SNAPSHOT_ID \
     --target /srv/immich-restore
   ```

4. The restore target contains the original absolute path hierarchy, for example
   `/srv/immich-restore/media/immich` and
   `/srv/immich-restore/home/taqi/docker/immich/.env`. Protect the restored
   environment file. Read the restored
   `/var/lib/immich-backup/deployment.txt` for recorded image versions and IDs.
5. Set up an isolated Immich deployment with compatible application and
   PostgreSQL images. Restore the media tree while preserving ownership and
   permissions. For external libraries, restore their files separately and
   reproduce their original container paths; this job only covers the configured
   media filesystem.
6. Restore the database dump using the official Immich procedure for the
   recorded version. Use a fresh database, not a file copy over a running
   PostgreSQL directory. The built in database restore interface is available
   in current
   Immich releases. See
   https://docs.immich.app/administration/backup-and-restore/
   and select matching version instructions where applicable.
7. Verify login, albums, original downloads, video playback, and several older
   assets before exposing the recovered deployment. Test a full data check when
   practical:

   ```sh
   sudo /usr/local/sbin/immich-backup check --read-data
   ```

The backup includes runtime configuration but not the working Git checkout.
Preserve the repository independently, and record the repository commit used
when deploying changes.

## Local tests

The script tests require Python, PyYAML, Jinja2, Bash, and standard Linux
utilities. They use mocked Docker and Restic commands and never contact the
Storage Box:

```sh
cd infra/ansible
python -m unittest discover -s tests -v
```
