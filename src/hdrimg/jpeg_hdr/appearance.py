"""HDR-only phone appearance for an already rendered sRGB JPEG.

Reuse the RAW workflow's smooth diffuse-light expansion, without changing its
SDR base or pretending an SDR photograph contains RAW radiance information.
"""
import numpy as np
from ..phone_tone import phone_hdr_luminance

SDR_WHITE_NITS = 203.0


def phone_gain_ev(sdr_y, *, peak_nits):
    peak = peak_nits / SDR_WHITE_NITS
    target = phone_hdr_luminance(
        sdr_y, source_y=sdr_y, source_highlight_start=.42,
        source_highlight_anchor=1., dark_weight=0., dark_highlight_power=1.,
        manual_highlight_bonus=0., manual_highlight_start=.42,
        manual_highlight_anchor=1., midtone_gain=1.9,
        peak_target=peak, peak_limit=peak, highlight_anchor=1.,
        shoulder_strength=1., hdr_strength=1., smooth_highlights=True,
    )
    return np.maximum(np.log2(np.maximum(target, 1e-7) /
                              np.maximum(sdr_y, 1e-7)), 0).astype(np.float32)


def constrain_gain(gain_ev, linear_rgb, cap, *, max_ev, peak_ratio=None):
    result = np.minimum(np.clip(gain_ev, 0, max_ev), cap)
    if peak_ratio is not None:
        # A scalar luminance-only gain preserves the stored RGB ratios. Bound
        # every channel, not just luminance, to avoid clipping colored details.
        peak_ev = np.log2(peak_ratio / np.maximum(linear_rgb.max(axis=-1), 1e-7))
        result = np.minimum(result, np.maximum(peak_ev, 0))
    return result.astype(np.float32)
