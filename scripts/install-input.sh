#!/usr/bin/env bash
set -euo pipefail
# Noble ships an older ydotool CLI. Pin the daemon-based API used by this harness.
mkdir -p .tmp
curl --fail --location --retry 3 https://codeload.github.com/ReimuNotMoe/ydotool/tar.gz/refs/tags/v1.0.4 -o .tmp/ydotool-v1.0.4.tar.gz
echo 'ba075a43aa6ead51940e892ecffa4d0b8b40c241e4e2bc4bd9bd26b61fde23bd  .tmp/ydotool-v1.0.4.tar.gz' | sha256sum --check
tar -xzf .tmp/ydotool-v1.0.4.tar.gz -C .tmp
cmake -S .tmp/ydotool-1.0.4 -B .tmp/ydotool-build
cmake --build .tmp/ydotool-build --parallel 2
