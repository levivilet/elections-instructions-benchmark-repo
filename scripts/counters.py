"""Cgroup-scoped hardware counters; the controller stays outside the cgroup."""
import errno
import os
from pathlib import Path
import select
import signal
import subprocess
import time
import uuid


def parse_counter(raw):
    rows = [line.split(';') for line in raw.splitlines()
            if len(line.split(';')) >= 6 and line.split(';')[2].strip() == 'instructions']
    if len(rows) != 1:
        raise ValueError('expected exactly one instruction count')
    row = rows[0]
    try:
        count, runtime, running = int(row[0]), int(row[4]), float(row[5])
    except ValueError as error:
        raise ValueError('unsupported or uncounted hardware instructions') from error
    if count <= 0 or runtime <= 0 or not 99.9 <= running <= 100:
        raise ValueError('invalid or multiplexed instruction measurement')
    return count


class Capture:
    def __init__(self, perf, directory):
        self.group = Path('/sys/fs/cgroup') / ('startup-' + uuid.uuid4().hex)
        self.directory = Path(directory)
        self.perf = perf
        self.process = None
        self.fds = []
        self.children = []
        self.disabled_at = None

    def __enter__(self):
        self.group.mkdir()
        try:
            for name in ['control', 'ack']:
                path = self.directory / name
                os.mkfifo(path)
                self.fds.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            self.log = (self.directory / 'perf.log').open('w')
            self.process = subprocess.Popen([
                self.perf, 'stat', '-a', '-e', 'instructions', '-G', self.group.name,
                '-x', ';', '-D', '-1', '--control', f'fd:{self.fds[0]},{self.fds[1]}',
                ], pass_fds=tuple(self.fds),
                stdout=subprocess.DEVNULL, stderr=self.log, start_new_session=True)
            self.control('enable')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def control(self, command):
        os.write(self.fds[0], (command + '\n').encode())
        if not select.select([self.fds[1]], [], [], 5)[0]:
            raise RuntimeError(f'perf did not acknowledge {command}')
        # perf 6.8 writes sizeof("ack\n"), including the C NUL terminator.
        if os.read(self.fds[1], 256) not in (b'ack\n', b'ack\n\x00'):
            raise RuntimeError('invalid perf control acknowledgement')
        if self.process.poll() is not None:
            raise RuntimeError('perf exited before cutoff')

    def launch(self, command, environment, stdout, stderr, cwd=None):
        # Only the launcher child enters the cgroup; controller, X server,
        # desktop, readiness polling and perf remain outside it.
        def enter_group():
            (self.group / 'cgroup.procs').write_text(str(os.getpid()))
            if 'SUDO_UID' in environment:
                os.setgroups([])
                os.setgid(int(environment['SUDO_GID']))
                os.setuid(int(environment['SUDO_UID']))
        child = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stdout, stderr=stderr,
                                 preexec_fn=enter_group, start_new_session=True)
        self.children.append(child)
        return child

    def pids(self):
        return [int(pid) for pid in (self.group / 'cgroup.procs').read_text().split()]

    def cutoff(self):
        self.control('disable')
        self.disabled_at = time.monotonic()

    def start(self):
        if self.disabled_at is None:
            raise RuntimeError('capture must be disabled before selection starts')
        self.control('enable')
        self.disabled_at = None

    def finish(self):
        if self.disabled_at is None:
            raise RuntimeError('capture has no acknowledged cutoff')
        self.process.send_signal(signal.SIGINT)
        self.process.wait(timeout=5)
        self.log.flush()
        raw = (self.directory / 'perf.log').read_text()
        return {'instructions': parse_counter(raw), 'rawCounter': raw,
                'source': 'perf stat instructions, per-CPU cgroup', 'cutoffAcknowledged': True}

    def __exit__(self, *args):
        # Disable before killing anything, including when readiness fails.
        try:
            if self.process and self.process.poll() is None:
                if self.disabled_at is None:
                    try:
                        self.cutoff()
                    except (OSError, RuntimeError):
                        pass
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
            if self.group.exists():
                (self.group / 'cgroup.kill').write_text('1')
                # Reap launcher zombies before rmdir: they can keep the cgroup
                # busy even after cgroup.procs no longer lists live tasks.
                for child in self.children:
                    child.wait(timeout=5)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        self.group.rmdir()
                        break
                    except OSError as error:
                        if error.errno != errno.EBUSY or time.monotonic() >= deadline:
                            raise
                        time.sleep(.01)
        finally:
            if self.process:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for fd in self.fds:
                os.close(fd)
            if hasattr(self, 'log'):
                self.log.close()
