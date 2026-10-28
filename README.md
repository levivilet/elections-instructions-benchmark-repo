# Selection instructions benchmark

Compare retired CPU instructions executed while an editor selects a word after a
real double-click on Linux ARM64. The chart reports each editor's median from
three fresh-profile trials, ordered from fewest instructions to most.

The latest complete run is published at
[GitHub Pages](https://levivilet.github.io/selections-instructions-benchmark/).
Each result includes the raw hardware counter, exact trial boundaries, host
metadata, clipboard evidence, and a digest proving the document stayed intact.
Incomplete matrices and invalid selections are never published.

## Measurement protocol

- Linux ARM64, Ubuntu 24.04 ARM, native Wayland (headless Sway + libinput), one editor per runner. The matrix is
  LVCE Editor, VS Code, Zed, Lapce, Basic Electron, CodeMirror 6,
  CodeMirror 5, and Ace in Electron. Geany is omitted because its native Wayland
  launch has not been validated.
- Each trial opens a fresh text fixture using a fresh HOME and XDG profile. Its
  short rows repeat the unique word `selectiontarget` so every supported editor
  has text beneath the measured pointer coordinates without soft-wrapped lines.
  Startup and settling happen with counters disabled.
- The external input driver activates the editor window, positions the pointer,
  verifies focus belongs to the editor cgroup, and installs a unique clipboard
  sentinel. Before counting, it confirms the sentinel is the current clipboard.
- `perf stat` counts retired user and kernel instructions for the editor's
  cgroup. Counting begins immediately before a native Wayland double-click via ydotool (two clicks
  100 ms apart) and ends after a fixed one-second response window. The editor
  and its descendants are included. Background work during that window is
  included.
- Launch, fixture setup, pointer positioning, focus setup, sentinel setup, the
  external input driver, selection/document verification, cleanup, the desktop,
  seatd, ydotoold, and the Wayland compositor are outside the counted cgroup. Counter control latency
  through acknowledgement is included.
- After counter cutoff, the harness copies the selection and requires exactly
  `selectiontarget`; stale clipboard content, a partial selection, wrong text,
  focus loss, unsupported or multiplexed counters, or an incomplete editor
  matrix invalidates the trial. It then copies the whole document and verifies
  its exact contents, and checks the fixture bytes remain unchanged.
- Three trials per editor use fresh application profiles. The chart compares
  median instruction counts. OS filesystem caches may be warm.
- Editor downloads are pinned by version and SHA-256 in
  `config/editors.lock.json`. Raw JSON retains counts, timings, host and protocol
  metadata, counter output, exact selection evidence, and document integrity.
- The CodeMirror 6, CodeMirror 5, and Ace comparisons use pinned minimal text
  editors without language services; their versions and configurations are
  recorded with each benchmark result.

The one-second response window measures the double-click and immediate editor
work while disclosing background work included during that interval.

## Run locally

The same ARM64 hardware-counter access and pinned editor packages used by CI
are required. On an Ubuntu ARM64 runner with `perf` and the desktop dependencies
installed (Sway, seatd, wl-clipboard, cmake, scdoc, python3-gi and
`gir1.2-gtk-3.0`; see the workflow for the full package list). Use a dedicated
host with `/dev/uinput` and no existing seatd session:

```sh
python3 scripts/install.py
python3 -m unittest discover -s tests
sudo sysctl kernel.perf_event_paranoid=-1
PERF=$(find -L /usr/lib/linux-tools -name perf -type f | sort | tail -n 1)
bash scripts/install-input.sh
bash scripts/run.sh lvce "$PERF"
```

Use `python3 scripts/benchmark.py --help` to see trial options.

The harness pins ydotool 1.0.4 by SHA-256 because Ubuntu 24.04 packages an older
CLI. Sway combines headless output with a libinput seat supplied by seatd
(`SEATD_VTBOUND=0`); Xwayland is disabled. An input preflight requires the
virtual keyboard and pointer to appear in compositor IPC and a native GTK
client to receive the double-click, copy the exact word, and copy its intact
document. Its evidence and desktop logs are uploaded with every editor result.
Pointer setup uses acknowledged compositor pixel coordinates; ydotool injects
all button and keyboard events. Setup remains outside the measurement window.
Each editor must expose an `xdg_shell` window and focused PID in its cgroup.
Fresh per-trial runtime directories connect to the shared compositor socket
explicitly, while all application HOME/XDG state remains isolated.

## CI

Every pull request runs hardware-accounting acceptance, all eight editor trials,
and the complete-report regression checks on the ARM runner matrix. Main pushes,
scheduled runs, and manual runs publish three trials per editor and deploy the
validated report to GitHub Pages. CI does not publish a partial matrix.

The [selection investigation](docs/selection-investigation.md) records the
source candidates, comparable measurements, diagnostic limits and remaining
performance acceptance work.
