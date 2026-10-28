#!/usr/bin/env bash
set -euo pipefail
# Run on a dedicated host: seatd/uinput are host devices, not nested X11 input.
# Never take over an existing desktop's seat daemon.
if [ -S /run/seatd.sock ]; then
  echo 'An existing seatd owns this host; use a dedicated benchmark runner.' >&2
  exit 1
fi
mkdir -p results
runtime=$(mktemp -d /tmp/selection-wayland.XXXXXX)
chmod 700 "$runtime"
export XDG_RUNTIME_DIR="$runtime"
export PATH="$PWD/.tmp/ydotool-build:$PATH"
export YDOTOOL_SOCKET="$runtime/ydotool.sock"
export SWAYSOCK="$runtime/sway.sock"
unset WAYLAND_DISPLAY
unset DISPLAY WAYLAND_SOCKET DBUS_SESSION_BUS_ADDRESS
pids=()
cleanup() {
  trap - EXIT
  # Close clients/compositor before the input and seat daemons they depend on.
  for ((index=${#pids[@]}-1; index>=0; index--)); do
    pid=${pids[index]}
    sudo kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  rm -rf "$runtime"
}
trap cleanup EXIT
sudo modprobe uinput
sudo env SEATD_VTBOUND=0 seatd -u "$(id -un)" > results/seatd.log 2>&1 &
pids+=("$!")
sudo env -u DISPLAY "$PWD/.tmp/ydotool-build/ydotoold" --socket-path="$YDOTOOL_SOCKET" --socket-own="$(id -u):$(id -g)" > results/ydotoold.log 2>&1 &
pids+=("$!")
cat > "$runtime/sway.conf" <<EOF
xwayland disable
output HEADLESS-1 mode 1280x800@60Hz scale 1
seat seat0 fallback true
input * xkb_layout us
input type:pointer accel_profile flat
input type:pointer pointer_accel 0
focus_follows_mouse no
mouse_warping none
default_border none
for_window [shell="xdg_shell"] fullscreen enable
EOF
for attempt in {1..100}; do
  if [ -S /run/seatd.sock ] && [ -S "$YDOTOOL_SOCKET" ]; then break; fi
  sleep .05
done
# libinput supplies the uinput device; headless supplies only the output.
env WLR_BACKENDS=headless,libinput WLR_RENDERER=pixman LIBSEAT_BACKEND=seatd \
  sway --config "$runtime/sway.conf" --debug > results/sway.log 2>&1 &
pids+=("$!")
ready=false
for attempt in {1..200}; do
  for socket in "$runtime"/wayland-*; do
    if [ -S "$socket" ]; then export WAYLAND_DISPLAY="${socket##*/}"; fi
  done
  if [ -S "$SWAYSOCK" ] && [ -n "${WAYLAND_DISPLAY:-}" ] && \
      swaymsg -r -t get_inputs | python3 -c 'import json,sys; inputs=json.load(sys.stdin); assert {"keyboard","pointer"} <= {i["type"] for i in inputs if i["name"] == "ydotoold virtual device"}'; then
    ready=true
    break
  fi
  sleep .05
done
if [ "$ready" != true ]; then
  swaymsg -r -t get_inputs > results/wayland-inputs.json || true
  cat results/wayland-inputs.json results/sway.log results/seatd.log results/ydotoold.log
  exit 1
fi
swaymsg -r -t get_inputs > results/wayland-inputs.json
# Prove client input, focus and clipboard before opening measured editors.
GDK_BACKEND=wayland python3 scripts/validate_wayland.py
if [ "${1:-}" != --validate-input ]; then
  # PAM may replace XDG_RUNTIME_DIR during sudo, even with --preserve-env.
  # Restore the compositor runtime after that transition, before Python builds
  # the absolute display socket used by each fresh application profile.
  sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET,BENCHMARK_RUN_URL,BENCHMARK_COMMIT,BENCHMARK_ZED_POST_OPEN_SETTLE_SECONDS \
    env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    python3 scripts/benchmark.py --editor "$1" --perf "$2" --repeats 3
  # Reuse this verified desktop: restarting uinput daemons on the same host
  # can leave compositor input state from the preceding session. Every editor
  # trial still launches a new process tree with a fresh application profile.
  if [ "${BENCHMARK_DIAGNOSE:-0}" = 1 ]; then
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/response.py --editor "$1" --perf "$2"
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET,BENCHMARK_JAVASCRIPT_PROFILE,BENCHMARK_ALLOCATION_PROFILE \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/diagnose.py --editor "$1" --perf "$2"
  fi
  if [ -n "${BENCHMARK_CANDIDATE_BUNDLE:-}" ]; then
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_BASELINE_BUNDLE" --revision a45322d60d92a59d80dec53d0b369338136145c7 --label baseline
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_DEPENDENCY_BUNDLE" --revision d4662b53cd745270bc4622b99d84d5eef218cdab --label dependency
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_TOKEN_BUNDLE" --revision "$BENCHMARK_CONTROL_REVISION" --label token
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_PROPERTY_BUNDLE" --revision "$BENCHMARK_CONTROL_REVISION" --label property --virtual-dom-revision d404c1bf0c5c2ec58e3407c0c5943477b03eb992
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_FLAT_BUNDLE" --revision "$BENCHMARK_CONTROL_REVISION" --virtual-dom-revision "$BENCHMARK_VIRTUAL_DOM_REVISION" --label flat
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_ROW_BUNDLE" --revision "$BENCHMARK_ROW_REVISION" --virtual-dom-revision "$BENCHMARK_VIRTUAL_DOM_REVISION" --label row
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION"
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --label paired-candidate --control-bundle "$BENCHMARK_MONOSPACE_CONTROL_BUNDLE" --control-revision "$BENCHMARK_CANDIDATE_REVISION" --control-pointer-render
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION" --label response --response
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION" --label allocations --allocation-profile
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION" --label native --native-profile
    sudo --preserve-env=PATH,XDG_RUNTIME_DIR,WAYLAND_DISPLAY,SWAYSOCK,YDOTOOL_SOCKET \
      env XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
      python3 scripts/candidate.py --perf "$2" --bundle "$BENCHMARK_CANDIDATE_BUNDLE" --css-dedup --renderer-process-revision "$BENCHMARK_RENDERER_PROCESS_REVISION" --pointer-render --renderer-revision "$BENCHMARK_RENDERER_REVISION" --revision "$BENCHMARK_CANDIDATE_REVISION" --virtual-dom-revision "$BENCHMARK_CANDIDATE_VIRTUAL_DOM_REVISION" --label cpu --cpu-profile
  fi
fi
