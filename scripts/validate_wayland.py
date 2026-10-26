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
        from gi.repository import Gtk, GLib
        window = Gtk.Window(title='Wayland input acceptance')
        view = Gtk.TextView()
        view.set_monospace(True)
        view.get_buffer().set_text(TEXT)
        def record_click(widget, event):
            event_data = dict(type=str(event.type), x=event.x, y=event.y)
            def record_selection():
                buffer = view.get_buffer()
                bounds = buffer.get_selection_bounds()
                selection = buffer.get_text(*bounds, True) if bounds else None
                with Path('results/wayland-client-events.jsonl').open('a') as stream:
                    stream.write(json.dumps(dict(event_data, selection=selection)) + '\n')
                return False
            GLib.idle_add(record_selection)
            return False
        view.connect('button-press-event', record_click)
        window.add(view)
        window.connect('destroy', Gtk.main_quit)
        window.show_all()
        def report_target():
            rect = view.get_iter_location(view.get_buffer().get_iter_at_offset(6))
            x, y = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET,
                                               rect.x + rect.width // 2,
                                               rect.y + rect.height // 2)
            target = view.translate_coordinates(window, x, y)
            Path('results/wayland-client-target.json').write_text(json.dumps(
                dict(target=target, glyph=dict(x=rect.x, y=rect.y, width=rect.width,
                                              height=rect.height),
                     allocation=[view.get_allocated_width(), view.get_allocated_height()])))
            return False
        GLib.timeout_add(100, report_target)
        Gtk.main()
        return
    for artifact in ['wayland-client-target.json', 'wayland-client-events.jsonl']:
        Path('results', artifact).unlink(missing_ok=True)
    child = subprocess.Popen([sys.executable, __file__, '--client'], env={**os.environ, 'GDK_BACKEND': 'wayland'})
    try:
        deadline = time.monotonic() + 15
        while True:
            window = visible_window([child.pid])
            if window and Path('results/wayland-client-target.json').exists():
                break
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('native test client did not appear')
            time.sleep(.05)
        layout = json.loads(Path('results/wayland-client-target.json').read_text())
        x, y = layout['target']
        focus(window)
        time.sleep(.2)
        # Keep the focus click away from the measured double-click so GTK
        # does not interpret the three presses as a line-selecting triple-click.
        move_pointer(window, x + 80, y)
        click()
        move_pointer(window, x, y)
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
            dict(valid=True, window=window, focused=focused, selection=selected, layout=layout,
                 inputDriver='ydotool 1.0.4', display='native Wayland'), indent=2) + '\n')
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == '__main__':
    main()
