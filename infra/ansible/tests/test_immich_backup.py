import gzip
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles' / 'immich_backup'


def render_template(name, settings):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROLE / 'templates'),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    environment.filters['quote'] = lambda value: shlex.quote(str(value))
    return environment.get_template(name).render(**settings)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'media with spaces'
        self.dumps = self.source / 'backups'
        self.compose = self.root / 'compose'
        self.state = self.root / 'state'
        self.bin = self.root / 'bin'
        for directory in (self.dumps, self.compose, self.state, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        self.dump = self.dumps / 'immich-db-backup-test.sql.gz'
        self.dump.write_bytes(gzip.compress(b'database dump'))
        for filename in ('compose.yaml', '.env'):
            (self.compose / filename).write_text('configuration')
        self.settings = yaml.safe_load((ROLE / 'defaults/main.yaml').read_text())
        self.settings.update(
            immich_backup_source=str(self.source),
            immich_backup_compose_dir=str(self.compose),
            immich_backup_state_dir=str(self.state),
        )
        self.script = self.root / 'backup.sh'
        self.script.write_text(render_template('backup.sh.j2', self.settings))
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', LOG=str(self.root / 'commands'))
        self.mock('mountpoint', 'exit "${MOUNT_STATUS:-0}"')
        self.mock('docker', 'printf "container image digest\\n"')
        self.mock('restic', '''printf '%s\\n' "$*" >> "$LOG"
shift 2
case "$1" in
    snapshots) exit "${SNAPSHOTS_STATUS:-0}" ;;
    backup) exit "${BACKUP_STATUS:-0}" ;;
    check) exit "${CHECK_STATUS:-0}" ;;
esac
''')

    def mock(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
        path.chmod(0o700)

    def run_backup(self, *args):
        return subprocess.run(['bash', str(self.script), *args], env=self.env, capture_output=True, text=True)

    def commands(self):
        log = self.root / 'commands'
        return log.read_text() if log.exists() else ''

    def test_repository_initialization_and_timers_are_opt_in(self):
        self.assertFalse(self.settings['immich_backup_initialize'])
        self.assertFalse(self.settings['immich_backup_timers_enabled'])
        host_settings = yaml.safe_load(
            (ROOT / 'inventory/host_vars/dockerhost.yaml').read_text()
        )
        self.assertTrue(host_settings['immich_backup_timers_enabled'])

    def test_recovery_documentation_line_lengths(self):
        document = ROOT.parents[1] / 'docker/immich/restore.md'
        for number, line in enumerate(document.read_text().splitlines(), 1):
            if not line.strip().startswith(('https://', 'http://')):
                self.assertLessEqual(len(line), 80, f'Line {number}')

    def test_script_syntax(self):
        result = subprocess.run(['bash', '-n', str(self.script)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_successful_backup_includes_configuration_and_metadata(self):
        result = self.run_backup('backup')
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.source, self.compose / '.env', self.compose / 'compose.yaml', self.state / 'deployment.txt'):
            self.assertIn(str(path), self.commands())
        self.assertIn('--tag immich', self.commands())
        self.assertIn('container image digest', (self.state / 'deployment.txt').read_text())

    def test_missing_mount_is_rejected(self):
        self.env['MOUNT_STATUS'] = '1'
        self.assertNotEqual(self.run_backup('backup').returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_missing_dump_is_rejected(self):
        self.dump.unlink()
        self.assertNotEqual(self.run_backup('backup').returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_stale_and_future_dumps_are_rejected(self):
        for timestamp in (time.time() - 100000, time.time() + 3600):
            with self.subTest(timestamp=timestamp):
                os.utime(self.dump, (timestamp, timestamp))
                self.assertNotEqual(self.run_backup('backup').returncode, 0)
                self.assertEqual(self.commands(), '')

    def test_corrupt_dump_is_rejected(self):
        self.dump.write_bytes(b'not gzip')
        self.assertNotEqual(self.run_backup('backup').returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_missing_private_configuration_is_rejected(self):
        (self.compose / '.env').unlink()
        self.assertNotEqual(self.run_backup('backup').returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_incomplete_restic_backup_is_a_failure(self):
        self.env['BACKUP_STATUS'] = '3'
        self.assertEqual(self.run_backup('backup').returncode, 3)

    def test_existing_repository_is_not_initialized_again(self):
        self.assertEqual(self.run_backup('init').returncode, 0)
        self.assertNotIn(' init', self.commands())

    def test_explicit_initialization(self):
        self.env['SNAPSHOTS_STATUS'] = '1'
        self.assertEqual(self.run_backup('init').returncode, 0)
        self.assertIn(' init', self.commands())

    def test_failed_integrity_check_prevents_pruning(self):
        self.env['CHECK_STATUS'] = '1'
        self.assertNotEqual(self.run_backup('maintenance').returncode, 0)
        self.assertNotIn('forget', self.commands())

    def test_maintenance_filters_retention_and_reads_data(self):
        self.assertEqual(self.run_backup('maintenance').returncode, 0)
        self.assertIn('forget --host immich-dockerhost --tag immich', self.commands())
        self.assertIn('--read-data-subset=1%', self.commands())

    def test_maintenance_timer_uses_finnish_time_without_jitter(self):
        timer = render_template('immich-backup-maintenance.timer.j2', self.settings)
        self.assertIn('OnCalendar=Sun *-*-* 03:00:00 Europe/Helsinki', timer)
        self.assertIn('AccuracySec=1s', timer)
        self.assertNotIn('RandomizedDelaySec', timer)

    def test_maintenance_sample_size_is_configurable(self):
        self.settings['immich_backup_read_data_percent'] = 5
        self.script.write_text(render_template('backup.sh.j2', self.settings))
        self.assertEqual(self.run_backup('maintenance').returncode, 0)
        self.assertIn('--read-data-subset=5%', self.commands())

    def test_overlapping_jobs_are_rejected(self):
        with (self.state / 'job.lock').open('w') as lock:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_backup('backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_snapshot_listing_succeeds_during_an_active_backup(self):
        with (self.state / 'job.lock').open('w') as lock:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_backup('snapshots')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('snapshots', self.commands())

    def test_snapshot_listing_propagates_repository_errors(self):
        self.env['SNAPSHOTS_STATUS'] = '1'
        self.assertEqual(self.run_backup('snapshots').returncode, 1)

    def test_restore_arguments_are_forwarded(self):
        result = self.run_backup('restore', 'latest', '--target', '/tmp/recovery')
        self.assertEqual(result.returncode, 0)
        self.assertIn('restore latest --target /tmp/recovery', self.commands())


if __name__ == '__main__':
    unittest.main()
