"""Build a static, validated report from the editor matrix artifacts."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import statistics
from counters import parse_counter
from benchmark import PROTOCOL, RESPONSE_WINDOW_SECONDS, selection_evidence_is_valid

ROOT = Path(__file__).resolve().parent.parent
EDITORS = json.loads((ROOT / "config/editors.lock.json").read_text())


def validate_result(data: dict, editor_id: str) -> dict:
    """Reject incomplete measurements instead of interpreting them as zero."""
    if not isinstance(data, dict) or not isinstance(data.get("editor"), dict) or data["editor"].get("id") != editor_id:
        raise ValueError(f"{editor_id}: result has the wrong editor identity")
    if data.get('protocol') != PROTOCOL:
        raise ValueError(f'{editor_id}: result does not use the current native Wayland protocol')
    trials = data.get("trials")
    if not isinstance(trials, list) or len(trials) < 3:
        raise ValueError(f"{editor_id}: result has no trials")
    for index, trial in enumerate(trials, 1):
        if not isinstance(trial, dict) or trial.get("valid") is not True:
            raise ValueError(f"{editor_id}: trial {index} is invalid")
        for key in ("instructions", "measurementSeconds", "inputDispatchSeconds", "inputToCutoffSeconds"):
            value = trial.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{editor_id}: trial {index} has invalid {key}")
        if (trial["instructions"] <= 0 or not isinstance(trial["instructions"], int)
                or trial["measurementSeconds"] < RESPONSE_WINDOW_SECONDS
                or trial["inputDispatchSeconds"] <= 0
                or trial["inputToCutoffSeconds"] < RESPONSE_WINDOW_SECONDS):
            raise ValueError(f"{editor_id}: trial {index} has out-of-range measurements")
        if (trial.get("cutoffAcknowledged") is not True or not trial.get("readiness")
                or trial.get("diagnosticWarmup", False)
                or trial.get("diagnosticCandidate")
                or not selection_evidence_is_valid(trial)
                or trial.get('displayBackend') != 'wayland'
                or trial.get('inputDriver') != 'ydotool'
                or trial.get('readiness', {}).get('shell') != 'xdg_shell'
                or trial.get('focusedNativeWindow', {}).get('shell') != 'xdg_shell'
                or trial.get('focusedNativeWindow', {}).get('focused') is not True
                or trial.get('focusedNativeWindow', {}).get('pid') != trial.get('focusPid')
                or not isinstance(trial.get("focusPid"), int)
                or trial.get("focusPid") not in trial.get("pidsAtInput", [])
                or parse_counter(trial.get("rawCounter", "")) != trial["instructions"]):
            raise ValueError(f"{editor_id}: invalid cutoff evidence or raw counter")
        if not isinstance(trial.get("source"), str) or not trial["source"]:
            raise ValueError(f"{editor_id}: trial {index} is missing its measurement source")
    if not isinstance(data.get("protocol"), dict) or not isinstance(data.get("host"), dict):
        raise ValueError(f"{editor_id}: result is missing protocol or host metadata")
    return data


def load_results(results_root: Path) -> list[dict]:
    """Load exactly one valid result for every editor in the checked-in matrix."""
    # upload-artifact stores paths relative to the uploaded directory, so the
    # benchmark's results/results.json becomes results.json in each artifact.
    paths = list(results_root.glob("selection-*/results.json"))
    found: dict[str, Path] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
            editor_id = data["editor"]["id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"malformed benchmark artifact {path}: {error}") from error
        if path.parent.name != f"selection-{editor_id}":
            raise ValueError(f"artifact directory does not match editor {editor_id}: {path}")
        if editor_id in found:
            raise ValueError(f"duplicate benchmark artifact for {editor_id}")
        found[editor_id] = path
    expected = {editor["id"] for editor in EDITORS}
    if found.keys() != expected:
        missing = sorted(expected - found.keys())
        extra = sorted(found.keys() - expected)
        raise ValueError(f"editor artifacts do not match the matrix (missing: {missing}; extra: {extra})")
    results = []
    for editor in EDITORS:
        editor_id = editor["id"]
        data = validate_result(json.loads(found[editor_id].read_text()), editor_id)
        if data["editor"] != editor or data["host"].get("machine") != "aarch64":
            raise ValueError(f"{editor_id}: version or architecture mismatch")
        if results and data["protocol"] != results[0]["data"]["protocol"]:
            raise ValueError("mixed measurement protocols")
        results.append({"editor": editor, "data": data})
    return results


def build_median_chart(entries: list[dict]) -> str:
    """Render a labeled SVG comparison with lower medians near the bottom."""
    sorted_entries = sorted(entries, key=lambda entry: entry["medianInstructions"])
    width, height = 1000, 470
    left, right, top, bottom = 64, 24, 38, 326
    plot_width, plot_height = width - left - right, bottom - top
    maximum = max((entry["medianInstructions"] for entry in entries), default=0)
    # Leave headroom above the largest observation and keep the all-zero chart
    # useful, while never clipping measurements above 100%.
    axis_max = max(1, math.ceil(maximum * 1.1))
    tick_count = 4
    x_step = plot_width / max(1, len(sorted_entries))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="chart-title chart-description" xmlns="http://www.w3.org/2000/svg">',
        '<title id="chart-title">Median instructions per word selection by editor</title>',
        '<desc id="chart-description">Each editor is labeled below its marker. '
        'The shared vertical scale starts at zero at the bottom, so lower markers '
        'represent fewer startup instructions. Values are also shown next to markers.</desc>',
    ]
    for tick in range(tick_count + 1):
        value = axis_max * tick / tick_count
        y = bottom - plot_height * tick / tick_count
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        parts.append(f'<text class="tick" x="{left-10}" y="{y+5:.2f}" text-anchor="end">{value/1e6:.1f}M</text>')
    parts.append(f'<text class="axis-label" x="{left}" y="{top-14}">Instructions (millions)</text>')

    for index, entry in enumerate(sorted_entries):
        x = left + x_step * (index + 0.5)
        value = entry["medianInstructions"]
        y = bottom - (value / axis_max) * plot_height
        name = html.escape(entry["name"])
        label = f'{value/1e6:.1f}M'
        parts.append(f'<line class="stem" x1="{x:.2f}" y1="{bottom}" x2="{x:.2f}" y2="{y:.2f}"/>')
        parts.append(f'<circle class="marker" cx="{x:.2f}" cy="{y:.2f}" r="5"><title>{name}: {label}</title></circle>')
        parts.append(f'<text class="value" x="{x:.2f}" y="{max(top+14, y-10):.2f}" text-anchor="middle">{label}</text>')
        parts.append(f'<text class="editor" transform="translate({x:.2f} {bottom+14}) rotate(48)" text-anchor="start">{name}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def build_report(results: list[dict], output: Path, run_url: str, commit: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir(exist_ok=True)
    entries = []
    rows = []
    for item in sorted(results, key=lambda item: statistics.median(t["instructions"] for t in item["data"]["trials"])):
        editor, data = item["editor"], item["data"]
        trials = data["trials"]
        median = statistics.median(trial["instructions"] for trial in trials)
        filename = f"{editor['id']}.json"
        (raw_dir / filename).write_text(json.dumps(data, indent=2) + "\n")
        entry = {
            "id": editor["id"], "name": editor["name"], "version": editor["version"],
            "medianInstructions": median,
            "trials": [{"repeat": trial.get("repeat"), "instructions": trial["instructions"],
                        "measurementSeconds": trial["measurementSeconds"],
                        "inputDispatchSeconds": trial["inputDispatchSeconds"],
                        "inputToCutoffSeconds": trial["inputToCutoffSeconds"],
                        "selection": trial["selection"],
                        "selectionCoordinates": trial["selectionCoordinates"],
                        "focusClickCoordinates": trial["focusClickCoordinates"],
                        "doubleClickDelayMilliseconds": trial["doubleClickDelayMilliseconds"],
                        "clipboardBefore": trial["clipboardBefore"],
                        "clipboardAfter": trial["clipboardAfter"],
                        "documentContentSha256": trial["documentContentSha256"],
                        "fixtureContentSha256": trial["fixtureContentSha256"],
                        "fixtureUnchanged": trial["fixtureUnchanged"],
                        "source": trial["source"]} for trial in trials],
            "protocol": data["protocol"], "host": data["host"], "raw": f"raw/{filename}",
        }
        entries.append(entry)
        values = "<br>".join(f"{trial['instructions']/1e6:.1f}M" for trial in trials)
        sources = ", ".join(sorted({html.escape(trial["source"]) for trial in trials}))
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(editor['name'])}</th>"
            f"<td>{html.escape(editor['version'])}</td>"
            f"<td>{median/1e6:.1f}M</td><td>{values}</td><td>{sources}</td>"
            f"<td><a href=\"raw/{html.escape(filename)}\" download>Download JSON</a></td></tr>"
        )
    report = {"complete": True, "runUrl": run_url, "commit": commit, "editors": entries}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    chart = build_median_chart(entries)
    safe_run_url = html.escape(run_url, quote=True)
    safe_commit = html.escape(commit)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Selection instructions benchmark</title><style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f4f7fa}}body{{max-width:1100px;margin:3rem auto;padding:0 1rem}}
h1{{line-height:1.15}}.note{{padding:1rem;background:#e8f1fa;border-radius:.5rem}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:white;margin:1.5rem 0}}th,td{{padding:.7rem;border-bottom:1px solid #d7e0e8;text-align:left;vertical-align:top}}
th{{background:#e8f1fa}}code{{overflow-wrap:anywhere}}a{{color:#0759a5}}
.chart-scroll{{overflow-x:auto;background:white;border:1px solid #d7e0e8;border-radius:.5rem;margin:1.5rem 0}}
.chart{{display:block;width:100%;min-width:760px;height:auto}}.grid{{stroke:#d7e0e8;stroke-width:1}}
.tick,.axis-label,.editor,.value{{font:12px system-ui,sans-serif;fill:#17212b}}.axis-label{{font-size:13px;font-weight:600}}
.stem{{stroke:#4780ad;stroke-width:2}}.marker{{fill:#0759a5;stroke:white;stroke-width:2}}.value{{font-weight:600}}
</style></head><body><main><h1>Selection instructions benchmark</h1>
<p>Latest complete run: <a href="{safe_run_url}">GitHub Actions run</a> · commit <code>{safe_commit}</code></p>
<p class="note">Retired hardware instructions are measured for a native Wayland double-click sent by ydotool that selects <code>selectiontarget</code> in a prepared plain-text document. Counters are enabled immediately before double-click dispatch and disabled after a fixed one-second response window. The editor and its descendants are counted; launch, fixture setup, focus, pointer positioning, the input driver, selection verification and cleanup are excluded. Background work during the response window is included. Each trial uses a fresh profile, installs and verifies a stale-clipboard sentinel before counting, then requires the copied selection to match the target and the document to remain unchanged. Each value below is one trial; the summary is the median of three. Raw counters, exact boundaries, host and protocol metadata are available in each downloadable JSON file.</p>
<p>The Linux ARM64 matrix covers LVCE, VS Code, Zed, Lapce, Basic Electron, CodeMirror 6, CodeMirror 5 and Ace in Electron, the supported ARM artifact subset of the reference desktop editor matrix. CodeMirror 5 uses plain text mode without line numbers or extensions; Ace uses plain text mode, the TextMate theme, no gutter and disabled workers.</p>
<p>All {len(entries)} editors completed with valid measurements.</p>
<section aria-labelledby="chart-heading"><h2 id="chart-heading">Median instructions per word selection by editor</h2>
<p>Editors are ordered by median instruction count, lowest first. Each marker uses the same vertical scale; lower values appear closer to zero at the bottom. Scroll the chart horizontally on narrow screens.</p>
<div class="chart-scroll">{chart}</div></section>
<div class="scroll"><table><thead><tr><th>Editor</th><th>Version</th><th>Median instructions per selection</th><th>Trials</th><th>Measurement source</th><th>Raw data</th></tr></thead><tbody>
{''.join(rows)}</tbody></table></div><p><a href="report.json">Download complete report JSON</a></p></main></body></html>
"""
    (output / "index.html").write_text(page)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/editors")
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp/pages")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    results = load_results(args.input)
    build_report(results, args.output, args.run_url, args.commit)
    print(f"Built a complete report for {len(results)} editors at {args.output}")


if __name__ == "__main__":
    main()
