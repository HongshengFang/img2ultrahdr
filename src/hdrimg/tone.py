from __future__ import annotations

from dataclasses import dataclass

import numpy as np

REC2020_LUMA = np.array([0.2627, 0.6780, 0.0593], dtype=np.float32)
SDR_WHITE_NITS = 203.0


@dataclass(frozen=True)
class ExposureStats:
    development_ev: float
    auto_ev: float
    total_ev: float
    scene_adjustment_ev: float
    log_average: float
    percentile_1: float
    percentile_99: float
    finite_fraction: float
    negative_fraction: float
    above_one_fraction: float
    channel_above_one_fraction: float
    neutral_channel_above_one_fraction: float
    sample_max_luminance: float
    sample_max_channel: float


def luminance_rec2020(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(rgb, dtype=np.float32) @ REC2020_LUMA


def exposure_statistics(
    rgb: np.ndarray,
    *,
    auto_exposure: bool,
    exposure_ev: float,
    development_ev: float = 0.0,
    max_preview_edge: int = 2048,
) -> ExposureStats:
    height, width = rgb.shape[:2]
    step = max(1, int(np.ceil(max(height, width) / max_preview_edge)))
    sample = np.asarray(rgb[::step, ::step, :3], dtype=np.float32)
    y = luminance_rec2020(sample).reshape(-1)
    finite = np.isfinite(y)
    finite_y = y[finite]
    finite_fraction = float(finite.mean()) if finite.size else 0.0
    negative_fraction = float(np.mean(finite_y < 0.0)) if finite_y.size else 0.0
    above_one_fraction = float(np.mean(finite_y > 1.0)) if finite_y.size else 0.0
    finite_channels = sample[np.isfinite(sample)]
    channel_above_one_fraction = (
        float(np.mean(finite_channels > 1.0)) if finite_channels.size else 0.0
    )
    neutral_scale = np.float32(2.0 ** -development_ev)
    neutral_channel_above_one_fraction = (
        float(np.mean(finite_channels * neutral_scale > 1.0))
        if finite_channels.size
        else 0.0
    )
    sample_max_luminance = float(np.max(finite_y)) if finite_y.size else 0.0
    sample_max_channel = float(np.max(finite_channels)) if finite_channels.size else 0.0
    positive = finite_y[finite_y > 0.0]
    if positive.size < 16:
        p1 = p99 = log_average = 0.0
        auto_ev = 0.0
    else:
        p1, p99 = (float(v) for v in np.percentile(positive, [1.0, 99.0]))
        trimmed = positive[(positive >= p1) & (positive <= p99)]
        log_average = float(np.exp(np.mean(np.log(np.maximum(trimmed, 1e-6)))))
        measured_ev = np.log2(0.18 / max(log_average, 1e-6))
        auto_ev = float(np.clip(measured_ev + development_ev, -2.0, 2.0))
    if not auto_exposure:
        auto_ev = 0.0
    total_ev = auto_ev + float(exposure_ev)
    return ExposureStats(
        development_ev=float(development_ev),
        auto_ev=auto_ev,
        total_ev=total_ev,
        scene_adjustment_ev=total_ev - float(development_ev),
        log_average=log_average,
        percentile_1=p1,
        percentile_99=p99,
        finite_fraction=finite_fraction,
        negative_fraction=negative_fraction,
        above_one_fraction=above_one_fraction,
        channel_above_one_fraction=channel_above_one_fraction,
        neutral_channel_above_one_fraction=neutral_channel_above_one_fraction,
        sample_max_luminance=sample_max_luminance,
        sample_max_channel=sample_max_channel,
    )


def apply_exposure_and_highlights(
    rgb: np.ndarray, *, total_ev: float, highlight_ev: float, shadow_ev: float = 0.0,
    white_ev: float = 0.0, black_ev: float = 0.0,
) -> np.ndarray:
    exposed = np.nan_to_num(
        np.asarray(rgb, dtype=np.float32) * np.float32(2.0**total_ev),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    np.maximum(exposed, 0.0, out=exposed)
    if shadow_ev:
        y = luminance_rec2020(exposed)
        weight = np.maximum(1.0 - y / np.float32(0.18), 0.0) ** 2
        exposed *= np.exp2(np.float32(shadow_ev) * weight)[..., None]
    if highlight_ev:
        y = luminance_rec2020(exposed)
        t = np.clip((y - 0.18) / (1.0 - 0.18), 0.0, 1.0)
        weight = t * t * (3.0 - 2.0 * t)
        exposed *= np.exp2(np.float32(highlight_ev) * weight)[..., None]
    # Endpoint controls act on scene-linear luminance, without clipping HDR at
    # SDR white. Their curves are monotone throughout the supported ±2 EV.
    if black_ev:
        y = luminance_rec2020(exposed)
        weight = np.maximum(1.0 - y / np.float32(0.045), 0.0) ** 2
        exposed *= np.exp2(np.float32(black_ev) * weight)[..., None]
    if white_ev:
        y = luminance_rec2020(exposed)
        weight = (y / (y + np.float32(0.9))) ** 2
        exposed *= np.exp2(np.float32(white_ev) * weight)[..., None]
    return exposed


def sdr_curve(y: np.ndarray) -> np.ndarray:
    return -np.expm1(-np.maximum(np.asarray(y, dtype=np.float32), 0.0))


def hdr_curve(
    y: np.ndarray, *, peak_nits: float, hdr_strength: float
) -> np.ndarray:
    y = np.maximum(np.asarray(y, dtype=np.float32), 0.0)
    peak = np.float32(peak_nits / SDR_WHITE_NITS)
    full = peak * -np.expm1(-y / peak)
    base = sdr_curve(y)
    return base + np.float32(hdr_strength) * (full - base)


def max_content_boost(peak_nits: float, hdr_strength: float) -> float:
    return 1.0 + float(hdr_strength) * (float(peak_nits) / SDR_WHITE_NITS - 1.0)


def scale_rgb_to_luminance(rgb: np.ndarray, mapped_y: np.ndarray) -> np.ndarray:
    y = luminance_rec2020(rgb)
    ratio = np.divide(
        mapped_y,
        y,
        out=np.zeros_like(mapped_y, dtype=np.float32),
        where=y > 1e-8,
    )
    return np.asarray(rgb, dtype=np.float32) * ratio[..., None]


def contrast_curve(
    y: np.ndarray,
    *,
    black_luminance: float,
    contrast: float,
    pivot: float = 0.18,
) -> np.ndarray:
    """Apply a global scene-linear toe/contrast curve anchored at middle gray."""
    values = np.maximum(np.asarray(y, dtype=np.float32), 0.0)
    black = np.float32(min(max(black_luminance, 0.0), pivot - 1e-3))
    normalized = np.maximum((values - black) / np.float32(pivot - black), 0.0)
    return np.float32(pivot) * np.power(normalized, np.float32(contrast))


def apply_scene_contrast(
    rgb: np.ndarray,
    *,
    black_luminance: float,
    contrast: float,
    pivot: float = 0.18,
) -> np.ndarray:
    y = luminance_rec2020(rgb)
    mapped = contrast_curve(
        y,
        black_luminance=black_luminance,
        contrast=contrast,
        pivot=pivot,
    )
    return scale_rgb_to_luminance(rgb, mapped)


def hdr_curve_with_highlight_expansion(
    y: np.ndarray,
    *,
    peak_nits: float,
    hdr_strength: float,
    highlight_start: float,
    highlight_anchor: float,
    highlight_lift: float,
) -> np.ndarray:
    """Map all scene tones into HDR headroom, with optional extra highlight lift."""
    values = np.maximum(np.asarray(y, dtype=np.float32), 0.0)
    peak = np.float32(peak_nits / SDR_WHITE_NITS)
    full = peak * -np.expm1(-values / peak)
    span = max(highlight_anchor - highlight_start, 1e-4)
    weight = np.clip((values - highlight_start) / span, 0.0, 1.0)
    weight = weight * weight * (3.0 - 2.0 * weight)
    expanded = np.minimum(
        full * np.power(np.float32(max(highlight_lift, 1.0)), weight), peak
    )
    base = sdr_curve(values)
    return base + np.float32(hdr_strength) * (expanded - base)
