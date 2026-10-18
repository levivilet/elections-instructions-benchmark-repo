#!/usr/bin/env bash
set -euo pipefail
openbox > /tmp/startup-openbox.log 2>&1 &
desktop_pid=$!
trap 'kill "$desktop_pid" 2>/dev/null || true' EXIT
# The desktop is outside the measured editor cgroup and ready before launch.
for attempt in {1..100}; do
  if xprop -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'; then break; fi
  sleep .05
done
sudo --preserve-env=DISPLAY,XAUTHORITY,BENCHMARK_RUN_URL,BENCHMARK_COMMIT,BENCHMARK_ZED_POST_OPEN_SETTLE_SECONDS python3 scripts/benchmark.py --editor "$1" --perf "$2" --repeats 3
