from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image


def _smoothstep(value: np.ndarray) -> np.ndarray:
    t = np.clip(value, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


@dataclass(frozen=True)
class PhoneSceneDecision:
    """Continuous scene cues measured before creative exposure compensation."""

    raw_p10: float
    raw_p50: float
    raw_p90: float
    raw_p99: float
    center_ratio: float
    dark_fraction: float
    dark_weight: float
    indoor_weight: float
    high_key_weight: float
    sdr_auto_ev: float
    hdr_midtone_gain: float
    hdr_peak_ratio: float
    hdr_anchor_percentile: float
    hdr_dark_highlight_power: float

    def as_record(self) -> dict[str, float]:
        return asdict(self)


def phone_scene_decision(
    rgb: np.ndarray, *, development_ev: float, peak_nits: float,
    refine_diffuse: bool = False,
) -> PhoneSceneDecision:
    """Choose a restrained SDR base and an independent HDR brightness budget.

    The cues use the linear RAW preview, prior to user exposure compensation. They
    vary continuously so a small change in framing cannot switch to another look.
    """
    from .tone import luminance_rec2020

    raw = np.maximum(np.asarray(rgb, dtype=np.float32) * np.float32(2.0**-development_ev), 0.0)
    y = luminance_rec2020(raw)
    valid = np.isfinite(y)
    if np.count_nonzero(valid) < 16:
        y = np.full((16, 16), 0.18, dtype=np.float32)
        raw = np.repeat(y[..., None], 3, axis=-1)
    else:
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    p10, p50, p90, p99 = (float(v) for v in np.percentile(y, [10, 50, 90, 99]))
    height, width = y.shape
    center = float(np.median(y[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4]))
    center_ratio = center / max(p50, 0.01)
    spread = np.max(raw, axis=-1) - np.min(raw, axis=-1)
    neutral_fraction = float(np.mean((y > 0.5) & (spread < 0.20)))
    dark_fraction = float(np.mean(y < 0.01))

    dark = float(1.0 - _smoothstep((p50 - 0.08) / 0.15))
    indoor = float(
        _smoothstep((p50 - 0.12) / 0.15)
        * (1.0 - _smoothstep((p90 - 0.75) / 0.15))
        * (1.0 - _smoothstep((center_ratio - 1.2) / 0.4))
        * (1.0 - _smoothstep((neutral_fraction - 0.02) / 0.05))
    )
    high_key = float(
        _smoothstep((center_ratio - 1.55) / 0.30)
        * _smoothstep((neutral_fraction - 0.06) / 0.06)
        * _smoothstep((p90 - 1.0) / 0.25)
    )
    bright_center = float(_smoothstep((center_ratio - 2.5) / 1.5))
    extreme_contrast = float(_smoothstep((p99 / max(p90, 0.01) - 4.0) / 2.0))
    flat = float(1.0 - _smoothstep((p90 / max(p10, 0.01) - 2.5) / 2.0))
    sdr_ev = float(np.clip(
        -0.20 - 1.20 * dark + 0.40 * dark * bright_center
        - 0.90 * dark * min(dark_fraction / 0.30, 1.0)
        - 1.60 * indoor + 1.20 * high_key,
        -2.0, 1.0,
    ))
    mid_gain = float(np.clip(
        1.90 - 0.60 * dark - 0.30 * indoor + 0.40 * high_key
        - 0.30 * dark * extreme_contrast
        + 0.10 * dark * min(dark_fraction / 0.30, 1.0),
        1.0, 2.5,
    ))
    peak = float(np.clip(
        2.50 + 2.40 * dark - 0.90 * indoor + 1.00 * high_key - 0.90 * flat,
        1.15, peak_nits / 203.0,
    ))
    if refine_diffuse:
        # A flat, well-exposed outdoor scene should not receive the same low
        # HDR budget as an evenly shaded wall. Use the RAW brightness cue in
        # addition to flatness; neither picture-specific names nor masks enter.
        bright_diffuse = float(_smoothstep((p50 - 0.70) / 0.30))
        peak = min(peak + 0.60 * flat * bright_diffuse, peak_nits / 203.0)
    anchor_percentile = 99.5 + 0.4 * dark * max(
        extreme_contrast, min(dark_fraction / 0.30, 1.0)
    )
    dark_highlight_power = 1.0 + extreme_contrast + 0.7 * min(dark_fraction / 0.30, 1.0)
    return PhoneSceneDecision(
        p10, p50, p90, p99, center_ratio, dark_fraction,
        dark, indoor, high_key, sdr_ev, mid_gain, peak,
        anchor_percentile, dark_highlight_power,
    )


def phone_hdr_luminance(
    sdr_y: np.ndarray,
    *,
    source_y: np.ndarray,
    source_highlight_start: float,
    source_highlight_anchor: float,
    dark_weight: float,
    dark_highlight_power: float,
    manual_highlight_bonus: float,
    manual_highlight_start: float,
    manual_highlight_anchor: float,
    midtone_gain: float,
    peak_target: float,
    peak_limit: float,
    highlight_anchor: float,
    shoulder_strength: float,
    hdr_strength: float,
    smooth_highlights: bool = False, dark_intermediate_lift: float = 0.0,
) -> np.ndarray:
    """Lift ordinary tones broadly while preserving a smooth highlight shoulder."""
    y = np.maximum(np.asarray(sdr_y, dtype=np.float32), 0.0)
    rise = _smoothstep((y - 0.015) / 0.085)
    base = y * (1.0 + np.float32(midtone_gain - 1.0) * rise)
    start = 0.42
    anchor = max(highlight_anchor, start + 0.08)
    at_anchor = anchor * midtone_gain
    extra = max(peak_target - at_anchor, 0.0)
    t = np.maximum((y - start) / (anchor - start), 0.0)
    # Smooth gain (rather than an additive percentile-anchored luminance lift)
    # preserves diffuse white surfaces and never gives a sub-white pixel more
    # than the scene's white gain. No fixed onset introduces a sky contour.
    if smooth_highlights:
        highlight_gain = max(peak_target - midtone_gain, 0.0) * _smoothstep(y / anchor)
        expanded = base + y * highlight_gain
    else:
        expanded = base + np.float32(extra) * (1.0 - np.exp(-3.0 * t))
    if dark_weight > 0.0:
        source_t = np.clip(
            (np.maximum(source_y, 0.0) - source_highlight_start)
            / max(source_highlight_anchor - source_highlight_start, 1e-4),
            0.0, 1.0,
        )
        if dark_intermediate_lift > 0:
            intermediate = min(dark_intermediate_lift, extra)
            middle_rise = _smoothstep((np.maximum(source_y, 0)/max(source_highlight_start, 1e-5)-.5)/.5)
            dark_expanded = (base + intermediate*middle_rise
                + np.float32(extra-intermediate)*np.power(source_t, dark_highlight_power+.55))
        else:
            dark_expanded = base + np.float32(extra) * np.power(source_t, dark_highlight_power)
        expanded = expanded * np.float32(1.0 - dark_weight) + dark_expanded * np.float32(dark_weight)
    if manual_highlight_bonus > 0.0:
        bonus_t = _smoothstep(
            (np.maximum(source_y, 0.0) - manual_highlight_start)
            / max(manual_highlight_anchor - manual_highlight_start, 1e-4)
        )
        expanded += np.float32(manual_highlight_bonus) * bonus_t
    if shoulder_strength > 0.0:
        above = np.maximum(expanded - np.float32(peak_target), 0.0)
        headroom = max(peak_limit - peak_target, 0.05)
        compressed = peak_target + above / (1.0 + shoulder_strength * above / headroom)
        expanded = np.where(expanded > peak_target, compressed, expanded)
    expanded = np.minimum(expanded, np.float32(peak_limit))
    return y + np.float32(hdr_strength) * (expanded - y)


def phone_illuminant_gains(
    rgb: np.ndarray, *, dark_weight: float, dark_fraction: float, daylight_neutrals: bool = False, indoor_weight: float = 0.0
) -> tuple[np.ndarray, float]:
    """Conservative neutral-highlight adaptation for a mostly black night scene.

    Gray-world estimates are unreliable on a warm rock wall or a sunset. Only
    enable this estimate when a large black area and the dark-scene cue agree,
    and when enough unsaturated bright samples support an illuminant estimate.
    The caller preserves luminance, separating this color change from exposure
    and the preexisting scene classification.
    """
    from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb
    from .tone import luminance_rec2020

    strength = 0.5 * dark_weight * float(_smoothstep((dark_fraction - 0.10) / 0.15))
    gains = np.ones(3, dtype=np.float32)
    if strength <= 0:
        if daylight_neutrals and dark_weight < 1:
            return phone_warm_white_gains(rgb, dark_weight=dark_weight, indoor_weight=indoor_weight)
        return gains, 0.0
    values = np.maximum(np.asarray(rgb, dtype=np.float32), 0)
    y = luminance_rec2020(values)
    lab = linear_srgb_to_oklab(rec2020_to_linear_srgb(values))
    relative_chroma = np.hypot(lab[..., 1], lab[..., 2]) / np.maximum(lab[..., 0], .02)
    lo, hi = np.percentile(y, [80, 99])
    neutral = (y > lo) & (y < hi) & (relative_chroma < .14) & np.all(values > 0, axis=-1)
    support = float(np.mean(neutral))
    strength *= float(_smoothstep((support - .005) / .03))
    if np.count_nonzero(neutral) < 16 or strength <= 0:
        return gains, 0.0
    white = np.median(values[neutral] / y[neutral, None], axis=0)
    log_gain = np.mean(np.log2(white)) - np.log2(white)
    gains = np.exp2(np.clip(log_gain * strength, -.65, .65)).astype(np.float32)
    return gains, float(strength)


def phone_shadow_red_offset(
    raw_rgb: np.ndarray, *, dark_weight: float, dark_fraction: float,
) -> float:
    """Estimate a small creative red-offset correction in blue night shadows.

    This is not a sensor black-level estimate. Only a mostly dark scene with
    blue-supported, near-black samples qualifies; red-lit scenes do not.
    Values and the returned offset are in development-neutral linear Rec.2020.
    """
    from .tone import luminance_rec2020

    weight = dark_weight * float(_smoothstep((dark_fraction - .10) / .15))
    if weight <= 0:
        return 0.0
    values = np.asarray(raw_rgb, dtype=np.float32)
    valid = np.all(np.isfinite(values), axis=-1)
    samples = values[valid]
    if len(samples) < 32:
        return 0.0
    y = luminance_rec2020(samples)
    lo, hi = np.percentile(y, [1, 5])
    near_black = samples[(y >= lo) & (y <= hi)]
    if len(near_black) < 16:
        return 0.0
    red, green, blue = np.median(near_black, axis=0)
    # Do not neutralize genuinely red illumination or neutral black surfaces.
    blue_support = float(_smoothstep((blue / max(float(green), 1e-5) - 1.5) / 1.5))
    blue_support *= float(1 - _smoothstep((red / max(float(blue), 1e-5) - 2) / 2))
    return float(min(max(red - green, 0), .012) * weight * blue_support)


def correct_phone_shadow_red(rgb: np.ndarray, *, offset: float, development_ev: float) -> np.ndarray:
    """Fade an estimated red offset out before ordinary midtones and highlights."""
    from .tone import luminance_rec2020

    if offset <= 0:
        return rgb
    values = np.asarray(rgb, dtype=np.float32)
    raw_y = luminance_rec2020(values) * np.float32(2.0**-development_ev)
    fade = 1 - _smoothstep((raw_y - .02) / .06)
    result = values.copy()
    result[..., 0] = np.maximum(result[..., 0] - offset * np.float32(2.0**development_ev) * fade, 0)
    return result


def lift_midtones(
    luminance: np.ndarray, *, low: float, center: float, high: float, ev: float
) -> np.ndarray:
    """Lift middle tones with a monotone curve anchored in shadows and highlights."""
    y = np.maximum(np.asarray(luminance, dtype=np.float32), 0.0)
    if ev <= 0.0:
        return y
    log_low = np.log2(max(low, 1e-6))
    log_center = np.log2(max(center, low * 1.01, 1e-6))
    log_high = np.log2(max(high, center * 1.01, 1e-6))
    effective_ev = min(ev, 0.40 * (log_high - log_center))
    log_y = np.log2(np.maximum(y, 1e-6))
    rise = _smoothstep((log_y - log_low) / max(log_center - log_low, 1e-4))
    fall = 1.0 - _smoothstep(
        (log_y - log_center) / max(log_high - log_center, 1e-4)
    )
    return y * np.exp2(np.float32(effective_ev) * rise * fall)


def highlight_shoulder(
    luminance: np.ndarray, *, start: float, span: float, strength: float
) -> np.ndarray:
    """Compress SDR highlights without clipping or changing darker tones."""
    y = np.maximum(np.asarray(luminance, dtype=np.float32), 0.0)
    if strength <= 0.0:
        return y
    above = np.maximum(y - np.float32(start), 0.0)
    return y - above + above / (1.0 + np.float32(strength) * above / max(span, 1e-4))


def phone_sdr_luminance(
    luminance: np.ndarray, *, contrast: float, pivot: float,
    shadow_lift: float,
    source: np.ndarray | None = None,
    dark_shoulder: float = 0.0,
    source_start: float = 0.0,
    source_anchor: float = 1.0,
) -> np.ndarray:
    """Scene-adaptive display tone with a stable white and a protected toe.

    Logit contrast changes shadows and highlights in opposite directions while
    retaining their ordering. The high-key lift only affects values below its
    anchor, so white clothing and sky highlights keep their separation.
    """
    y = np.clip(np.asarray(luminance, dtype=np.float32), 0.0, 1.0)
    if contrast > 0.0:
        safe = np.clip(y, 1e-6, 1.0 - 1e-6)
        anchor = np.log(pivot / (1.0 - pivot))
        logit = np.log(safe / (1.0 - safe))
        mapped = (1.0 + contrast) * logit - contrast * anchor
        y = np.where(y <= 0.0, 0.0, np.where(
            y >= 1.0, 1.0, 1.0 / (1.0 + np.exp(-mapped))
        )).astype(np.float32)
    if shadow_lift > 0.0:
        anchor = np.float32(0.30)
        gamma = np.float32(1.0 - 0.28 * shadow_lift)
        y = np.where(
            y < anchor,
            anchor * np.power(np.maximum(y / anchor, 0.0), gamma),
            y,
        )
    if dark_shoulder > 0.0 and source is not None:
        # Compress broad bright surfaces in dark scenes, while source RAW
        # highlights still identify the small lights that should stay bright.
        above = np.maximum(y - np.float32(0.15), 0.0)
        compressed = np.where(
            y > 0.15, 0.15 + above / (1.0 + dark_shoulder * above), y
        )
        preserve = _smoothstep(
            (np.asarray(source, dtype=np.float32) - source_start)
            / max(source_anchor - source_start, 1e-4)
        )
        y = compressed * (1.0 - preserve) + y * preserve
    return y


def _box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(values, ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(np.cumsum(integral, axis=0, dtype=np.float64), axis=1)
    size = 2 * radius + 1
    return (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    ).astype(np.float32) / np.float32(size * size)


def guided_log_luminance_base(
    luminance: np.ndarray, *, radius_fraction: float = 0.012, epsilon: float = 0.04
) -> np.ndarray:
    """Low-resolution edge-aware illumination map for chunked local contrast."""
    log_y = np.log2(np.maximum(np.asarray(luminance, dtype=np.float32), 1e-4))
    radius = max(2, round(min(log_y.shape) * radius_fraction))
    mean = _box_mean(log_y, radius)
    variance = np.maximum(_box_mean(log_y * log_y, radius) - mean * mean, 0.0)
    coefficient = variance / (variance + np.float32(epsilon))
    offset = mean * (1.0 - coefficient)
    return (_box_mean(coefficient, radius) * log_y + _box_mean(offset, radius)).astype(
        np.float32
    )


def resize_base_rows(
    base: Image.Image, *, width: int, full_height: int, start: int, stop: int
) -> np.ndarray:
    scale = base.height / full_height
    rows = base.resize(
        (width, stop - start),
        resample=Image.Resampling.BILINEAR,
        box=(0, start * scale, base.width, stop * scale),
    )
    return np.asarray(rows, dtype=np.float32)


def apply_local_contrast(
    luminance: np.ndarray, *, base_log: np.ndarray, strength: float,
    protection: np.ndarray | None = None,
) -> np.ndarray:
    y = np.maximum(np.asarray(luminance, dtype=np.float32), 0.0)
    if strength <= 0.0:
        return y
    detail = np.clip(np.log2(np.maximum(y, 1e-4)) - base_log, -0.6, 0.6)
    if protection is not None:
        detail *= 1 - np.clip(protection, 0, 1)
    return y * np.exp2(np.float32(strength) * detail)


def restore_display_detail(
    display_y: np.ndarray, *, source_y: np.ndarray, base_log: np.ndarray,
    strength: float, protection: np.ndarray | None = None,
) -> np.ndarray:
    """Restore restrained source detail after display highlight compression.

    A guided base separates illumination from detail. Applying the residual in
    display log-odds retains white/black endpoints without clipping the signal.
    The bound limits possible halos; callers must gate noisy dark scenes.
    """
    if strength <= 0.0:
        return display_y
    y = np.clip(np.asarray(display_y, dtype=np.float32), 0.0, 1.0)
    detail = np.clip(np.log2(np.maximum(source_y, 1e-4)) - base_log, -0.75, 0.75)
    if protection is not None:
        detail *= 1 - np.clip(protection, 0, 1)
    reliability = _smoothstep((y - 0.04) / 0.20)
    gain = np.exp2(np.float32(strength) * reliability * detail)
    return y * gain / (1.0 - y + y * gain)


def compress_dark_scene_illumination(
    luminance: np.ndarray, *, base_log: np.ndarray, exposure_ev: float,
    dark_weight: float, dark_fraction: float, highlight_ratio: float, high_key_weight: float,
) -> np.ndarray:
    """Compress broad illumination while retaining the local detail residual."""
    tail = float(_smoothstep((highlight_ratio - 2.0) / 4.0))
    strength = .8 * dark_weight * tail * (1 - float(_smoothstep((dark_fraction-.05)/.10)))
    exposed = luminance * np.float32(2**exposure_ev)
    excess = np.maximum(base_log + exposure_ev - np.log2(.25), 0)
    backlight = _smoothstep((base_log - np.log2(.16))/2)
    return exposed * np.exp2(-strength * excess - .45*high_key_weight*backlight)


def phone_warm_white_gains(rgb: np.ndarray, *, dark_weight: float, indoor_weight: float = 0.0) -> tuple[np.ndarray, float]:
    """Experimental supported warm-neutral illuminant adaptation after scene cues.

    A bounded correction preserves luminance at the call site. Restrictive
    chroma/support thresholds reject warm walls and sparse white highlights.
    """
    from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb
    from .tone import luminance_rec2020
    gains = np.ones(3, dtype=np.float32)
    values = np.maximum(np.nan_to_num(rgb, nan=0, posinf=0, neginf=0),0)
    y = luminance_rec2020(values)
    lab = linear_srgb_to_oklab(rec2020_to_linear_srgb(values))
    relative = np.hypot(lab[...,1],lab[...,2])/np.maximum(lab[...,0], .02)
    lo, hi = np.percentile(y,[70,99])
    neutral = ((y>lo)&(y<hi)&(relative<.055)&(values[...,0]>values[...,1]*1.015)
               &(values[...,2]<values[...,1]*.99)&np.all(values>0,axis=-1))
    support = float(neutral.mean())
    confidence = max(float(_smoothstep((support-.05)/.10)),
                     indoor_weight*float(_smoothstep((support-.005)/.03)))
    strength = .8*(1-dark_weight)*confidence
    if neutral.sum()<32 or strength <= 0:
        return gains, 0.0
    white = np.median(values[neutral]/y[neutral,None],axis=0)
    log_gain = np.mean(np.log2(white))-np.log2(white)
    gains = np.exp2(np.clip(log_gain*strength,-.3,.3)).astype(np.float32)
    return gains,float(strength)
