"""Native Wayland discovery and external ydotool input (outside editor cgroups)."""
import json
import subprocess
import time

DOUBLE_CLICK_DELAY_MS = 100


def sway(*args):
    return json.loads(subprocess.check_output(['swaymsg', '-r', *args], text=True, timeout=5))


def nodes(tree):
    yield tree
    for child in tree.get('nodes', []) + tree.get('floating_nodes', []):
        yield from nodes(child)


def main_windows(tree, pids):
    # Xwayland is disabled too; require the compositor's positive native-shell
    # evidence rather than assuming an unset DISPLAY proves native Wayland.
    return [node for node in nodes(tree)
            if node.get('pid') in pids and node.get('shell') == 'xdg_shell'
            and node.get('visible') and node.get('type') == 'con'
            and not node.get('window_properties', {}).get('transient_for')]


def visible_window(pids):
    windows = main_windows(sway('-t', 'get_tree'), pids)
    return windows[0] if windows else None


def focused_window(pids):
    return next((node for node in main_windows(sway('-t', 'get_tree'), pids)
                 if node.get('focused')), None)


def focus(window):
    reply = sway(f"[con_id={int(window['id'])}] focus")
    if not all(item.get('success') for item in reply):
        raise RuntimeError(f'could not focus native window: {reply}')


def key(*codes):
    subprocess.run(['ydotool', 'key', *codes], check=True, timeout=5)


def ctrl(code):
    key('29:1', f'{code}:1', f'{code}:0', '29:0')


def move_pointer(window, x, y):
    # Pointer setup is excluded from counting. ydotool 1.0.4's --absolute
    # simulates a reset using INT32_MIN relative motion; libinput can discard
    # that motion. Use the compositor's acknowledged pixel coordinates instead.
    rect = window['rect']
    reply = sway(f"seat seat0 cursor set {rect['x'] + x} {rect['y'] + y}")
    if not all(item.get('success') for item in reply):
        raise RuntimeError(f'could not position pointer: {reply}')


def click():
    subprocess.run(['ydotool', 'click', '0xC0'], check=True, timeout=5)


def double_click_command():
    # v1.0.4 delays after BOTH button transitions: 50 ms down + 50 ms up
    # keeps the press-to-press double-click interval at 100 ms.
    return ['ydotool', 'click', '--repeat', '2', '--next-delay',
            str(DOUBLE_CLICK_DELAY_MS // 2), '0xC0']


def read_clipboard():
    return subprocess.check_output(['wl-paste', '--no-newline'], text=True, timeout=5)


def clipboard_after_copy(previous, timeout=2.0):
    deadline = time.monotonic() + timeout
    while True:
        copied = read_clipboard()
        if copied != previous or time.monotonic() >= deadline:
            return copied
        time.sleep(.02)


def set_clipboard(text):
    subprocess.run(['wl-copy', '--type', 'text/plain;charset=utf-8'],
                   input=text, text=True, check=True, timeout=5)
