import copy
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
                       double_click_command, focus_click_coordinates, is_main_window,
                       clipboard_after_copy, selection_coordinates, selection_evidence_is_valid,
                       zed_post_open_settle_seconds)
from counters import Capture, parse_counter
import report
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
                focusPid=12, pidsAtInput=[12, 14], readiness={'windowId': '1'},
                cutoffAcknowledged=True,
                rawCounter=f'{count};;instructions;selection-test;1234;100.00;;\n')


def result(editor, counts=(100, 300, 200)):
    return dict(editor=editor, protocol=PROTOCOL, host={'machine': 'aarch64'},
                trials=[dict(trial(count), repeat=i + 1) for i, count in enumerate(counts)])


class BenchmarkTest(unittest.TestCase):
    def test_only_bundled_editor_apps_are_built_during_install(self):
        self.assertEqual(BUILD_APPS, {'codemirror-electron', 'codemirror5-electron', 'ace-electron'})
        self.assertNotIn('basic-electron', BUILD_APPS)

    def test_editor_fixture_and_double_click_are_explicit(self):
        self.assertIn(SELECTION_TEXT, FIXTURE_CONTENT)
        self.assertEqual(FIXTURE_LINE.count(SELECTION_TEXT), FIXTURE_WORDS_PER_LINE)
        self.assertLess(len(FIXTURE_LINE), 160)
        self.assertGreater(FIXTURE_CONTENT.count(SELECTION_TEXT), 100)
        self.assertEqual(double_click_command(), [
            'xdotool', 'click', '--clearmodifiers', '--repeat', '2',
            '--delay', str(DOUBLE_CLICK_DELAY_MS), '1'])
        self.assertEqual(PROTOCOL['action'], 'one X11 double-click selecting a word in a prepared plain-text document')
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
        with patch('benchmark.subprocess.check_output', side_effect=['stale', SELECTION_TEXT]) as read_clipboard, \
                patch('benchmark.time.sleep'):
            self.assertEqual(clipboard_after_copy('stale'), SELECTION_TEXT)
        self.assertEqual(read_clipboard.call_count, 2)

    def test_main_window_type_defaults_and_rejections(self):
        mapped = 'WM_STATE(WM_STATE):\n    window state: Normal\n'
        missing = '_NET_WM_WINDOW_TYPE:  not found.\nWM_TRANSIENT_FOR:  not found.\n'
        self.assertTrue(is_main_window(mapped + missing))
        self.assertTrue(is_main_window(mapped + '_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_NORMAL'))
        self.assertFalse(is_main_window(missing))
        self.assertFalse(is_main_window(mapped + '_NET_WM_WINDOW_TYPE: not found.\nWM_TRANSIENT_FOR(WINDOW): window id # 0x123'))
        for kind in ['SPLASH', 'DIALOG', 'UTILITY', 'MENU']:
            self.assertFalse(is_main_window(mapped + f'_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_{kind}'))

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
                   dict(instructions=101), dict(valid=False), dict(clipboardAfter='stale'),
                   dict(focusPid=999), dict(selection=None), dict(fixtureUnchanged=False)]
        for changes in invalid:
            broken = copy.deepcopy(data)
            broken['trials'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                report.validate_result(broken, editor['id'])
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


if __name__ == '__main__':
    unittest.main()
