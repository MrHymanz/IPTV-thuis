#!/usr/bin/env python3
"""LibreELEC updater: public GitHub source, tested commits, local private data."""
import argparse
from contextlib import closing
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULTS = {
    'repository': 'MrHymanz/IPTV-thuis', 'branch': 'main',
    'workflow': '.github/workflows/validate.yml', 'recording_guard_seconds': 600,
}
SHA = re.compile(r'^[0-9a-f]{40}$')
DOCKER = '/storage/.kodi/addons/service.system.docker/bin/docker'


class Deferred(Exception):
    pass


def command(args, timeout=180):
    try:
        return subprocess.run([str(a) for a in args], check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError('Opdracht mislukt: ' + str(args[0])) from None


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def claim_update(database, guard=600, now=None):
    """Serialize with recording starts/scheduling. Return False rather than interrupt."""
    now = time.time() if now is None else now
    with closing(sqlite3.connect(database, timeout=10)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("""SELECT 1 FROM recordings WHERE state='recording'
                      OR (state='queued' AND start<? AND end>?) LIMIT 1""", (now + guard, now)).fetchone():
            return False
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('update_lease_until', str(now + 300)))
    return True


def clear_lease(database):
    with closing(sqlite3.connect(database, timeout=10)) as db, db:
        db.execute("DELETE FROM settings WHERE key='update_lease_until'")


def unpack_archive(data, destination):
    """GitHub archives have one root directory. Never follow archive links."""
    total = 0
    root = None
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 20000:
            raise ValueError('Bronarchief bevat te veel bestanden.')
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith('/') or '..' in parts:
                raise ValueError('Ongeldig archiefpad.')
            if root is None:
                root = parts[0]
            if parts[0] != root or not (member.isfile() or member.isdir()):
                raise ValueError('Ongeldig archieftype.')
            total += member.size
            if total > 256 * 1024 * 1024:
                raise ValueError('Bronarchief is te groot.')
            target = destination.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600 | (member.mode & 0o111))


class Updater:
    def __init__(self, base, run=command):
        self.base = Path(base)
        self.run = run
        self.database = self.base / 'data/mijntv.db'
        self.config = dict(DEFAULTS)
        self.config.update(json.loads((self.base / 'updates.json').read_text()))
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.config['repository']):
            raise ValueError('Ongeldige GitHub-repository.')
        guard = self.config['recording_guard_seconds']
        if isinstance(guard, bool) or not isinstance(guard, int) or guard < 600:
            raise ValueError('De opnamebuffer moet minimaal 600 seconden zijn.')
        self.state_path = self.base / 'update-state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def docker(self, *args, timeout=180):
        return self.run([DOCKER, *args], timeout=timeout)

    def get(self, url, limit=64 * 1024 * 1024):
        headers = {'User-Agent': 'IPTV-thuis-updater', 'Accept': 'application/vnd.github+json'}
        with urlopen(Request(url, headers=headers), timeout=60) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Download is te groot.')
        return data

    def candidate(self):
        repo = self.config['repository']
        head = json.loads(self.get('https://api.github.com/repos/' + repo + '/commits/' +
                                   quote(self.config['branch'], safe=''), limit=1024 * 1024))
        revision = head.get('sha', '')
        if not SHA.fullmatch(revision):
            raise ValueError('GitHub gaf geen geldige commit terug.')
        if revision == self.state.get('revision'):
            raise Deferred('Deze versie is al geïnstalleerd.')
        if revision == self.state.get('failed_revision'):
            raise Deferred('Deze versie is eerder teruggedraaid; wacht op een nieuwe commit.')
        runs = json.loads(self.get('https://api.github.com/repos/' + repo +
                                  '/actions/runs?head_sha=' + revision + '&event=push&per_page=100',
                                  limit=4 * 1024 * 1024))
        if not any(run.get('head_sha') == revision and run.get('path') == self.config['workflow']
                   and run.get('event') == 'push' and run.get('head_branch') == self.config['branch']
                   and run.get('status') == 'completed' and run.get('conclusion') == 'success'
                   for run in runs.get('workflow_runs', [])):
            raise Deferred('De centrale tests/build zijn nog niet geslaagd voor deze commit.')
        return revision

    def idle(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            now = time.time()
            return not db.execute("""SELECT 1 FROM recordings WHERE state='recording'
                OR (state='queued' AND start<? AND end>?) LIMIT 1""",
                (now + self.config['recording_guard_seconds'], now)).fetchone()

    def healthy(self, timeout=120):
        deadline = time.monotonic() + timeout
        startup_deadline = time.monotonic() + 15
        was_running = False
        while time.monotonic() < deadline:
            try:
                info = json.loads(self.docker('inspect', 'iptv-thuis-tv'))[0]['State']
            except (RuntimeError, KeyError, ValueError):
                return False
            running = bool(info.get('Running'))
            if not running and (was_running or time.monotonic() >= startup_deadline):
                return False
            was_running = was_running or running
            if info.get('Health', {}).get('Status') == 'unhealthy':
                return False
            if running and info.get('Health', {}).get('Status') == 'healthy':
                return True
            time.sleep(2)
        return False

    def build(self, revision):
        folder = self.base / 'updates' / revision
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, mode=0o700)
        data = self.get('https://codeload.github.com/' + self.config['repository'] + '/tar.gz/' + revision)
        unpack_archive(data, folder)
        image = 'iptv-thuis-tv:git-' + revision[:12]
        self.docker('build', '--build-arg', 'VCS_REF=' + revision, '-f',
                    folder / 'deploy/libreelec/Dockerfile', '-t', image, folder, timeout=3600)
        return folder, image

    def install(self, revision, folder, image):
        if not claim_update(self.database, self.config['recording_guard_seconds']):
            raise Deferred('Tijdens het bouwen is een opname gestart of dichterbij gekomen.')
        previous = 'iptv-thuis-tv-previous'
        failed = 'iptv-thuis-tv-failed'
        stopped = False
        renamed = False
        snapshot_ready = False
        old_state = dict(self.state)
        snapshot = self.base / 'database-before-update.sqlite'
        # Kept on the same private, local data partition; never uploaded.
        try:
            info = json.loads(self.docker('inspect', 'iptv-thuis-tv'))[0]
            audio = next((v.split('=', 1)[1] for v in info['Config'].get('Env', [])
                          if v.startswith('IPTV_AUDIO_DEVICE=')), 'auto')
            self.run(['systemctl', 'stop', 'iptv-thuis.service'])
            stopped = True
            # No database writers remain after stopping the service.
            snapshot.unlink(missing_ok=True)
            with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            snapshot.chmod(0o600)
            snapshot_ready = True
            for name in [previous, failed]:
                try:
                    self.docker('rm', name)
                except RuntimeError:
                    pass  # Never force-remove a running container.
            self.docker('rename', 'iptv-thuis-tv', previous)
            renamed = True
            env = dict(os.environ, IPTV_IMAGE=image, IPTV_AUDIO_DEVICE=audio)
            try:
                subprocess.run(['/bin/sh', str(folder / 'deploy/libreelec/create-container.sh')],
                               env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            except (subprocess.SubprocessError, OSError):
                raise RuntimeError('De nieuwe tv-container kon niet worden aangemaakt.') from None
            self.run(['systemctl', 'start', 'iptv-thuis.service'])
            if not self.healthy():
                raise RuntimeError('De nieuwe tv-app startte niet gezond op.')
            # An updated updater/launcher becomes active on the next timer run.
            for name, source in [('update.py', 'update.py'), ('run-tv.sh', 'run-tv.sh')]:
                destination = self.base / name
                temporary = self.base / (name + '.new')
                shutil.copyfile(folder / 'deploy/libreelec' / source, temporary)
                temporary.chmod(0o700)
                temporary.replace(destination)
            self.state = {'revision': revision, 'updated_at': int(time.time())}
            write_json(self.state_path, self.state)
            print('Update geïnstalleerd: ' + revision[:12], flush=True)
        except Exception:
            if stopped:
                self.run(['systemctl', 'stop', 'iptv-thuis.service'])
                if renamed:
                    try:
                        self.docker('rename', 'iptv-thuis-tv', failed)
                    except RuntimeError:
                        pass
                    self.docker('rename', previous, 'iptv-thuis-tv')
                if snapshot_ready:
                    for suffix in ['-wal', '-shm']:
                        Path(str(self.database) + suffix).unlink(missing_ok=True)
                    shutil.copyfile(snapshot, self.database)
                    self.database.chmod(0o600)
                # The launcher may have fallen back to Kodi on a failed healthcheck.
                self.run(['systemctl', 'stop', 'kodi.service'])
                (self.base / 'enabled').touch()
                self.run(['systemctl', 'start', 'iptv-thuis.service'])
                if not self.healthy():
                    raise RuntimeError('Ook de vorige versie kon niet gezond opstarten; controleer de tv.') from None
            self.state = old_state
            self.state['failed_revision'] = revision
            write_json(self.state_path, self.state)
            raise
        finally:
            clear_lease(self.database)

    def update(self):
        if not (self.base / 'enabled').exists():
            raise Deferred('De zelfstandige tv-app is uitgeschakeld.')
        try:
            active = self.run(['systemctl', 'is-active', 'iptv-thuis.service'])
        except RuntimeError:
            active = ''
        if active != 'active':
            raise Deferred('De tv-service is niet actief; automatisch openen wordt overgeslagen.')
        revision = self.candidate()
        if not self.idle():
            raise Deferred('Er loopt een opname of er begint er binnen tien minuten een.')
        required = 4 * 1024**3 + self.database.stat().st_size
        if shutil.disk_usage(self.base).free < required:
            raise Deferred('Onvoldoende vrije ruimte voor een veilige build en databasebackup.')
        folder, image = self.build(revision)
        self.install(revision, folder, image)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='/storage/iptv-thuis')
    args = parser.parse_args()
    base = Path(args.base)
    os.umask(0o077)
    with (base / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Er loopt al een updatecontrole.')
            return
        try:
            Updater(base).update()
        except Deferred as error:
            print(str(error), flush=True)
        except Exception as error:
            # Never print request URLs, Docker environment or account-bearing exceptions.
            print('Update mislukt; bestaande installatie behouden/hersteld (' + type(error).__name__ + ').', flush=True)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
