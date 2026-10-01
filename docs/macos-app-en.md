# Img2UltraHDR 0.2 — quick guide

Img2UltraHDR is a local Mac editor for one Canon CR2 or Fujifilm RAF at a time. It uses the accepted Clear V8 R5 / Natural V8 recipes and exports a 1000-nit Ultra HDR JPEG. Apple Silicon and macOS 15 or newer are required; this build depends on this Mac's existing Python environment and image tools.

## Editing

1. Open `~/Applications/Img2UltraHDR.app`, then drop a RAW or choose **Open**. Allow Documents access if macOS asks: the local engine and sample files live there.
2. Wait for the first precise preview. Drag exposure, highlights, shadows, saturation, HDR strength or SDR brightness for immediate GPU feedback. Release to run precise refinement. Numeric input settles after 250 ms.
3. **Live preview** reuses the most recent precise spatial/color analysis. **Refining** means the full preview engine is working. **Precise preview** is the completed result. You can keep editing during refinement; obsolete results are discarded.
4. White-balance modes require RAW preparation. Custom starts at 5600 K / neutral tint, then remembers its values. Once ready, temperature and tint also preview interactively. Precise redevelopment can visibly correct this approximation. Auto does not show an invented Kelvin value.
5. **Initial look** shows the current style's defaults. **100%** renders the full image at physical pixel scale. Pinch to zoom; drag or scroll to pan. Starting another adjustment returns to the ordinary preview. A drag is one undo step, with 100 steps retained.
6. **Export** processes the current parameters at full resolution, encodes Ultra HDR and validates it before publishing. An extra SDR JPEG and removal of camera metadata are optional. ICC and HDR information remain intact. Controls lock during export; cancellation remains available.

The gear button or `⌘,` opens **Settings**. Choose 中文 / English and Light / Dark / System. Preferences update immediately and persist without changing photo parameters. System file-dialog controls follow the macOS language. The photo surround stays neutral dark gray in every appearance.

## Histogram and pixel readings

The histogram covers the whole displayed image, including areas outside a zoomed viewport. Its sampling label distinguishes preview, full-size and decoded export data. HDR/SDR and Initial look switch the statistics with the image. During dragging it refreshes up to ten times per second.

- **SDR:** Display P3 encoded values from 0 to 255.
- **HDR:** a broad black-to-SDR-white section, followed by an EV scale through +2.30 EV / 1000 nit. The orange marker is the current display limit, separate from the file's output ceiling.
- **RGB probe:** hover over the photograph. SDR uses Display P3 0–255; HDR uses linear Rec.2020 percentages, which can exceed 100%. Reference nits are image luminance multiplied by 203, not measured screen brightness. Moving outside the image clears the pixel reading.
- **Boundary overlays:** click the triangles or press `J`. Blue marks near black; red marks any channel near the output ceiling. HDR values above SDR white are normal and are not marked red just for exceeding it. These are output warnings, not RAW sensor-clipping measurements.

Exact thresholds: SDR ≤0.5 in all encoded channels or ≥254.5 in any channel; HDR luminance ≤0.00001 or any channel ≥99.9% of `1000/203`. Overlays never affect histogram counts, sampling or export.

An SDR monitor uses the paired SDR rendition. Display brightness/headroom never changes export parameters. If GPU acceleration fails, the app keeps precise previews available and identifies unavailable live/statistics tools.

## Storage and recovery

Edits and drafts: `~/Library/Application Support/Img2UltraHDR`. Cache: `~/Library/Caches/Img2UltraHDR` (10 GB target, plus temporary working space). Logs: `~/Library/Logs/Img2UltraHDR/engine.log`.

Reopening the same original restores its edits. A moved original can be located again and verified by content fingerprint. Clearing cache while the app is closed does not delete edits or originals. An engine update invalidates old cached results and reports that regeneration is needed.

Keep this project and `.venv` in place: the app records absolute dependency paths. After moving them or replacing dependencies, rebuild with `scripts/build_macos_app.sh`. The build includes bilingual resources, Metal source, a Vision helper and local signatures; full Xcode and a paid developer account are unnecessary.

`dist/Img2UltraHDR 0.1 Recovery.app` points to the frozen 0.1 engine in `outputs/app-v02-validation/baseline/`. Keep that snapshot and the shared Python environment for rollback. The original baseline app and source snapshot are also retained unchanged.

See the [0.2 validation record](macos-app-v02-validation.md) for measurements and remaining physical-screen checks. Screenshots verify layout, not actual HDR brightness.
