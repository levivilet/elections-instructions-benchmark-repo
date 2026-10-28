# Selection instruction investigation

Earlier in the investigation, the retained candidate used a median
**54,944,838** instructions versus CodeMirror 5's **24,589,256** (2.23 times
the count) in [run 40630945918](https://github.com/levivilet/selections-instructions-benchmark/actions/runs/40630945918).
That candidate substituted identified source bundles into the pinned LVCE
v0.126.12 artifact and was not a released LVCE build.

## Final released-artifact validation

The released performance target remains **not met**. The official ARM64 Debian
artifact LVCE v0.126.14 used a median **52,341,148** instructions versus
CodeMirror 5's **23,955,518**, a **28,385,630** instruction gap (2.18 times the
count). The complete eight-editor report and all benchmark gates passed in
[run 40688717414](https://github.com/levivilet/selections-instructions-benchmark/actions/runs/40688717414).
The measured artifact was
[`lvce-v0.126.14_arm64.deb`](https://github.com/lvce-editor/lvce-editor/releases/download/v0.126.14/lvce-v0.126.14_arm64.deb),
SHA-256 `bb4feef2fddee941f4fa609eaa154ed4a083f7ccb9c9c2bc4c28493e8085a3d6`.

Each editor used three fresh-profile native Wayland double-click trials with a
one-second response window and per-CPU cgroup `perf stat` instruction counts.
All LVCE and CodeMirror 5 trials selected `selectiontarget`; document and fixture
SHA-256 was `c71260efceaabfa62611f7e1d3af4502dbd1290f7a1372fd4ea11fe1dcf27f36`,
and the fixture remained unchanged. The three LVCE counts were 52,341,148,
53,833,349 and 51,269,329. The CodeMirror 5 counts were 23,930,933, 24,101,331
and 23,955,518. The one-second counter boundary and raw-counter accounting
guards passed.

The source improvements and required dependencies are integrated in the merged
LVCE v0.126.14 release. This merge continuation completed the requested
integration work; it did not restart the prior optimization investigation or
close the remaining CodeMirror 5 performance gap.

## What the measurements establish

CodeMirror 5 handles selection in its renderer: it updates the selection model
and cursor/selection DOM. LVCE also coordinates renderer-worker, editor-worker,
status-bar and main-area state, asynchronous messages, DOM patches and renderer
transactions. Its native profiles contain allocation, garbage collection,
compilation, message serialization and rasterization work. A faster virtual DOM
microbenchmark therefore does not imply an equally large whole-editor gain.

Native-only instruction samples across three earlier captures counted 485
samples on LVCE dedicated workers and 330 on thread-pool threads, out of 1,493
samples. CodeMirror 5 had no dedicated-worker samples and 78 thread-pool samples
out of 706. Symbols resolved against the matching Electron 44.9.2 binary show
that thread-pool work mixes V8 scavenging, baseline compilation and text
rasterization; it cannot all be assigned to rendering or to one worker.

A separate diagnostic capture uses URL markers to identify OS worker threads,
without enabling V8's CPU sampler. Across its three captures, the editor worker
has 198 native instruction samples, renderer-worker 170, status-bar-worker 79,
and main-area-worker 30. The page has 383 and thread-pool threads 371. These
sampled diagnostic proportions point to work beyond editor DOM construction;
they are not exact additive component counters or acceptance measurements.

Enabling the CPU sampler on every isolate dramatically perturbs native counts:
three captures contained 83,616 `v8:ProfEvntProc` samples, and even idle workers
had hundreds of samples per capture. Those captures must not rank workers by
native cost. CPU/heap profiles can identify candidate paths, but unchanged
native stat trials must measure each optimization.

## Comparable experiments

Each row below compares variants within one context. Counts are medians of
three trials. Results from different frontend versions or runs must not be
added or treated as one cumulative speedup.

| Change | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| Combined pointer edit/render call, v0.126.0 frontend | 58.82M | 53.17M | Promising reduction, with substantial variance; preserves split-editor fallback and command ordering. |
| Asynchronous status delivery | 54.40M | 54.35M | No meaningful instruction reduction; removes an edit-queue wait. |
| Skip identical CSS stylesheet replacement | 55.24M | 53.49M | Noisy native effect; traces confirm redundant group stylesheet invalidations disappear. |
| Avoid copying the full editor state for render options | 56.46M | 56.35M | No meaningful native effect despite a 9.7% local render-construction microbenchmark improvement. |
| Published status-worker 3.35.3 position-width policy | 56.20M | 53.00M | All paired trials lower; retains real text measurement and stable-label persistence. This fix was already published upstream and is reused. |
| Skip unchanged main-area CSS and empty transactions | 51.80M | 53.05M | Mixed pairs; repeated comparison 53.18M to 53.06M also fails to establish a reliable whole-editor win. |

The paired trials alternate control/candidate order, use fresh application
profiles, record bundle hashes and source revisions, and restore every
substituted bundle even on failure. The historical baseline, dependency-only,
token, property-diff, flat-diff and row-cache controls retain their original
v0.126.0 frontend. Current controls share the v0.126.12 frontend. An old worker
failed selection against the newer frontend, so those contexts are installed
separately instead of accepting invalid trials or silently skipping controls.

The source candidates also avoid ordinary-token boundary allocations, cache
unchanged row nodes, and skip fully identical virtual subtrees. The subtree
check examines the entire subtree: shared parent identity alone is insufficient.
Rendering/minimap regressions and in-place token mutation remain covered.

The rejected plain-gutter experiment removed empty decoration filtering and unused
breakpoint-label/decorated-node work for ordinary line numbers. A local warmed
microbenchmark, 20,000 constructions of a 40-row gutter, improved from a median
751.69 ms to 186.89 ms. One thousand differential cases matched the original
output, including hidden numbers, active lines, breakpoints, lightbulbs,
decorations and merge-conflict rows. Its matched native median nevertheless rose
from **49,604,107** to **53,965,282** instructions in
[run 40633381302](https://github.com/levivilet/selections-instructions-benchmark/actions/runs/40633381302).
Individual paired changes were +4.46M, -0.76M and +4.32M. The full candidate
measured 52,929,948 versus CodeMirror 5's 23,831,783. The experiment was reverted;
the local microbenchmark did not establish a whole-editor benefit.

## Rejected shortcuts and validation scope

Diagnostic status suppression showed that status work is material, but removes
a user-visible feature and is never a candidate for release. Disabling provider
work or AI root selectors did not explain the whole gap. Promoting selection or
text layers and forcing immediate pointer commits increased native instruction
counts; the rejected layer experiments also increased process memory by several
MiB. None is enabled in the current product candidate.

The benchmark retains its original native Wayland double-click, 100 ms press
interval, acknowledged counter boundaries, one-second response window, fresh
HOME/XDG profiles, exact selected-word check, whole-document and fixture digests,
and raw-counter validation. All eight editor jobs, hardware accounting and the
complete report remain required. Failed GTK input preflights invalidate a job;
they are not accepted as editor measurements. A preserved intermittent failure
shows GDK delivering both clicks while GTK's internal multipress gestures reset;
its root cause remains unresolved.

Independent response observations require the correct `15 selected` status and
selection DOM. Page-clock DOM mutations and animation-frame callbacks do not
measure compositor presentation and do not establish a matched latency win.

The earlier diagnosis proposed inspecting marker-only thread attribution with
allocation call trees. The later instruction was to merge the measured
improvements so far, not to restart that optimization work. Any further attempt
to close the remaining performance gap needs a new discriminating experiment or
an explicitly extended investigation budget.

The earlier diagnostic comparison uses an immutable copy of the worker bundled
with the pinned release as its control. Both sides use that same official
frontend; the control keeps all original worker/page bundles, while the
candidate applies all retained identified producers. This compares the retained
combination against the unmodified release rather than comparing two identical
workers after the gutter revert. The final released-artifact measurement above
provides separate validation of the official Debian build.

Retained source work was merged through
[editor-worker#1680](https://github.com/lvce-editor/editor-worker/pull/1680),
[virtual-dom#602](https://github.com/lvce-editor/virtual-dom/pull/602),
[renderer-process#965](https://github.com/lvce-editor/renderer-process/pull/965),
and [main-area-worker#865](https://github.com/lvce-editor/main-area-worker/pull/865).
The consumer integration [lvce-editor#16346](https://github.com/lvce-editor/lvce-editor/pull/16346)
is merged and published as v0.126.14. Its released dependencies include
virtual-dom 11.25.2, editor-worker 19.78.7, renderer-process 30.81.2,
main-area-worker 9.77.5, and status-bar-worker 3.35.3. Official Debian artifact,
selection behavior, and required source/consumer CI gates passed. The performance
target remains outstanding as measured above.
