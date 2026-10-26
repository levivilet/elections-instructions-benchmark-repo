"""Measure retired CPU instructions for one verified editor word selection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
import uuid
from counters import Capture
from wayland import (DOUBLE_CLICK_DELAY_MS, click, clipboard_after_copy, ctrl,
                     double_click_command, focus, focused_window, key,
                     move_pointer, read_clipboard, set_clipboard, visible_window)

ROOT = Path(__file__).resolve().parent.parent
SELECTION_TEXT = 'selectiontarget'
CLIPBOARD_SENTINEL_PREFIX = 'selection-benchmark-stale-'
FIXTURE_WORDS_PER_LINE = 8
FIXTURE_LINE = ' '.join([SELECTION_TEXT] * FIXTURE_WORDS_PER_LINE)
FIXTURE_CONTENT = (FIXTURE_LINE + '\n') * 60
FOCUS_CLICK_OFFSET_X = 80
CLIPBOARD_UPDATE_TIMEOUT_SECONDS = 2.0
RESPONSE_WINDOW_SECONDS = 1.0
DEFAULT_ZED_POST_OPEN_SETTLE_SECONDS = 10.0
MAX_ZED_POST_OPEN_SETTLE_SECONDS = 30.0
PROTOCOL = {
    'version': 5,
    'metric': 'retired hardware instructions (user and kernel)',
    'action': 'one native Wayland double-click via ydotool selecting a word in a prepared plain-text document',
    'boundary': 'acknowledged perf enable immediately before double-click dispatch through acknowledged disable 1.0 seconds after dispatch',
    'responseWindowSeconds': RESPONSE_WINDOW_SECONDS,
    'accounting': 'per-CPU cgroup; includes editor descendants and all work during the response window',
    'excluded': 'launch, fixture setup, pointer positioning, stale-clipboard setup, focus setup, the external input driver, selection verification, and cleanup',
    'profile': 'fresh HOME and XDG directories for every trial',
    'cache': 'warm OS filesystem caches permitted; application profiles always fresh',
    'launcher': 'editor cgroup contains editor process tree; controller, Wayland compositor, seatd, ydotoold, ydotool and clipboard helpers stay outside',
    'cutoff': 'perf disable acknowledgement after fixed response window; control latency through acknowledgement is included',
    'verification': 'after cutoff, copy the selection and require exactly selectiontarget with stale-clipboard protection; require the fixture bytes to remain unchanged',
    'doubleClickDelayMilliseconds': DOUBLE_CLICK_DELAY_MS,
    'repeats': 3,
}


def command_for(editor, home, fixture):
    binary = str(ROOT / '.tmp/apps' / editor['id'] / editor['binary'])
    editor_id = editor['id']
    if editor_id == 'geany':
        return ['geany', '--new-instance', str(fixture)]
    args = []
    if editor_id in {'lvce', 'vscode', 'basic-electron', 'codemirror6', 'codemirror5', 'ace'}:
        args += ['--no-sandbox', '--disable-gpu', '--ozone-platform=wayland']
    if editor_id in {'lvce', 'vscode', 'zed'}:
        args += ['--user-data-dir', str(home / 'profile')]
    if editor_id == 'vscode':
        args += ['--disable-extensions', '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust']
    if editor_id == 'lapce':
        args += ['--new', '--wait']
    if editor_id == 'basic-electron':
        args += [str(ROOT / 'basic-electron'), str(fixture)]
    elif editor_id in {'codemirror6', 'codemirror5', 'ace'}:
        app = {'codemirror6': 'codemirror-electron', 'codemirror5': 'codemirror5-electron',
               'ace': 'ace-electron'}[editor_id]
        args += [str(ROOT / app), str(fixture)]
    else:
        args += [str(fixture)]
    return [binary, *args]


def selection_coordinates(editor_id):
    positions = {'lvce': (100, 400), 'basic-electron': (200, 160), 'codemirror6': (200, 160),
                 'codemirror5': (200, 160), 'ace': (200, 160), 'lapce': (500, 150),
                 'zed': (800, 135)}
    return positions.get(editor_id, (500, 400))


def focus_click_coordinates(editor_id):
    x, y = selection_coordinates(editor_id)
    return x + FOCUS_CLICK_OFFSET_X, y


def selection_evidence_is_valid(trial):
    sentinel = trial.get('clipboardSentinel')
    fixture_digest = trial.get('fixtureContentSha256')
    document_digest = trial.get('documentContentSha256')
    return (trial.get('selection') == SELECTION_TEXT
            and trial.get('selectionText') == SELECTION_TEXT
            and isinstance(sentinel, str)
            and re.fullmatch(re.escape(CLIPBOARD_SENTINEL_PREFIX) + r'[0-9a-f]{32}', sentinel)
            and trial.get('clipboardBefore') == sentinel
            and trial.get('clipboardAfter') == SELECTION_TEXT
            and trial.get('clipboardAfter') != sentinel
            and trial.get('fixtureUnchanged') is True
            and isinstance(fixture_digest, str)
            and re.fullmatch(r'[0-9a-f]{64}', fixture_digest)
            and document_digest == fixture_digest)


def zed_post_open_settle_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError('Zed post-open settling must be a number from 0 to 30 seconds') from error
    if not 0 <= seconds <= MAX_ZED_POST_OPEN_SETTLE_SECONDS:
        raise ValueError('Zed post-open settling must be a number from 0 to 30 seconds')
    return seconds


def trial(editor, perf, zed_settle_seconds=DEFAULT_ZED_POST_OPEN_SETTLE_SECONDS, *, idle=False, warm=False, capture_type=Capture):
    with tempfile.TemporaryDirectory(prefix='selection-trial-') as directory:
        d = Path(directory)
        home = d / 'home'
        home.mkdir()
        fixture = d / 'selection-fixture.txt'
        fixture.write_text(FIXTURE_CONTENT)
        fixture_digest = hashlib.sha256(FIXTURE_CONTENT.encode()).hexdigest()
        env = {**os.environ, 'HOME': str(home), 'LC_ALL': 'C', 'LIBGL_ALWAYS_SOFTWARE': '1',
               'GALLIUM_DRIVER': 'llvmpipe', 'ZED_ALLOW_EMULATED_GPU': '1',
               'ELECTRON_OZONE_PLATFORM_HINT': 'wayland', 'GDK_BACKEND': 'wayland',
               'WINIT_UNIX_BACKEND': 'wayland', 'QT_QPA_PLATFORM': 'wayland',
               'WAYLAND_DISPLAY': str(Path(os.environ['XDG_RUNTIME_DIR']) / os.environ['WAYLAND_DISPLAY'])}
        for env_key, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                          ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                          ('XDG_RUNTIME_DIR', 'runtime')]:
            env[env_key] = str(home / name)
            (home / name).mkdir()
        for name in ['DISPLAY', 'WAYLAND_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
            env.pop(name, None)
        (home / 'runtime').chmod(0o700)
        if 'SUDO_UID' in env:
            uid, gid = int(env['SUDO_UID']), int(env['SUDO_GID'])
            for path in [d, home, fixture, *home.iterdir()]:
                os.chown(path, uid, gid)
        command = command_for(editor, home, fixture)
        details = {'stage': 'launch', 'command': command, 'fixture': str(fixture)}
        failure_diagnostics = {}
        try:
            with capture_type(perf, d) as capture:
                # Startup, window discovery, settling and focus are excluded.
                capture.cutoff()
                with (d / 'stdout').open('w') as stdout, (d / 'stderr').open('w') as stderr:
                    child = capture.launch(command, env, stdout, stderr, cwd=d)
                    start = time.monotonic()
                    while True:
                        pids = capture.pids()
                        if not pids:
                            raise RuntimeError(f'editor exited before main window: {child.poll()}')
                        window = visible_window(pids)
                        if window:
                            break
                        if time.monotonic() - start > 120:
                            failure_diagnostics = {'reason': 'main window readiness timeout'}
                            raise RuntimeError('main window readiness timeout')
                        time.sleep(.02)
                    time.sleep(10)
                    details.update(window=window, pids=pids, stage='settle')
                    if editor['id'] in {'codemirror6', 'codemirror5', 'ace'}:
                        title = visible_window(capture.pids())['name']
                        name = {'codemirror6': 'CodeMirror 6', 'codemirror5': 'CodeMirror 5',
                                'ace': 'Ace'}[editor['id']]
                        if title != f"{name} - {fixture.name}":
                            raise RuntimeError(f'{name} did not finish loading fixture {fixture.name}: {title!r}')
                    focus(window)
                    if editor['id'] == 'zed':
                        ctrl(25)  # P
                        subprocess.run(['ydotool', 'type', '--key-delay', '1', fixture.name], check=True)
                        time.sleep(.5)
                        key('28:1', '28:0')
                        time.sleep(zed_settle_seconds)
                        title = visible_window(capture.pids())['name']
                        if fixture.name not in title:
                            raise RuntimeError(f'Zed did not open fixture {fixture.name}: {title!r}')
                    x, y = selection_coordinates(editor['id'])
                    focus_x, focus_y = focus_click_coordinates(editor['id'])
                    details.update(selectionCoordinates=[x, y], focusClickCoordinates=[focus_x, focus_y],
                                   targetText=SELECTION_TEXT, stage='focus-input')
                    move_pointer(window, focus_x, focus_y)
                    click()
                    move_pointer(window, x, y)
                    pids = capture.pids()
                    focused = focused_window(pids)
                    if not focused:
                        raise RuntimeError('focus is outside native editor cgroup')
                    focused_window_id, focused_pid = focused['id'], focused['pid']
                    details.update(focusWindow=focused_window_id, focusPid=focused_pid, pids=pids, stage='input')
                    if focused_pid not in pids:
                        raise RuntimeError(f'focus is outside editor cgroup: window={focused_window_id}, pid={focused_pid}')
                    time.sleep(.5)
                    if warm:
                        # Diagnostic only: exercise edit work, then restore the fixture.
                        key('107:1', '107:0')
                        key('14:1', '14:0')
                        time.sleep(1)
                        ctrl(44)
                        time.sleep(1)
                        ctrl(31)
                        time.sleep(1)
                        if fixture.read_text() != FIXTURE_CONTENT:
                            raise RuntimeError('warm-up undo did not restore fixture contents')
                    clipboard_sentinel = CLIPBOARD_SENTINEL_PREFIX + uuid.uuid4().hex
                    set_clipboard(clipboard_sentinel)
                    clipboard_before = read_clipboard()
                    if clipboard_before != clipboard_sentinel:
                        raise RuntimeError('clipboard sentinel was not installed before selection')
                    capture.start()
                    measured_started = time.monotonic()
                    if idle:
                        # Diagnostic only: retain the double-click driver's elapsed time without an event.
                        time.sleep(.2)
                    else:
                        subprocess.run(double_click_command(), check=True)
                    input_sent = time.monotonic()
                    remaining = input_sent + RESPONSE_WINDOW_SECONDS - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    capture.cutoff()
                    result = capture.finish()
                    result.update(valid=True, readiness=window,
                                  displayBackend='wayland', inputDriver='ydotool',
                                  focusedNativeWindow=focused,
                                  diagnosticWarmup=warm,
                                  selectionText=None if idle else SELECTION_TEXT,
                                  doubleClickDelayMilliseconds=DOUBLE_CLICK_DELAY_MS,
                                  clipboardBefore=clipboard_before, focusWindow=focused_window_id,
                                  focusPid=focused_pid, pidsAtInput=pids,
                                  inputDispatchSeconds=input_sent-measured_started,
                                  inputToCutoffSeconds=capture.disabled_at-input_sent,
                                  measurementSeconds=capture.disabled_at-measured_started,
                                  command=command)
                    if editor['id'] == 'zed':
                        result['postOpenSettleSeconds'] = zed_settle_seconds
                    details.update(measurement={key: result.get(key) for key in
                                    ('instructions', 'measurementSeconds', 'inputDispatchSeconds', 'inputToCutoffSeconds')},
                                   stage='verify-selection')
                    if not idle:
                        ctrl(46)
                        copied = clipboard_after_copy(clipboard_sentinel)
                        if copied != SELECTION_TEXT or copied == clipboard_sentinel:
                            raise RuntimeError(f'selection mismatch: expected {SELECTION_TEXT!r}, got {copied!r}')
                        ctrl(30)
                        ctrl(46)
                        document_after = clipboard_after_copy(SELECTION_TEXT)
                        if document_after != FIXTURE_CONTENT:
                            raise RuntimeError('editor document contents changed after selection')
                    else:
                        document_after = FIXTURE_CONTENT
                    contents_after = fixture.read_text()
                    content_digest_after = hashlib.sha256(contents_after.encode()).hexdigest()
                    if content_digest_after != fixture_digest or contents_after != FIXTURE_CONTENT:
                        raise RuntimeError('selection modified the fixture document')
                    details.update(clipboardAfter=None if idle else copied,
                                   documentContentSha256=hashlib.sha256(document_after.encode()).hexdigest(),
                                   fixtureContentSha256=content_digest_after)
                    result.update(selection=None if idle else copied,
                                  clipboardAfter=None if idle else copied,
                                  clipboardSentinel=clipboard_sentinel,
                                  focusClickCoordinates=[focus_x, focus_y],
                                  selectionCoordinates=[x, y],
                                  documentContentSha256=hashlib.sha256(document_after.encode()).hexdigest(),
                                  fixtureContentSha256=content_digest_after,
                                  fixtureUnchanged=contents_after == FIXTURE_CONTENT)
            child.wait(timeout=5)
            return result
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return {'valid': False, 'error': str(error), 'command': command,
                    'diagnostics': {**details, **failure_diagnostics},
                    **{name: (d / name).read_text(errors='replace')[-8000:]
                       for name in ['stdout', 'stderr', 'perf.log'] if (d / name).exists()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error('at least three trials required')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == args.editor)
    host = {'machine': platform.machine(), 'kernel': platform.release(), 'cpuinfo': Path('/proc/cpuinfo').read_text(),
            'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
            'eventSources': sorted(p.name for p in Path('/sys/bus/event_source/devices').iterdir()),
            'paranoid': Path('/proc/sys/kernel/perf_event_paranoid').read_text().strip(),
            'editorUid': int(os.environ.get('SUDO_UID', os.getuid())),
            'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')}
    host['desktop'] = {'compositor': subprocess.check_output(['sway', '--version'], text=True).strip(),
                       'inputDriver': 'ydotool 1.0.4', 'xwayland': False,
                       'inputs': json.loads(Path('results/wayland-inputs.json').read_text())}
    if host['machine'] != 'aarch64':
        raise SystemExit('requires Linux ARM64')
    if editor.get('package'):
        host['packageVersion'] = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', editor['package']], text=True)
    zed_settle_seconds = zed_post_open_settle_seconds(os.environ.get(
        'BENCHMARK_ZED_POST_OPEN_SETTLE_SECONDS', str(DEFAULT_ZED_POST_OPEN_SETTLE_SECONDS)))
    trials = [dict(trial(editor, args.perf, zed_settle_seconds), repeat=i+1) for i in range(args.repeats)]
    Path('results').mkdir(exist_ok=True)
    Path('results/results.json').write_text(json.dumps(dict(editor=editor, protocol=PROTOCOL, host=host, trials=trials), indent=2)+'\n')
    if not all(t['valid'] for t in trials):
        raise SystemExit('invalid trials; cannot publish complete report')

if __name__ == '__main__':
    main()
