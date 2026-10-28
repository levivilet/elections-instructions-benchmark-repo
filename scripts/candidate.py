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


TEXT_LAYER_CSS = b'\n.EditorRows { will-change: transform; }\n'


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



def immediate_renderer(content):
    # Reviewed ordered scheduler barrier, backported to the pinned release.
    # Keep its existing normalization, global tail, failure recovery and frame cap.
    def replace_once(old, new):
        nonlocal content
        if content.count(old) != 1:
            raise RuntimeError('expected one pinned immediate-render anchor: ' + old[:65])
        content = content.replace(old, new)

    start = content.index('// src/parts/RendererFrameScheduler/RendererFrameScheduler.js') if '// src/parts/RendererFrameScheduler/RendererFrameScheduler.js' in content else -1
    end = content.find('// src/parts/RendererProcess/RendererProcess.js', start)
    if start < 0 or end < 0:
        raise RuntimeError('expected one pinned immediate-render scheduler boundary')
    prefix, suffix = content[:start], content[end:]
    content = content[start:end]
    replace_once('waitForEligibleFrame = () => {\n      if (isTest()) {',
                 'waitForEligibleFrame = (frame) => {\n      if (frame.immediate || isTest()) {')
    replace_once('          settled = true;\n          clearTimeout(timer);',
                 '          settled = true;\n          frame.flush = void 0;\n          clearTimeout(timer);')
    replace_once('        frameId = requestAnimationFrame(handleFrame);\n      });',
                 '        frameId = requestAnimationFrame(handleFrame);\n        frame.flush = () => finish(now());\n      });')
    replace_once('const timestamp = await waitForEligibleFrame();',
                 'const timestamp = await waitForEligibleFrame(frame);')
    replace_once("""    sendMultiple = (commands2) => {
      if (state3.pendingFrame) {
        state3.pendingFrame.commands.push(...commands2);
        return state3.pendingFrame.promise;
      }
      const frame = {
        commands: [...commands2],
        promise: Promise.resolve()
      };""", """    sendMultiple = (commands2, immediate = false) => {
      const { pendingFrame } = state3;
      if (pendingFrame) {
        pendingFrame.commands.push(...commands2);
        if (immediate) {
          pendingFrame.immediate = true;
          pendingFrame.flush?.();
        }
        return pendingFrame.promise;
      }
      const frame = {
        commands: [...commands2],
        immediate,
        promise: Promise.resolve()
      };""")
    content = prefix + content + suffix
    replace_once('__export(ViewletEditorText_ipc_exports, {',
                 '__export(ViewletEditorText_ipc_exports, {\n  immediateCommands: () => ["handleMouseDown"],')
    old = """      if (commands2.length > 0) {
        await invoke2(
          /* Viewlet.sendMultiple */
          "Viewlet.sendMultiple",
          /* commands */
          commands2
        );
      }
      if (getInstance(uid) === instance && instance.factory.afterRender)"""
    new = """      if (commands2.length > 0) {
        if (instance.factory.immediateCommands?.includes(fnName)) {
          await sendMultiple(commands2, true);
        } else {
          await invoke2("Viewlet.sendMultiple", commands2);
        }
      }
      if (getInstance(uid) === instance && instance.factory.afterRender)"""
    replace_once(old, new)
    return content

CSS_DEDUP_ANCHOR = """const addCssStyleSheet = (id, text) => {
  const existing = get$5(id);
  if (existing) {
    existing.replaceSync(text);
    setText$1(id, text);
    if (id === 'ContributedColorTheme') {
      window.dispatchEvent(new Event('color-theme-changed'));
    }
    return;
  }
  const sheet = new CSSStyleSheet({});
  set$6(id, sheet);
  setText$1(id, text);
  sheet.replaceSync(text);
  document.adoptedStyleSheets.push(sheet);
  if (id === 'ContributedColorTheme') {
    window.dispatchEvent(new Event('color-theme-changed'));
  }
};
"""
CSS_DEDUP_REPLACEMENT = """const addCssStyleSheet = (id, text) => {
  const existing = get$5(id);
  if (existing) {
    if (typeof text !== 'string' || getText(id) !== text) {
      existing.replaceSync(text);
      setText$1(id, text);
    }
    if (id === 'ContributedColorTheme') {
      window.dispatchEvent(new Event('color-theme-changed'));
    }
    return;
  }
  const sheet = new CSSStyleSheet({});
  sheet.replaceSync(text);
  document.adoptedStyleSheets.push(sheet);
  set$6(id, sheet);
  setText$1(id, text);
  if (id === 'ContributedColorTheme') {
    window.dispatchEvent(new Event('color-theme-changed'));
  }
};
"""


def css_dedup_renderer(content):
    if content.count(CSS_DEDUP_ANCHOR) != 1:
        raise RuntimeError('expected one pinned CSS stylesheet implementation')
    return content.replace(CSS_DEDUP_ANCHOR, CSS_DEDUP_REPLACEMENT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--label', choices=['baseline', 'dependency', 'token', 'property', 'flat', 'row', 'paired-candidate', 'paired-row', 'candidate', 'response', 'allocations', 'native', 'cpu', 'without-status', 'without-line-decorations'], default='candidate')
    parser.add_argument('--virtual-dom-revision')
    parser.add_argument('--pointer-render', action='store_true')
    styles = parser.add_mutually_exclusive_group()
    styles.add_argument('--editor-overlay', action='store_true')
    styles.add_argument('--text-layer', action='store_true')
    parser.add_argument('--immediate-render', action='store_true')
    parser.add_argument('--css-dedup', action='store_true')
    parser.add_argument('--renderer-process-revision')
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
    if (args.pointer_render or args.immediate_render) and not args.renderer_revision:
        parser.error('pointer renderer requires its reviewed source revision')
    if args.css_dedup and not args.renderer_process_revision:
        parser.error('CSS dedup requires its reviewed source revision')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == 'lvce')
    targets = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/editor-worker/dist/editorWorkerMain.js'))
    if len(targets) != 1:
        raise RuntimeError(f'expected one installed editor bundle, got {targets}')
    target = targets[0]
    renderer_target = None
    renderer_original = renderer_candidate = None
    if args.pointer_render or args.immediate_render:
        renderers = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/renderer-worker/dist/rendererWorkerMain.js'))
        if len(renderers) != 1:
            raise RuntimeError(f'expected one installed renderer bundle, got {renderers}')
        renderer_target = renderers[0]
        renderer_original = renderer_target.read_bytes()
        renderer_pointer = pointer_renderer(renderer_original.decode()).encode() if args.pointer_render else renderer_original
        renderer_candidate = immediate_renderer(renderer_pointer.decode()).encode() if args.immediate_render else renderer_pointer
    page_target = None
    page_original = page_candidate = None
    if args.css_dedup:
        pages = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/packages/renderer-process/dist/rendererProcessMain.js'))
        if len(pages) != 1:
            raise RuntimeError(f'expected one installed renderer process bundle, got {pages}')
        page_target = pages[0]
        page_original = page_target.read_bytes()
        page_candidate = css_dedup_renderer(page_original.decode()).encode()
    css_target = None
    css_original = css_candidate = None
    if args.editor_overlay or args.text_layer:
        styles = list((ROOT / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static').glob('*/css/App.css'))
        if len(styles) != 1:
            raise RuntimeError(f'expected one installed editor stylesheet, got {styles}')
        css_target = styles[0]
        css_original = css_target.read_bytes()
        if b'.EditorLayers' not in css_original or b'.EditorSelection' not in css_original:
            raise RuntimeError('packaged stylesheet is missing editor layer rules')
        if args.text_layer and b'.EditorRows' not in css_original:
            raise RuntimeError('packaged stylesheet is missing editor rows rules')
        css_candidate = css_original + (TEXT_LAYER_CSS if args.text_layer else OVERLAY_CSS)
    plans = [(args.label, args.bundle, args.revision, args.virtual_dom_revision, args.pointer_render, args.editor_overlay or args.text_layer)]
    if args.control_bundle:
        plans.insert(0, (args.control_label, args.control_bundle, args.control_revision, args.control_virtual_dom_revision or args.virtual_dom_revision, args.control_pointer_render or (args.pointer_render if args.label == 'without-status' else False), False))
    sequence = []
    outputs = {label: {'editor': editor, 'protocol': PROTOCOL, 'workerRevision': revision,
                       'virtualDomRevision': virtual_dom_revision,
                       'pointerRender': pointer_render,
                       'editorOverlay': editor_overlay and args.editor_overlay,
                       'textLayer': editor_overlay and args.text_layer,
                       'cssDedup': args.css_dedup and label == args.label,
                       'rendererProcessPatchRevision': args.renderer_process_revision if args.css_dedup else None,
                       'rendererProcessBundleSha256': hashlib.sha256(page_candidate if label == args.label else page_original).hexdigest() if page_original else None,
                       'immediateRender': args.immediate_render and label == args.label,
                       'editorStylesSha256': hashlib.sha256(css_candidate if editor_overlay else css_original).hexdigest() if css_original else None,
                       'diagnosticStatusSuppressed': label == 'without-status',
                       'diagnosticLineDecorationsSuppressed': label == 'without-line-decorations',
                       'rendererPatchRevision': args.renderer_revision if pointer_render or args.immediate_render else None,
                       'rendererBundleSha256': hashlib.sha256(renderer_candidate if label == args.label else renderer_pointer if pointer_render else renderer_original).hexdigest() if renderer_original else None,
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
                        renderer_target.write_bytes(renderer_candidate if label == args.label else renderer_pointer if pointer_render else renderer_original)
                    if page_target:
                        page_target.write_bytes(page_candidate if label == args.label else page_original)
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
            if page_target:
                page_target.write_bytes(page_original)
            if css_target:
                css_target.write_bytes(css_original)


if __name__ == '__main__':
    main()
