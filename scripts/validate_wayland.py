"""Fail closed unless the headless compositor actually receives ydotool input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from wayland import (click, clipboard_after_copy, ctrl, double_click_command,
                     focus, focused_window, move_pointer, read_clipboard,
                     set_clipboard, visible_window)

TEXT = ('selectiontarget ' * 8 + '\n') * 60


def main():
    if '--client' in sys.argv:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        window = Gtk.Window(title='Wayland input acceptance')
        view = Gtk.TextView()
        view.set_monospace(True)
        view.get_buffer().set_text(TEXT)
        window.add(view)
        window.connect('destroy', Gtk.main_quit)
        window.show_all()
        Gtk.main()
        return
    child = subprocess.Popen([sys.executable, __file__, '--client'], env={**os.environ, 'GDK_BACKEND': 'wayland'})
    try:
        deadline = time.monotonic() + 15
        while True:
            window = visible_window([child.pid])
            if window:
                break
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('native test client did not appear')
            time.sleep(.05)
        focus(window)
        time.sleep(.2)
        # Keep the focus click away from the measured double-click so GTK
        # does not interpret the three presses as a line-selecting triple-click.
        move_pointer(window, 280, 160)
        click()
        move_pointer(window, 200, 160)
        time.sleep(.2)
        focused = focused_window([child.pid])
        if not focused:
            raise RuntimeError('test client did not receive focus')
        set_clipboard('wayland-input-sentinel')
        if read_clipboard() != 'wayland-input-sentinel':
            raise RuntimeError('test clipboard sentinel unavailable')
        subprocess.run(double_click_command(), check=True)
        time.sleep(.2)
        ctrl(46)  # C
        selected = clipboard_after_copy('wayland-input-sentinel')
        if selected != 'selectiontarget':
            raise RuntimeError(f'ydotool pointer/double-click did not reach test client: {selected!r}')
        ctrl(30)  # A
        ctrl(46)
        if clipboard_after_copy(selected) != TEXT:
            raise RuntimeError('ydotool keyboard did not select the full test document')
        Path('results/wayland-acceptance.json').write_text(json.dumps(
            dict(valid=True, window=window, focused=focused, selection=selected,
                 inputDriver='ydotool 1.0.4', display='native Wayland'), indent=2) + '\n')
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == '__main__':
    main()
