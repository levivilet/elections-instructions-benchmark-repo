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
    parser.add_argument('--label', choices=['baseline', 'dependency', 'token', 'property', 'flat', 'row', 'paired-candidate', 'paired-row', 'candidate', 'response', 'allocations', 'native', 'cpu'], default='candidate')
    parser.add_argument('--virtual-dom-revision')
    parser.add_argument('--control-bundle', type=Path)
    parser.add_argument('--control-revision')
    observers = parser.add_mutually_exclusive_group()
    observers.add_argument('--response', action='store_true')
    observers.add_argument('--allocation-profile', action='store_true')
    observers.add_argument('--native-profile', action='store_true')
    observers.add_argument('--cpu-profile', action='store_true')
    args = parser.parse_args()
    if args.control_bundle and (not args.control_revision or args.response or args.allocation_profile or args.native_profile or args.cpu_profile):
        parser.error('paired counts require a control revision and no observer')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == 'lvce')
    targets = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/editor-worker/dist/editorWorkerMain.js'))
    if len(targets) != 1:
        raise RuntimeError(f'expected one installed editor bundle, got {targets}')
    target = targets[0]
    plans = [(args.label, args.bundle, args.revision)]
    if args.control_bundle:
        plans.insert(0, ('paired-flat', args.control_bundle, args.control_revision))
    sequence = []
    outputs = {label: {'editor': editor, 'protocol': PROTOCOL, 'workerRevision': revision,
                       'virtualDomRevision': args.virtual_dom_revision,
                       'allocationProfiling': args.allocation_profile,
                       'nativeProfiling': args.native_profile,
                       'cpuProfiling': args.cpu_profile,
                       'bundleSha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                       'sequence': sequence,
                       'note': 'Diagnostic candidate, not the pinned release; excluded from the official report.',
                       'trials': []}
               for label, bundle, revision in plans}
    destination = Path('results/diagnostics')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='selection-candidate-backup-') as directory:
        backup = Path(directory) / 'editorWorkerMain.js'
        shutil.copyfile(target, backup)
        try:
            for repeat in range(1, 4):
                ordered = plans if repeat % 2 else list(reversed(plans))
                for label, bundle, revision in ordered:
                    shutil.copyfile(bundle, target)
                    options = {}
                    if args.response:
                        os.environ['SELECTION_RESPONSE_EDITOR'] = 'lvce'
                        os.environ['SELECTION_RESPONSE_DESTINATION'] = str(
                            destination.resolve() / f'candidate-response-{repeat}.json')
                        options['capture_type'] = ResponseCapture
                    elif args.allocation_profile or args.native_profile or args.cpu_profile:
                        os.environ['BENCHMARK_ALLOCATION_PROFILE'] = '1' if args.allocation_profile else '0'
                        os.environ['BENCHMARK_CPU_PROFILE'] = '1' if args.cpu_profile else '0'
                        os.environ['BENCHMARK_JAVASCRIPT_PROFILE'] = '0'
                        os.environ['SELECTION_PROFILE_DESTINATION'] = str(
                            destination.resolve() / f'{args.label}-profile-{repeat}')
                        options['capture_type'] = ProfileCapture
                    result = dict(trial(editor, args.perf, **options), repeat=repeat, diagnosticCandidate=revision)
                    outputs[label]['trials'].append(result)
                    sequence.append({'label': label, 'repeat': repeat})
                    for output_label, output in outputs.items():
                        (destination / f'{output_label}.json').write_text(json.dumps(output, indent=2) + '\n')
                    if (not result['valid'] or not selection_evidence_is_valid(result)
                            or (not (args.allocation_profile or args.native_profile or args.cpu_profile)
                                and parse_counter(result.get('rawCounter', '')) != result.get('instructions'))):
                        raise RuntimeError(f'invalid candidate trial: {result}')
        finally:
            shutil.copyfile(backup, target)


if __name__ == '__main__':
    main()
