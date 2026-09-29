"""Conservative residual-noise cues for automatic phone-clear RAW denoise."""
from __future__ import annotations

import numpy as np

from .phone_tone import phone_scene_decision
from .tone import luminance_rec2020


def _smooth(value: float) -> float:
    t = float(np.clip(value, 0, 1))
    return t*t*(3-2*t)


def residual_shadow_noise(scene: np.ndarray, *, development_ev: float) -> dict[str, float | int]:
    """Estimate noise on the least textured quarter of supported dark tiles.

    The first balanced development is the input. Two-pixel averaging reduces
    demosaic correlation before a diagonal Haar residual/MAD estimate. This
    remains a heuristic: a low-texture image is not proof of pure sensor noise.
    """
    y = luminance_rec2020(scene)*np.float32(2**-development_ev)
    y = np.nan_to_num(y, nan=0, posinf=0, neginf=0)
    h, w = y.shape
    h -= h % 64
    w -= w % 64
    empty = {"relative_noise": 0.0, "shadow_support": 0.0, "flat_tiles": 0}
    if not h or not w:
        return empty
    tiles = y[:h, :w].reshape(h//64, 64, w//64, 64).transpose(0, 2, 1, 3).reshape(-1, 64, 64)
    median = np.median(tiles, axis=(1, 2))
    coarse = tiles.reshape(-1, 8, 8, 8, 8).mean(axis=(2, 4))
    contrast = (np.percentile(coarse, 90, axis=(1, 2))
                - np.percentile(coarse, 10, axis=(1, 2)))/(np.maximum(median, 0)+.005)
    shadows = (median > .001) & (median < .08) & np.isfinite(contrast)
    support = float(np.mean(shadows))
    if np.count_nonzero(shadows) < 16:
        return {**empty, "shadow_support": support}
    flat = shadows & (contrast <= np.percentile(contrast[shadows], 25))
    small = tiles[flat].reshape(-1, 32, 2, 32, 2).mean(axis=(2, 4))
    hh = (small[:, ::2, ::2]-small[:, ::2, 1::2]
          -small[:, 1::2, ::2]+small[:, 1::2, 1::2])/2
    sigma = np.median(abs(hh), axis=(1, 2))/.67448975
    return {"relative_noise": float(np.median(sigma/(median[flat]+.005))),
            "shadow_support": support, "flat_tiles": int(flat.sum())}


def phone_denoise_decision(scene: np.ndarray, *, development_ev: float) -> dict[str, float | int]:
    step = max(1, int(np.ceil(max(scene.shape[:2])/1024)))
    cue = phone_scene_decision(scene[::step, ::step], development_ev=development_ev, peak_nits=1000)
    if cue.dark_weight <= 0:
        return {"dark_weight": 0.0, "relative_noise": 0.0, "shadow_support": 0.0,
                "flat_tiles": 0, "extra_denoise_weight": 0.0}
    noise = residual_shadow_noise(scene, development_ev=development_ev)
    weight = (cue.dark_weight * _smooth((noise["relative_noise"]-.03)/.05)
              * _smooth((noise["shadow_support"]-.10)/.35))
    return {"dark_weight": cue.dark_weight, **noise, "extra_denoise_weight": weight}
