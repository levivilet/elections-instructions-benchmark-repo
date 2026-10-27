"""Measure an explicitly identified worker bundle without publishing it as a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from benchmark import ROOT, PROTOCOL, trial, selection_evidence_is_valid
from counters import parse_counter
from diagnose import ProfileCapture
from response import ResponseCapture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--label', choices=['baseline', 'dependency', 'token', 'property', 'flat', 'candidate', 'response', 'allocations'], default='candidate')
    parser.add_argument('--virtual-dom-revision')
    observers = parser.add_mutually_exclusive_group()
    observers.add_argument('--response', action='store_true')
    observers.add_argument('--allocation-profile', action='store_true')
    args = parser.parse_args()
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == 'lvce')
    targets = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/editor-worker/dist/editorWorkerMain.js'))
    if len(targets) != 1:
        raise RuntimeError(f'expected one installed editor bundle, got {targets}')
    target = targets[0]
    output = {'editor': editor, 'protocol': PROTOCOL, 'workerRevision': args.revision,
              'virtualDomRevision': args.virtual_dom_revision,
              'allocationProfiling': args.allocation_profile,
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
                options = {}
                if args.response:
                    os.environ['SELECTION_RESPONSE_EDITOR'] = 'lvce'
                    os.environ['SELECTION_RESPONSE_DESTINATION'] = str(
                        destination.parent.resolve() / f'candidate-response-{repeat}.json')
                    options['capture_type'] = ResponseCapture
                elif args.allocation_profile:
                    os.environ['BENCHMARK_ALLOCATION_PROFILE'] = '1'
                    os.environ['SELECTION_PROFILE_DESTINATION'] = str(
                        destination.parent.resolve() / f'candidate-profile-{repeat}')
                    options['capture_type'] = ProfileCapture
                result = dict(trial(editor, args.perf, **options), repeat=repeat, diagnosticCandidate=args.revision)
                output['trials'].append(result)
                destination.write_text(json.dumps(output, indent=2) + '\n')
                if (not result['valid'] or not selection_evidence_is_valid(result)
                        or (not args.allocation_profile
                            and parse_counter(result.get('rawCounter', '')) != result.get('instructions'))):
                    raise RuntimeError(f'invalid candidate trial: {result}')
        finally:
            shutil.copyfile(backup, target)


if __name__ == '__main__':
    main()
