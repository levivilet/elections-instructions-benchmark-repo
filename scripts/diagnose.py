"""Separate idle controls and sampled profiles; never inputs to the official report."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess

from benchmark import ROOT, PROTOCOL, trial, selection_evidence_is_valid
from counters import Capture


class ProfileCapture(Capture):
    def __enter__(self):
        self.group.mkdir()
        try:
            for name in ['control', 'ack']:
                path = self.directory / name
                os.mkfifo(path)
                self.fds.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            self.log = (self.directory / 'perf.log').open('w')
            self.process = subprocess.Popen([
                self.perf, 'record', '-a', '-e', 'instructions', '-c', '100000',
                '-G', self.group.name, '--call-graph', 'dwarf,8192', '-D', '-1',
                '--tail-synthesize',
                '--control', f'fd:{self.fds[0]},{self.fds[1]}',
                '-o', str(self.directory / 'perf.data'),
            ], pass_fds=tuple(self.fds), stdout=subprocess.DEVNULL,
                stderr=self.log, start_new_session=True)
            self.control('ping', timeout=30)
            self.control('enable')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def launch(self, command, environment, stdout, stderr, cwd=None):
        # Resolve V8 generated code in the sampled profile. This extra work is
        # deliberately restricted to diagnostic trials, never official counts.
        command = [command[0], '--js-flags=--perf-basic-prof', *command[1:]]
        return super().launch(command, environment, stdout, stderr, cwd)

    def finish(self):
        if self.disabled_at is None:
            raise RuntimeError('profile has no acknowledged cutoff')
        destination = Path(os.environ['SELECTION_PROFILE_DESTINATION'])
        destination.mkdir(parents=True)
        processes = []
        for pid in self.pids():
            process = Path('/proc') / str(pid)
            try:
                processes.append({'pid': pid, 'command': process.joinpath('cmdline').read_bytes().decode().split('\0'),
                                  'threads': {task.name: (task / 'comm').read_text().strip()
                                              for task in (process / 'task').iterdir()}})
            except FileNotFoundError:
                continue
        (destination / 'processes.json').write_text(json.dumps(processes, indent=2) + '\n')
        # Startup was excluded while counters were disabled. Synthesize the
        # surviving processes' comm/mmap records at the tail so their sampled
        # instructions can still be attributed and unwound.
        self.process.send_signal(signal.SIGINT)
        self.process.wait(timeout=10)
        self.log.flush()
        # perf finalizes data, then re-raises SIGINT on some supported versions.
        if self.process.returncode not in (0, -signal.SIGINT):
            raise RuntimeError((self.directory / 'perf.log').read_text())
        # Resolve symbols while the editor and its temporary mapped files live.
        for name, args in [
            ('report.txt', ['report', '--stdio', '--no-children', '--percent-limit', '0.5',
                            '--sort', 'comm,dso,symbol']),
            ('stacks.txt', ['script', '-F', 'comm,pid,tid,time,event,ip,sym,dso']),
        ]:
            with (destination / name).open('w') as output:
                subprocess.run([self.perf, *args, '-i', str(self.directory / 'perf.data')],
                               stdout=output, stderr=subprocess.STDOUT, check=True)
        if not (destination / 'stacks.txt').read_text().strip():
            raise RuntimeError('instruction profile contains no samples')
        for name in ['perf.data', 'perf.log']:
            shutil.move(str(self.directory / name), str(destination / name))
            # perf creates root-only data when cgroup capture runs via sudo.
            # These profiles contain only the synthetic benchmark document.
            (destination / name).chmod(0o644)
        return {'diagnosticProfile': str(destination), 'cutoffAcknowledged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce', 'codemirror5'], required=True)
    parser.add_argument('--perf', required=True)
    args = parser.parse_args()
    if platform.machine() != 'aarch64':
        parser.error('requires the official Linux ARM64 runner')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text())
                  if e['id'] == args.editor)
    destination = Path('results/diagnostics').resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output = {'editor': editor, 'protocol': PROTOCOL,
              'note': 'Diagnostic only. Sampling overhead and idle counts are not official selection results.',
              'idle': [], 'profiles': []}
    output_path = destination / 'diagnostics.json'
    for kind, options in [('idle', {'idle': True}), ('profiles', {'capture_type': ProfileCapture})]:
        for repeat in range(1, 4):
            os.environ['SELECTION_PROFILE_DESTINATION'] = str(destination / f'profile-{repeat}')
            result = dict(trial(editor, args.perf, **options), repeat=repeat)
            output[kind].append(result)
            output_path.write_text(json.dumps(output, indent=2) + '\n')
            if not result['valid'] or (kind == 'profiles' and not selection_evidence_is_valid(result)):
                raise SystemExit(f'invalid {kind} diagnostic: {result}')


if __name__ == '__main__':
    main()
