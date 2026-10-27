"""Observe native input-to-selection DOM separately from official counters."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess

from benchmark import ROOT, PROTOCOL, trial, selection_evidence_is_valid
from counters import Capture


class ResponseCapture(Capture):
    def launch(self, command, environment, stdout, stderr, cwd=None):
        return super().launch([command[0], '--remote-debugging-port=9222', *command[1:]],
                              environment, stdout, stderr, cwd)

    def start(self):
        destination = Path(os.environ['SELECTION_RESPONSE_DESTINATION'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.observer_log = destination.with_suffix('.log').open('w')
        self.observer = subprocess.Popen(
            ['node', str(ROOT / 'scripts/observe-response.mjs'), str(destination),
             os.environ['SELECTION_RESPONSE_EDITOR']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.observer_log, text=True)
        if (not select.select([self.observer.stdout], [], [], 20)[0]
                or self.observer.stdout.readline().strip() != 'ready'):
            raise RuntimeError('response observer failed to attach; see diagnostic log')
        super().start()

    def finish(self):
        result = super().finish()
        self.observer.stdin.write('\n')
        self.observer.stdin.flush()
        if self.observer.wait(timeout=20) != 0:
            raise RuntimeError('response observer failed; see diagnostic log')
        result['response'] = json.loads(Path(os.environ['SELECTION_RESPONSE_DESTINATION']).read_text())
        if result['response'].get('inputTrusted') is not True:
            raise RuntimeError('response observation did not receive trusted input')
        return result

    def __exit__(self, *args):
        try:
            if hasattr(self, 'observer'):
                if self.observer.poll() is None:
                    self.observer.kill()
                self.observer.wait(timeout=5)
                self.observer.stdin.close()
                self.observer.stdout.close()
            if hasattr(self, 'observer_log'):
                self.observer_log.close()
        finally:
            super().__exit__(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--editor', choices=['lvce', 'codemirror5'], required=True)
    args = parser.parse_args()
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text())
                  if e['id'] == args.editor)
    destination = Path('results/diagnostics/responses').resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output = {'editor': editor, 'protocol': PROTOCOL, 'trials': [],
              'note': 'Diagnostic response observer, not an official instruction result.'}
    os.environ['SELECTION_RESPONSE_EDITOR'] = args.editor
    for repeat in range(1, 4):
        os.environ['SELECTION_RESPONSE_DESTINATION'] = str(destination / f'{args.editor}-{repeat}.json')
        result = dict(trial(editor, args.perf, capture_type=ResponseCapture),
                      repeat=repeat, diagnosticResponse=True)
        output['trials'].append(result)
        (destination / f'{args.editor}.json').write_text(json.dumps(output, indent=2) + '\n')
        if not result['valid'] or not selection_evidence_is_valid(result):
            raise RuntimeError(f'invalid response observation: {result}')


if __name__ == '__main__':
    main()
