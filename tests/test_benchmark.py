import copy
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark import (CLIPBOARD_SENTINEL_PREFIX, DOUBLE_CLICK_DELAY_MS, FIXTURE_CONTENT,
                       FIXTURE_LINE, FIXTURE_WORDS_PER_LINE,
                       PROTOCOL, RESPONSE_WINDOW_SECONDS, SELECTION_TEXT, command_for,
                       double_click_command, focus_click_coordinates,
                       clipboard_after_copy, selection_coordinates, selection_evidence_is_valid,
                       zed_post_open_settle_seconds)
from wayland import main_windows, move_pointer
from counters import Capture, parse_counter
import report
import candidate
from install import BUILD_APPS


def trial(count=100):
    sentinel = CLIPBOARD_SENTINEL_PREFIX + 'a' * 32
    return dict(valid=True, instructions=count, measurementSeconds=1.12,
                inputDispatchSeconds=.12, inputToCutoffSeconds=1.0,
                source='perf stat instructions, per-CPU cgroup',
                selectionText=SELECTION_TEXT, selection=SELECTION_TEXT,
                clipboardSentinel=sentinel, clipboardBefore=sentinel,
                clipboardAfter=SELECTION_TEXT,
                selectionCoordinates=[500, 400], focusClickCoordinates=[580, 400],
                doubleClickDelayMilliseconds=DOUBLE_CLICK_DELAY_MS,
                documentContentSha256='b' * 64,
                fixtureContentSha256='b' * 64, fixtureUnchanged=True,
                focusPid=12, pidsAtInput=[12, 14],
                readiness={'id': 1, 'pid': 12, 'shell': 'xdg_shell'},
                displayBackend='wayland', inputDriver='ydotool',
                focusedNativeWindow={'id': 1, 'pid': 12, 'shell': 'xdg_shell', 'focused': True},
                cutoffAcknowledged=True,
                rawCounter=f'{count};;instructions;selection-test;1234;100.00;;\n')


def result(editor, counts=(100, 300, 200)):
    return dict(editor=editor, protocol=PROTOCOL, host={'machine': 'aarch64'},
                trials=[dict(trial(count), repeat=i + 1) for i, count in enumerate(counts)])


class BenchmarkTest(unittest.TestCase):
    def test_paired_candidates_alternate_and_restore_the_installed_bundle(self):
        for fail, status_ablation, control_pointer, immediate in [(f, a, c, i) for f, a, c in [(False, False, False), (True, False, False), (False, True, False), (True, True, False), (False, False, True), (True, False, True)] for i in [False, True]]:
            with self.subTest(fail=fail, status_ablation=status_ablation, control_pointer=control_pointer), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'config').mkdir()
                (root / 'config/editors.lock.json').write_text('[{"id":"lvce"}]')
                installed = root / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static/test/packages/editor-worker/dist/editorWorkerMain.js'
                installed.parent.mkdir(parents=True)
                installed.write_text('original')
                renderer = root / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static/test/packages/renderer-worker/dist/rendererWorkerMain.js'
                renderer.parent.mkdir(parents=True)
                original_renderer = candidate.POINTER_RENDER_ANCHOR + (Path(__file__).parent / 'fixtures/pinned-render-barrier.js').read_text()
                renderer.write_text(original_renderer)
                immediate_observed = []
                pointer_observed = []
                css = root / '.tmp/apps/lvce/usr/lib/lvce/resources/app/static/test/css/App.css'
                css.parent.mkdir(parents=True)
                css.write_text('.EditorLayers {} .EditorSelection {}')
                css_observed = []
                bundle = root / 'candidate.js'
                bundle.write_text('candidate')
                control = root / 'control.js'
                control.write_text('control')
                observed = []
                def measure(*args, **kwargs):
                    observed.append(installed.read_text())
                    immediate_observed.append('if (frame.immediate || isTest())' in renderer.read_text())
                    css_observed.append(css.read_text().endswith(candidate.OVERLAY_CSS.decode()))
                    pointer_observed.append('Editor.handleMouseDownAndRender' in renderer.read_text())
                    if fail and len(observed) == 3:
                        raise RuntimeError('measurement failed')
                    return trial()
                label = 'without-status' if status_ablation else 'paired-candidate'
                argv = ['candidate.py', '--perf', 'test', '--bundle', str(bundle), '--revision', 'candidate-sha',
                        '--label', label, '--control-bundle', str(control), '--control-revision', 'control-sha',
                        '--virtual-dom-revision', 'new-diff', '--control-virtual-dom-revision', 'old-diff',
                        '--pointer-render', '--renderer-revision', 'reviewed-renderer', '--editor-overlay']
                if immediate:
                    argv.append('--immediate-render')
                if control_pointer:
                    argv.append('--control-pointer-render')
                with contextlib.chdir(root), patch.object(candidate, 'ROOT', root), \
                        patch.object(candidate, 'trial', side_effect=measure), patch.object(sys, 'argv', argv):
                    with self.assertRaises(RuntimeError) if fail else contextlib.nullcontext():
                        candidate.main()
                self.assertEqual(installed.read_text(), 'original')
                self.assertEqual(renderer.read_text(), original_renderer)
                self.assertEqual(css.read_text(), '.EditorLayers {} .EditorSelection {}')
                if not fail:
                    self.assertEqual(observed, ['control', 'candidate', 'candidate', 'control', 'control', 'candidate'])
                    self.assertEqual(immediate_observed, [False, True, True, False, False, True] if immediate else [False] * 6)
                    self.assertEqual(css_observed, [False, True, True, False, False, True])
                    self.assertEqual(pointer_observed, [True] * 6 if status_ablation or control_pointer else [False, True, True, False, False, True])
                    for output_label, revision in [(label, 'candidate-sha'), ('paired-flat', 'control-sha')]:
                        output = json.loads((root / f'results/diagnostics/{output_label}.json').read_text())
                        self.assertEqual(output['workerRevision'], revision)
                        self.assertEqual(output['virtualDomRevision'], 'old-diff' if output_label == 'paired-flat' else 'new-diff')
                        self.assertEqual(output['pointerRender'], status_ablation or control_pointer or output_label != 'paired-flat')
                        self.assertEqual(output['immediateRender'], immediate and output_label != 'paired-flat')
                        self.assertEqual(output['editorOverlay'], output_label != 'paired-flat')
                        self.assertEqual(output['diagnosticStatusSuppressed'], output_label == 'without-status')
                        self.assertEqual(len(output['trials']), 3)
                        self.assertEqual(len(output['sequence']), 6)

    def test_immediate_backport_preserves_ordered_pending_frame_and_background_batching(self):
        import subprocess
        fixture = Path(__file__).parent / 'fixtures/pinned-render-barrier.js'
        patched = candidate.immediate_renderer(fixture.read_text())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'patched.js'
            target.write_text(patched)
            subprocess.run(['node', str(Path(__file__).parent / 'check-render-barrier.mjs'), str(target)], check=True)
        self.assertIn('instance.factory.immediateCommands?.includes(fnName)', patched)
        for content in ['', fixture.read_text() * 2]:
            with self.assertRaisesRegex(RuntimeError, 'expected one pinned'):
                candidate.immediate_renderer(content)

    def test_pointer_renderer_rejects_unmatched_or_ambiguous_build_output(self):
        for source in ['', candidate.POINTER_RENDER_ANCHOR * 2]:
            with self.assertRaisesRegex(RuntimeError, 'expected one pinned'):
                candidate.pointer_renderer(source)

    def test_only_bundled_editor_apps_are_built_during_install(self):
        self.assertEqual(BUILD_APPS, {'codemirror-electron', 'codemirror5-electron', 'ace-electron'})
        self.assertNotIn('basic-electron', BUILD_APPS)

    def test_editor_fixture_and_double_click_are_explicit(self):
        self.assertIn(SELECTION_TEXT, FIXTURE_CONTENT)
        self.assertEqual(FIXTURE_LINE.count(SELECTION_TEXT), FIXTURE_WORDS_PER_LINE)
        self.assertLess(len(FIXTURE_LINE), 160)
        self.assertGreater(FIXTURE_CONTENT.count(SELECTION_TEXT), 100)
        self.assertEqual(double_click_command(), [
            'ydotool', 'click', '--repeat', '2', '--next-delay',
            str(DOUBLE_CLICK_DELAY_MS // 2), '0xC0'])
        self.assertEqual(PROTOCOL['action'], 'one native Wayland double-click via ydotool selecting a word in a prepared plain-text document')
        self.assertEqual(PROTOCOL['responseWindowSeconds'], RESPONSE_WINDOW_SECONDS)
        self.assertIn('pointer positioning', PROTOCOL['excluded'])
        for editor in report.EDITORS:
            x, y = selection_coordinates(editor['id'])
            focus_x, focus_y = focus_click_coordinates(editor['id'])
            self.assertEqual(y, focus_y)
            self.assertGreater(abs(x - focus_x), 5)
        self.assertEqual(selection_coordinates('lvce'), (100, 400))

    def test_codemirror_and_other_electron_editors_use_bundled_apps(self):
        expected = {
            'codemirror6': ('codemirror-electron', '@codemirror/view', '6.43.14'),
            'codemirror5': ('codemirror5-electron', 'codemirror', '5.65.21'),
            'ace': ('ace-electron', 'ace-builds', '1.45.0'),
        }
        for editor_id, (app_name, dependency, version) in expected.items():
            with self.subTest(editor=editor_id):
                editor = next(editor for editor in report.EDITORS if editor['id'] == editor_id)
                command = command_for(editor, Path('/tmp/profile-home'), Path('/tmp/fixture.txt'))
                self.assertIn(str(ROOT / app_name), command)
                self.assertEqual(command[-1], '/tmp/fixture.txt')
                app = json.loads((ROOT / app_name / 'package.json').read_text())
                self.assertEqual(app['main'], 'main.js')
                self.assertEqual(app['dependencies'][dependency], version)

    def test_zed_post_open_settle_setting_is_bounded(self):
        self.assertEqual(zed_post_open_settle_seconds('1'), 1.0)
        self.assertEqual(zed_post_open_settle_seconds('10'), 10.0)
        self.assertEqual(zed_post_open_settle_seconds('0'), 0.0)
        for value in ['-1', '30.1', 'not-a-number']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                zed_post_open_settle_seconds(value)

    def test_stale_clipboard_and_exact_selection_evidence(self):
        evidence = trial()
        self.assertTrue(selection_evidence_is_valid(evidence))
        for changes in [dict(selection='selection'), dict(clipboardAfter='old clipboard'),
                        dict(clipboardBefore='selectiontarget'), dict(clipboardSentinel='selectiontarget'),
                        dict(fixtureUnchanged=False), dict(fixtureContentSha256='short'),
                        dict(documentContentSha256='c' * 64)]:
            broken = dict(evidence, **changes)
            with self.subTest(changes=changes):
                self.assertFalse(selection_evidence_is_valid(broken))

    def test_clipboard_copy_waits_for_the_new_selection(self):
        with patch('wayland.subprocess.check_output', side_effect=['stale', SELECTION_TEXT]) as read_clipboard, \
                patch('wayland.time.sleep'):
            self.assertEqual(clipboard_after_copy('stale'), SELECTION_TEXT)
        self.assertEqual(read_clipboard.call_count, 2)

    def test_native_window_discovery_rejects_xwayland_hidden_and_unrelated_pids(self):
        native = dict(id=1, pid=12, shell='xdg_shell', visible=True, type='con')
        floating = dict(native, id=2)
        tree = dict(nodes=[dict(nodes=[native])], floating_nodes=[floating])
        self.assertEqual(main_windows(tree, [12]), [native, floating])
        for changes in [dict(shell='xwayland'), dict(visible=False), dict(pid=99),
                        dict(type='workspace'), dict(window_properties={'transient_for': 100})]:
            with self.subTest(changes=changes):
                self.assertEqual(main_windows(dict(nodes=[dict(native, **changes)]), [12]), [])

    def test_every_electron_editor_requires_native_wayland(self):
        for editor in report.EDITORS:
            if editor['id'] in {'lvce', 'vscode', 'basic-electron', 'codemirror6', 'codemirror5', 'ace'}:
                with self.subTest(editor=editor['id']):
                    command = command_for(editor, Path('/tmp/profile'), Path('/tmp/fixture'))
                    self.assertIn('--ozone-platform=wayland', command)
                    self.assertNotIn('--ozone-platform=x11', command)

    def test_pointer_setup_uses_acknowledged_compositor_pixel_coordinates(self):
        with patch('wayland.sway', return_value=[{'success': True}]) as ipc:
            move_pointer({'rect': {'x': 10, 'y': 20}}, 200, 160)
            ipc.assert_called_once_with('seat seat0 cursor set 210 180')
        with patch('wayland.sway', return_value=[{'success': False}]), self.assertRaises(RuntimeError):
            move_pointer({'rect': {'x': 0, 'y': 0}}, 200, 160)

    def test_counter_control_and_multiplex_validation(self):
        for ack in [b'ack\n', b'ack\n\x00', b'wrong\n']:
            control_read, control_write = os.pipe()
            ack_read, ack_write = os.pipe()
            try:
                os.write(ack_write, ack)
                capture = Capture('unused', '.')
                capture.fds = [control_write, ack_read]
                capture.process = Mock()
                capture.process.poll.return_value = None
                if ack.startswith(b'ack'):
                    capture.control('disable')
                else:
                    with self.assertRaises(RuntimeError):
                        capture.control('disable')
                self.assertEqual(os.read(control_read, 256), b'disable\n')
            finally:
                for fd in [control_read, control_write, ack_read, ack_write]:
                    os.close(fd)
        self.assertEqual(parse_counter('123;;instructions;group;100;100.00;;'), 123)
        for count, running in [('<not supported>', '100'), ('0', '100'), ('123', '50'), ('123', 'nan')]:
            with self.assertRaises(ValueError):
                parse_counter(f'{count};;instructions;group;100;{running};;')

    def test_report_rejects_unverified_selection_focus_cutoff_and_counter(self):
        editor = report.EDITORS[0]
        data = result(editor)
        report.validate_result(data, editor['id'])
        invalid = [dict(cutoffAcknowledged=False), dict(inputToCutoffSeconds=.1),
                   dict(diagnosticCandidate='unreleased-worker'),
                   dict(diagnosticResponse=True),
                   dict(instructions=101), dict(valid=False), dict(clipboardAfter='stale'),
                   dict(focusPid=999), dict(selection=None), dict(fixtureUnchanged=False),
                   dict(displayBackend='x11'), dict(inputDriver='xdotool'),
                   dict(focusedNativeWindow={'shell': 'xwayland', 'focused': True, 'pid': 12}),
                   dict(focusedNativeWindow={'shell': 'xdg_shell', 'focused': False, 'pid': 12}),
                   dict(readiness={'shell': 'xwayland'})]
        for changes in invalid:
            broken = copy.deepcopy(data)
            broken['trials'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                report.validate_result(broken, editor['id'])
        old = copy.deepcopy(data)
        old['protocol']['version'] = 4
        with self.assertRaises(ValueError):
            report.validate_result(old, editor['id'])
        data['trials'].pop()
        with self.assertRaises(ValueError):
            report.validate_result(data, editor['id'])

    def test_matrix_must_have_every_editor_and_matching_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                report.load_results(root)
            for editor in report.EDITORS:
                path = root / ('selection-' + editor['id'])
                path.mkdir()
                (path / 'results.json').write_text(json.dumps(result(editor)))
            self.assertEqual(len(report.load_results(root)), len(report.EDITORS))
            path = root / ('selection-' + report.EDITORS[0]['id']) / 'results.json'
            path.write_text(json.dumps(result(dict(report.EDITORS[0], version='wrong'))))
            with self.assertRaises(ValueError):
                report.load_results(root)

    def test_chart_and_report_use_medians_lowest_first_and_keep_raw_trials(self):
        items = []
        for editor, counts in [(report.EDITORS[0], [100, 300, 200]), (report.EDITORS[1], [50, 40, 60])]:
            items.append(dict(editor=editor, data=result(editor, counts)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report.build_report(items, root, 'https://example.test/run', 'commit')
            payload = json.loads((root / 'report.json').read_text())
            self.assertEqual([e['medianInstructions'] for e in payload['editors']], [50, 200])
            chart = report.build_median_chart(payload['editors'])
            self.assertLess(chart.index('VS Code'), chart.index('LVCE Editor'))
            raw = json.loads((root / 'raw/lvce.json').read_text())
            self.assertEqual(raw, items[0]['data'])
            page = (root / 'index.html').read_text()
            self.assertIn('Median instructions per word selection', page)
            self.assertIn('Download JSON', page)
            self.assertIn('native Wayland double-click sent by ydotool', page)
            self.assertEqual(payload['editors'][0]['protocol']['version'], 5)


if __name__ == '__main__':
    unittest.main()
