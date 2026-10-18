"""Hardware acceptance: detached descendants, cutoff and controller exclusion."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from counters import Capture


def measure(mode, after=0, harness=False):
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)
        if 'SUDO_UID' in os.environ:
            os.chown(d, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
        try:
            return capture_workload(d, mode, after, harness)
        except BaseException:
            log = d / 'perf.log'
            if log.exists():
                Path('results/accounting-failure.log').write_text(log.read_text())
            raise


def capture_workload(d, mode, after, harness):
    with Capture(sys.argv[1], d) as capture:
        with (d / 'stdout').open('w') as stdout, (d / 'stderr').open('w') as stderr:
            child = capture.launch([str(Path('.tmp/workload').resolve()), mode, '10000000',
                                    str(d / 'ready'), str(d / 'go'), str(after)],
                                   os.environ.copy(), stdout, stderr)
        deadline = time.monotonic() + 10
        while not (d / 'ready').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('workload readiness timeout')
            time.sleep(.005)
        if harness:
            subprocess.run([sys.executable, '-c', 'sum(i*i for i in range(3000000))'], check=True)
        capture.cutoff()
        (d / 'go').touch()
        child.wait(timeout=10)
        return capture.finish()


Path('results').mkdir(exist_ok=True)
subprocess.run(['cc', '-O2', 'tests/workload.c', '-o', '.tmp/workload'], check=True)
results = {'direct': measure('direct'), 'detached': measure('detached'),
           'postCutoff': measure('direct', after=100000000),
           'busyController': measure('direct', harness=True)}
baseline = results['direct']['instructions']
for name, result in results.items():
    # Identical 10M loop must dominate every capture. An uncounted detached
    # child, counted 100M shutdown loop or counted Python harness violates this.
    result['ratioToDirect'] = result['instructions'] / baseline
    if not .9 < result['ratioToDirect'] < 1.1:
        Path('results/accounting.json').write_text(json.dumps(results, indent=2))
        raise SystemExit(f'{name}: accounting mismatch {result["ratioToDirect"]}')
Path('results/accounting.json').write_text(json.dumps(results, indent=2) + '\n')
print('Detached descendants, cutoff and harness exclusion validated')
