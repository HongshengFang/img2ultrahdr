from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb
from .tone import apply_exposure_and_highlights, luminance_rec2020

DEFAULT_EXPOSURE_EV = 0.0
DEFAULT_CONTRAST = 1.35
DEFAULT_SATURATION = 1.10


@dataclass(frozen=True)
class LookDecision:
    enabled: bool
    exposure_ev: float
    contrast: float
    saturation: float
    exposure_source: str
    contrast_source: str
    saturation_source: str
    metrics: dict[str, float]
    suggested: dict[str, float]


def _trimmed_log_average(values: np.ndarray) -> float:
    positive = np.asarray(values, dtype=np.float32)
    positive = positive[np.isfinite(positive) & (positive > 0.0)]
    if positive.size < 16:
        return 0.18
    low, high = np.percentile(positive, [1.0, 99.0])
    trimmed = positive[(positive >= low) & (positive <= high)]
    if trimmed.size < 16:
        trimmed = positive
    return float(np.exp(np.mean(np.log(np.maximum(trimmed, 1e-6)))))


def resolve_look(
    rgb: np.ndarray,
    *,
    base_scene_adjustment_ev: float,
    enabled: bool,
    auto_exposure: bool,
    exposure_ev: float | None,
    contrast: float | None,
    saturation: float | None,
    max_preview_edge: int = 1024,
) -> LookDecision:
    """Measure a scene preview and resolve automatic or user-selected look controls."""
    height, width = rgb.shape[:2]
    step = max(1, int(np.ceil(max(height, width) / max_preview_edge)))
    preview = apply_exposure_and_highlights(
        rgb[::step, ::step, :3],
        total_ev=base_scene_adjustment_ev,
        highlight_ev=0.0,
    )
    y = luminance_rec2020(preview)
    valid = np.isfinite(y) & (y > 0.0)
    values = y[valid]

    if values.size < 16:
        p5, p10, p50, p90, p95, p99 = (0.02, 0.04, 0.18, 0.50, 0.65, 1.0)
        center_log_average = 0.18
        chroma_p75 = 0.05
        shadow_fraction = 0.0
        highlight_fraction = 0.0
    else:
        p5, p10, p50, p90, p95, p99 = (
            float(value)
            for value in np.percentile(values, [5.0, 10.0, 50.0, 90.0, 95.0, 99.0])
        )
        y_height, y_width = y.shape
        y0, y1 = y_height // 4, y_height - y_height // 4
        x0, x1 = y_width // 4, y_width - y_width // 4
        center_log_average = _trimmed_log_average(y[y0:y1, x0:x1])
        shadow_fraction = float(np.mean(values < 0.02))
        highlight_fraction = float(np.mean(values > 1.0))

        linear_srgb = rec2020_to_linear_srgb(preview.reshape(-1, 3))
        lab = linear_srgb_to_oklab(linear_srgb)
        chroma = np.sqrt(np.sum(np.square(lab[:, 1:]), axis=1))
        midtone = (
            np.all(np.isfinite(lab), axis=1)
            & valid.reshape(-1)
            & (y.reshape(-1) >= p5)
            & (y.reshape(-1) <= p95)
        )
        chroma_p75 = (
            float(np.percentile(chroma[midtone], 75.0))
            if np.count_nonzero(midtone) >= 16
            else 0.05
        )

    dynamic_range_stops = float(
        np.clip(np.log2(max(p90, 1e-6) / max(p10, 1e-6)), 0.0, 12.0)
    )
    suggested_exposure = float(
        np.clip(
            0.15 + 0.45 * np.log2(0.18 / max(center_log_average, 1e-6)),
            -0.35,
            0.35,
        )
    )
    suggested_contrast = float(
        np.clip(1.75 - 0.075 * dynamic_range_stops, 1.30, 1.65)
    )
    suggested_saturation = float(np.clip(1.16 - 1.2 * chroma_p75, 1.05, 1.18))

    if exposure_ev is not None:
        resolved_exposure = float(exposure_ev)
        exposure_source = "manual"
    elif enabled and auto_exposure:
        resolved_exposure = suggested_exposure
        exposure_source = "auto"
    else:
        resolved_exposure = DEFAULT_EXPOSURE_EV
        exposure_source = "default"

    if contrast is not None:
        resolved_contrast = float(contrast)
        contrast_source = "manual"
    elif enabled:
        resolved_contrast = suggested_contrast
        contrast_source = "auto"
    else:
        resolved_contrast = DEFAULT_CONTRAST
        contrast_source = "default"

    if saturation is not None:
        resolved_saturation = float(saturation)
        saturation_source = "manual"
    elif enabled:
        resolved_saturation = suggested_saturation
        saturation_source = "auto"
    else:
        resolved_saturation = DEFAULT_SATURATION
        saturation_source = "default"

    return LookDecision(
        enabled=bool(enabled),
        exposure_ev=resolved_exposure,
        contrast=resolved_contrast,
        saturation=resolved_saturation,
        exposure_source=exposure_source,
        contrast_source=contrast_source,
        saturation_source=saturation_source,
        metrics={
            "center_log_average": float(center_log_average),
            "percentile_5": float(p5),
            "percentile_10": float(p10),
            "percentile_50": float(p50),
            "percentile_90": float(p90),
            "percentile_95": float(p95),
            "percentile_99": float(p99),
            "dynamic_range_stops": dynamic_range_stops,
            "oklab_chroma_p75": float(chroma_p75),
            "shadow_fraction": shadow_fraction,
            "highlight_fraction": highlight_fraction,
        },
        suggested={
            "exposure_ev": suggested_exposure,
            "contrast": suggested_contrast,
            "saturation": suggested_saturation,
        },
    )
