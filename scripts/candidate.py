"""Measure an explicitly identified worker bundle without publishing it as a release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from benchmark import ROOT, PROTOCOL, trial, selection_evidence_is_valid
from counters import parse_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--label', choices=['baseline', 'candidate'], default='candidate')
    args = parser.parse_args()
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == 'lvce')
    targets = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/editor-worker/dist/editorWorkerMain.js'))
    if len(targets) != 1:
        raise RuntimeError(f'expected one installed editor bundle, got {targets}')
    target = targets[0]
    output = {'editor': editor, 'protocol': PROTOCOL, 'workerRevision': args.revision,
              'bundleSha256': hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
              'note': 'Diagnostic candidate, not the pinned release; excluded from the official report.', 'trials': []}
    destination = Path(f'results/diagnostics/{args.label}.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='selection-candidate-backup-') as directory:
        backup = Path(directory) / 'editorWorkerMain.js'
        shutil.copyfile(target, backup)
        try:
            shutil.copyfile(args.bundle, target)
            for repeat in range(1, 4):
                result = dict(trial(editor, args.perf), repeat=repeat, diagnosticCandidate=args.revision)
                output['trials'].append(result)
                destination.write_text(json.dumps(output, indent=2) + '\n')
                if (not result['valid'] or not selection_evidence_is_valid(result)
                        or parse_counter(result.get('rawCounter', '')) != result.get('instructions')):
                    raise RuntimeError(f'invalid candidate trial: {result}')
        finally:
            shutil.copyfile(backup, target)


if __name__ == '__main__':
    main()
