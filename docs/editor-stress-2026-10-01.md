# Editor stress validation — 2026-10-01

Validated on this Mac with production Python, AppKit, SwiftUI, Metal, RawTherapee,
Vision and Ultra HDR tools. The resulting installed app is **0.4.0 (build 4)**.
Tests used isolated support directories, cache entries and export destinations.
Both original RAW fingerprints stayed unchanged. The user's current window session
was preserved, including its Natural style and current slider values.

## Problems reproduced and repaired

- Extreme text-field input reached the interactive model before its delayed
  commit clamped the value. Global numeric inputs now normalize before interactive
  work starts, including nonfinite values and restored out-of-range settings.
- Export protection depended on disabled controls. Undo, redo, reset, comparison,
  full-size inspection, retry and local deletion now protect the active export
  at their model entry points as well. History changes clear an unfinished drag.
- Malformed edit/draft records, a damaged dependency-check stamp and incomplete
  preparation caches could prevent recovery. Invalid editing records are kept as
  `.corrupt-*` backups; recovery prefers an intact edit/draft and reports the
  recovery to the UI. Disposable dependency records are checked again. Prepared
  TIFF, analysis, person-mask and display-buffer caches are checked before reuse
  and regenerated when missing or truncated. Invalid window recipes request the
  valid backend edit instead of replacing it with default controls.
- Concurrent JSON publishers shared a single `.partial` filename. Eight writers
  reproduced a `FileNotFoundError` while publishing 240 records. Each publisher
  now owns a unique staging file, and replacement remains atomic.

The new fault-injection tests failed against the earlier implementation and pass
after repair. The first numeric/model run recorded 51 failed checks; the final
run recorded **7,101 actions, 11,623 successful checks and zero failures**,
including all 81 displayed values after each numeric input lost focus.

## Coverage and evidence

| Area | Actual exercise | Result |
| --- | --- | --- |
| Regression | Entire Python suite | 290 passed |
| Recovery faults | Invalid JSON/types/timestamps/recipes, broken TIFF/NPY, broken startup checks, concurrent records | 21 passed |
| Native controls/model | 81 field inputs: huge positive/negative values, exponent overflow, NaN, Chinese text, empty input, decimals; seeded drags, history, style/WB changes, selection/reset/cancel races, stale events and bounded history | 7,101 actions; 11,623 checks passed |
| Existing native model checks | Startup watchdog, saved sessions, local editing, export lock, stale events, undo/redo and bounded caches | 31 passed |
| Real worker | Two RAWs, three queued bursts of source/edit/cancel requests, invalid settings/requests, Unicode and uppercase relocated paths, missing/invalid RAW, actual local selection, eight edge/overlapping regions, missing selection recovery, damaged draft/packet, crash and restart | 17 scenarios; 83 requests passed |
| Actual cancellation | RAW development, full rendering, Ultra HDR encoding, float preview | 0.021–0.040 s; no remaining task group or partial cache |
| Real exports | Full-size HDR + SDR, collisions, overwrite, privacy removal, invalid extension; decode/probe validation | Passed; originals unchanged |
| Actual RAW white balance | Camera, custom 2000K/tint −100, custom 15000K/tint +100, Natural/Auto | Four redevelopments passed; finite bounded SDR/HDR |
| Numerical extremes | Seed 20261001, 64 full-range recipes across two retained real RAW scenes and both styles, including eight regions at image edges, tiny/large radii and extreme rotation | 128 precise frames + 128 Metal frames finite, nonnegative and bounded |
| Packaged app | Cold startup → RAW import → edit → undo → redo → cancel full-size work → recover preview → full-size export | All seven steps completed; no application error |

An additional positive smart-region test rendered the actual Vision-generated
1025×1536 selection asset. With the asset present in the strength-zero anchor,
both SDR and HDR Metal strength-one results passed the strict local/CPU comparison.
The initial comparison fixture lacked the mask in its anchor and was corrected;
the renderer appropriately does not apply a smart region until its asset arrives.

The scene-math test reduces real scenes to 256 pixels for repeatable coverage. It
isolates tone/local math; it does not substitute for the separate actual RAW
redevelopment and full-resolution export tests. Its extreme jumps had a worst
median/p95 luminance difference of 0.0613/0.2031 EV between interactive and precise
frames. Interactive preview remains an approximation until the precise result
arrives. This is an accuracy measurement, separate from the finite/bounded checks.

## Sustained operation

Two five-minute native benchmarks exercised the shipped preview at 30 parameter
updates per second, with histogram/probe updates and SDR/HDR changes. The local
run kept eight regions active and changed their strength, position and rotation.
It presented 5,131 frames with the window visible throughout, without GPU errors.
Under concurrent compilation/RAW processing it averaged 17.1 presented fps, with
a 73.9 ms P95 presentation latency. This establishes stability under load, not a
guarantee of 30 fps under every workload.

After warmup, local GPU allocation remained around 113–117 MiB (113.2 MiB at
270 seconds); the process's high-water RSS stabilized at 180.8 MiB. Global GPU
allocation stabilized at 105.2 MiB. The first global window became occluded after
14.6 seconds, so its five-minute average presentation fps is not a meaningful
visible-performance measurement. It still exercised background scheduling and
memory retention. Final installed-app measurements are saved separately below.

Repeated packet/window tests cover 40 cycles of changing photos/styles, local
regions, SDR/HDR, resize, hide/show and minimize/restore. Native command-line
windows were not considered visible by macOS in this session, including the
Launch Services wrapper. All 40 latest-recipe frame checks passed, without GPU
errors. The result explicitly records `visibility_limited: true`: this validates
frame/lifecycle work, rather than on-screen presentation of every restored window.

The final installed global benchmark rendered 2,017 frames without a GPU error
and produced the expected histogram pixel count, but its window was occluded
throughout, so no presentation fps or latency is inferred from that run.

The installed local run rendered 2,749 frames, with no GPU error and the expected
histogram count. It was also occluded throughout. At 90 seconds the installed
global/local GPU allocations were 105.2/115.5 MiB and process high-water RSS was
185.8/189.4 MiB. These background measurements are not on-screen latency results.

The installed app was then reopened normally. Its exact 1024×1536 preview restored
the user's original photo and all saved Natural adjustments. Bundled engine source
matched the workspace, the RAW SHA-256 matched the saved fingerprint and strict
code-signature verification passed after rendering.

Both temporary validation app bundles were unregistered and removed. The installed
app and the test evidence remain. No stress worker or RAW-development process was
left running; only the user's normal app and its idle controller remain.

## Repeating the checks

```sh
.venv/bin/python -m pytest -o addopts= -q

swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift \
  scripts/stress_macos_editor.swift -o /tmp/editor-stress
HDRIMG_RESOURCES="$PWD/macos/Sources/Img2UltraHDR/Resources" \
  /tmp/editor-stress /tmp/editor-stress-output

.venv/bin/python scripts/stress_editor_worker.py /tmp/worker-stress-output \
  --prepared-cache /absolute/path/to/an/isolated/cache

.venv/bin/python scripts/stress_render_limits.py /tmp/render-stress-output \
  --prepared-cache /absolute/path/to/an/isolated/cache
```

The worker script requires at least two valid prepared RAW records in `scenes/`
and installed app helpers. It links immutable retained inputs into the test cache;
it never rewrites an original RAW. Output directories must be new. The generated
`cases.json` can be passed to `scripts/check_local_preview.swift` for the production
Metal local/global pipeline. `scripts/stress_preview_window.swift` exercises the
actual editor view; launch its executable through a temporary macOS app bundle
when testing on-screen presentation.

Full artifacts are ignored under `outputs/editor-stress-2026-10-01/`, including
before/after failures, `tests.xml`, native `stress.json`, worker events/logs,
decoded/encoded images, GPU frames, presentation timestamps and memory samples.
There is no claim of exhaustive testing of every user action or hardware/storage
failure. Actual disk exhaustion and physically removing a mounted drive were not
induced; existing error/rollback tests cover those code paths through fault injection.
