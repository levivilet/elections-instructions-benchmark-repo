"""Measure an explicitly identified worker bundle without publishing it as a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from benchmark import ROOT, PROTOCOL, trial, selection_evidence_is_valid
from counters import Capture, parse_counter
from diagnose import ProfileCapture
from response import ResponseCapture


OVERLAY_CSS = b'\n.Selections { position: absolute; inset: 0; pointer-events: none; contain: strict; will-change: transform; } .LayerCursor { will-change: transform; }\n'


class CandidateCapture(Capture):
    def finish(self):
        result = super().finish()
        memory = []
        for pid in self.pids():
            try:
                fields = {}
                for line in (Path('/proc') / str(pid) / 'smaps_rollup').read_text().splitlines():
                    if line.startswith(('Rss:', 'Pss:')):
                        key, value, unit = line.split()
                        if unit != 'kB':
                            raise RuntimeError(f'unexpected process memory unit: {unit}')
                        fields[key[:-1]] = int(value) * 1024
                memory.append({'pid': pid, **fields})
            except (FileNotFoundError, ProcessLookupError):
                continue
        result['memoryAfterCutoff'] = memory
        return result


POINTER_RENDER_ANCHOR = """      await actualInvoke(fullId, editor.uid, ...restArgs);
      return renderPendingEditors(editor, preserveFocus);"""


def pointer_renderer(content):
    # Backport only the reviewed pointer-call change into the pinned release.
    # Fail closed if its build output changes; never silently compare two controls.
    if content.count(POINTER_RENDER_ANCHOR) != 1:
        raise RuntimeError('expected one pinned renderer pointer-call anchor')
    return content.replace(POINTER_RENDER_ANCHOR, """      if (fullId === "Editor.handleMouseDown" && !preserveFocus && getEditorUids(editor).size === 1) {
        const commands = await actualInvoke("Editor.handleMouseDownAndRender", editor.uid, ...restArgs);
        if (getEditorUids(editor).size > 1) {
          const pending = await renderPendingEditors(editor, preserveFocus);
          return { ...pending, commands: [...commands, ...pending.commands] };
        }
        return { ...editor, commands };
      }
""" + POINTER_RENDER_ANCHOR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--label', choices=['baseline', 'dependency', 'token', 'property', 'flat', 'row', 'paired-candidate', 'paired-row', 'candidate', 'response', 'allocations', 'native', 'cpu', 'without-status'], default='candidate')
    parser.add_argument('--virtual-dom-revision')
    parser.add_argument('--pointer-render', action='store_true')
    parser.add_argument('--editor-overlay', action='store_true')
    parser.add_argument('--renderer-revision')
    parser.add_argument('--control-bundle', type=Path)
    parser.add_argument('--control-label', default='paired-flat')
    parser.add_argument('--control-pointer-render', action='store_true')
    parser.add_argument('--control-revision')
    parser.add_argument('--control-virtual-dom-revision')
    observers = parser.add_mutually_exclusive_group()
    observers.add_argument('--response', action='store_true')
    observers.add_argument('--allocation-profile', action='store_true')
    observers.add_argument('--native-profile', action='store_true')
    observers.add_argument('--cpu-profile', action='store_true')
    args = parser.parse_args()
    if args.control_bundle and (not args.control_revision or args.response or args.allocation_profile or args.native_profile or args.cpu_profile):
        parser.error('paired counts require a control revision and no observer')
    if args.pointer_render and not args.renderer_revision:
        parser.error('pointer renderer requires its reviewed source revision')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == 'lvce')
    targets = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/editor-worker/dist/editorWorkerMain.js'))
    if len(targets) != 1:
        raise RuntimeError(f'expected one installed editor bundle, got {targets}')
    target = targets[0]
    renderer_target = None
    renderer_original = renderer_candidate = None
    if args.pointer_render:
        renderers = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/renderer-worker/dist/rendererWorkerMain.js'))
        if len(renderers) != 1:
            raise RuntimeError(f'expected one installed renderer bundle, got {renderers}')
        renderer_target = renderers[0]
        renderer_original = renderer_target.read_bytes()
        renderer_candidate = pointer_renderer(renderer_original.decode()).encode()
    css_target = None
    css_original = css_candidate = None
    if args.editor_overlay:
        styles = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/css/parts/ViewletEditor.css'))
        if len(styles) != 1:
            raise RuntimeError(f'expected one installed editor stylesheet, got {styles}')
        css_target = styles[0]
        css_original = css_target.read_bytes()
        css_candidate = css_original + OVERLAY_CSS
    plans = [(args.label, args.bundle, args.revision, args.virtual_dom_revision, args.pointer_render, args.editor_overlay)]
    if args.control_bundle:
        plans.insert(0, (args.control_label, args.control_bundle, args.control_revision, args.control_virtual_dom_revision or args.virtual_dom_revision, args.control_pointer_render or (args.pointer_render if args.label == 'without-status' else False), False))
    sequence = []
    outputs = {label: {'editor': editor, 'protocol': PROTOCOL, 'workerRevision': revision,
                       'virtualDomRevision': virtual_dom_revision,
                       'pointerRender': pointer_render,
                       'editorOverlay': editor_overlay,
                       'editorStylesSha256': hashlib.sha256(css_candidate if editor_overlay else css_original).hexdigest() if css_original else None,
                       'diagnosticStatusSuppressed': label == 'without-status',
                       'rendererPatchRevision': args.renderer_revision if pointer_render else None,
                       'rendererBundleSha256': hashlib.sha256(renderer_candidate if pointer_render else renderer_original).hexdigest() if renderer_original else None,
                       'allocationProfiling': args.allocation_profile,
                       'nativeProfiling': args.native_profile,
                       'cpuProfiling': args.cpu_profile,
                       'bundleSha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                       'sequence': sequence,
                       'note': 'Diagnostic candidate, not the pinned release; excluded from the official report.',
                       'trials': []}
               for label, bundle, revision, virtual_dom_revision, pointer_render, editor_overlay in plans}
    destination = Path('results/diagnostics')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='selection-candidate-backup-') as directory:
        backup = Path(directory) / 'editorWorkerMain.js'
        shutil.copyfile(target, backup)
        try:
            for repeat in range(1, 4):
                ordered = plans if repeat % 2 else list(reversed(plans))
                for label, bundle, revision, _virtual_dom_revision, pointer_render, editor_overlay in ordered:
                    shutil.copyfile(bundle, target)
                    if renderer_target:
                        renderer_target.write_bytes(renderer_candidate if pointer_render else renderer_original)
                    if css_target:
                        css_target.write_bytes(css_candidate if editor_overlay else css_original)
                    options = {'capture_type': CandidateCapture}
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
            if renderer_target:
                renderer_target.write_bytes(renderer_original)
            if css_target:
                css_target.write_bytes(css_original)


if __name__ == '__main__':
    main()
