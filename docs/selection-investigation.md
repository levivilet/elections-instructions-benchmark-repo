# Selection instruction investigation

The performance target is **not met**. In the last measured full candidate,
LVCE used a median **54,944,838** instructions versus CodeMirror 5's
**24,589,256**, a **30,355,582** instruction gap (2.23 times the count).
These are fresh-profile ARM measurements from
[run 40630945918](https://github.com/levivilet/selections-instructions-benchmark/actions/runs/40630945918).
The candidate substitutes identified source bundles into the pinned LVCE
v0.126.12 artifact; it is not a released LVCE build and is excluded from the
published editor comparison. Producer releases, consumer integration and final
official Debian validation remain outstanding.

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

The latest plain-gutter candidate removes empty decoration filtering and unused
breakpoint-label/decorated-node work for ordinary line numbers. A local warmed
microbenchmark, 20,000 constructions of a 40-row gutter, improved from a median
751.69 ms to 186.89 ms. One thousand differential cases matched the original
output, including hidden numbers, active lines, breakpoints, lightbulbs,
decorations and merge-conflict rows. **Its matched native measurement is pending;
the local result is not a whole-editor performance claim.**

## Rejected shortcuts and remaining validation

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

Continuation should inspect marker-only thread attribution together with the
allocation call trees, then test one behavior-preserving change at a time. Keep
all producer and consumer CI gates, publish only required merged dependencies,
and rerun the final comparison using an actual official LVCE Debian artifact.
Do not declare the task complete while the fresh-profile median exceeds
CodeMirror 5 or while release/integration acceptance is unfinished.
