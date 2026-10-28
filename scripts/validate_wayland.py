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
        import ctypes
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib, GObject
        window = Gtk.Window(title='Wayland input acceptance')
        view = Gtk.TextView()
        view.set_monospace(True)
        view.get_buffer().set_text(TEXT)
        # GTK represents mouse sequences as NULL, but GTK3's introspection
        # annotation incorrectly rejects None for get_sequence_state().
        gtk = ctypes.CDLL('libgtk-3.so.0')
        gtk.gtk_gesture_get_sequence_state.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gtk.gtk_gesture_get_sequence_state.restype = ctypes.c_int
        pointer = ctypes.pythonapi.PyCapsule_GetPointer
        pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
        pointer.restype = ctypes.c_void_p
        def record(data):
            with Path('results/wayland-client-events.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(data, monotonic=time.monotonic())) + '\n')
        observed_gestures = set()
        def record_gesture(gesture, n_press, x, y):
            if gesture.get_widget() != view:
                return True
            if gesture not in observed_gestures:
                observed_gestures.add(gesture)
                gesture.connect('stopped', lambda current: record(dict(gesture='stopped')))
                gesture.connect('sequence-state-changed', lambda current, sequence, state:
                                record(dict(gesture='sequence-state-changed', state=int(state))))
            sequence = gesture.get_current_sequence()
            event = gesture.get_last_event(sequence)
            source = event.get_source_device() if event else None
            record(dict(gesture='pressed', count=n_press, x=x, y=y,
                        sequenceState=(str(gesture.get_sequence_state(sequence)) if sequence else
                                       gtk.gtk_gesture_get_sequence_state(pointer(gesture.__gpointer__, None), None)),
                        active=gesture.is_active(), recognized=gesture.is_recognized(),
                        button=gesture.get_current_button(),
                        eventTime=event.get_time() if event else None,
                        source=str(source.get_source()) if source else None,
                        sourceName=source.get_name() if source else None))
            return True
        GObject.add_emission_hook(Gtk.GestureMultiPress, 'pressed', record_gesture)
        settings = view.get_settings()
        record(dict(doubleClickTime=settings.get_property('gtk-double-click-time'),
                    doubleClickDistance=settings.get_property('gtk-double-click-distance')))
        def record_click(widget, event):
            event_data = dict(type=str(event.type), x=event.x, y=event.y,
                              button=event.button, state=int(event.state), time=event.time,
                              textWindow=event.window == view.get_window(Gtk.TextWindowType.TEXT))
            def record_selection():
                buffer = view.get_buffer()
                bounds = buffer.get_selection_bounds()
                selection = buffer.get_text(*bounds, True) if bounds else None
                record(dict(event_data, selection=selection))
                return False
            GLib.idle_add(record_selection)
            return False
        view.connect('button-press-event', record_click)
        view.connect('button-release-event', record_click)
        def record_mark(buffer, location, mark):
            bounds = buffer.get_selection_bounds()
            with Path('results/wayland-client-events.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(mark=mark.get_name(), offset=location.get_offset(),
                    selection=buffer.get_text(*bounds, True) if bounds else None)) + '\n')
        view.get_buffer().connect('mark-set', record_mark)
        scroller = Gtk.ScrolledWindow()
        scroller.add(view)
        window.add(scroller)
        window.set_default_size(1280, 800)
        view.get_buffer().place_cursor(view.get_buffer().get_start_iter())
        window.connect('destroy', Gtk.main_quit)
        window.show_all()
        view.grab_focus()
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
        # Establish native pointer focus separately: a cold GTK client can
        # consume the first injected press during activation. Keep this click
        # spatially separate and beyond GTK's multi-press interval, so the
        # following double-click cannot become a triple-click.
        move_pointer(window, x + 80, y)
        click()
        time.sleep(.6)
        move_pointer(window, x, y)
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
