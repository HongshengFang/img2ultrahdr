"""Noise-supported smoothing of structurally quiet blue RAW surfaces.

Area-filtered color and structure identify support; native Haar residuals
estimate the filter's noise scale. This is a conservative color/structure
heuristic, not semantic sky segmentation. Work is chunked with full halos.
"""
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from .color import rec2020_to_linear_display_p3
from .phone_tone import _box_mean, _smoothstep, resize_base_rows
from .tone import luminance_rec2020


def smooth_blue_support(rgb: np.ndarray, *, development_ev: float) -> np.ndarray:
    values = np.nan_to_num(np.asarray(rgb, dtype=np.float32), nan=0, posinf=0, neginf=0)
    p3 = np.maximum(rec2020_to_linear_display_p3(values), 1e-7)
    y = np.maximum(luminance_rec2020(values), 1e-7)
    blue = _smoothstep((p3[..., 2] / np.maximum(p3[..., 0], 1e-6) - 1.15) / .65)
    blue *= _smoothstep((p3[..., 2] / np.maximum(p3[..., 1], 1e-6) - 1.02) / .35)
    base = _box_mean(np.log2(np.maximum(y, 1e-6)), 2)
    variance = np.maximum(_box_mean(base * base, 2) - _box_mean(base, 2)**2, 0)
    mask = blue * (1 - _smoothstep((np.sqrt(variance) - .035) / .10))
    mask *= _smoothstep((y * 2**-development_ev - .004) / .012)
    return _box_mean(mask, 1)


def _guided_surface(rgb: np.ndarray, epsilon: float, radius: int = 3) -> np.ndarray:
    y = np.maximum(luminance_rec2020(rgb), 1e-7)
    guide = np.log2(y)
    mean = _box_mean(guide, radius)
    variance = np.maximum(_box_mean(guide * guide, radius) - mean * mean, 0)
    coefficient = variance / (variance + epsilon)
    offset = mean * (1 - coefficient)
    smooth_log = _box_mean(coefficient, radius) * guide + _box_mean(offset, radius)
    channels = []
    for channel in range(3):
        ratio = np.log2(np.maximum(rgb[..., channel] / y, .01))
        average = _box_mean(ratio, radius)
        covariance = _box_mean(guide * ratio, radius) - mean * average
        a = covariance / (variance + epsilon)
        b = average - a * mean
        channels.append(np.exp2(_box_mean(a, radius) * guide + _box_mean(b, radius)))
    color = np.stack(channels, axis=-1)
    color /= np.maximum(luminance_rec2020(color), 1e-7)[..., None]
    return color * np.exp2(smooth_log)[..., None]


def denoise_blue_surfaces(source: Path, destination: Path, *, strength: float = 1,
                          development_ev: float = -2, chunk_rows: int = 256) -> dict:
    """Write a separate float TIFF only when support exists, preserving its ICC.

    The caller retains the input if ``applied`` is false. Existing destinations
    are never replaced. Strength zero is an exact bypass.
    """
    if not 0 <= strength <= 1:
        raise ValueError("Surface denoise strength must be between 0 and 1")
    if chunk_rows < 1:
        raise ValueError("Chunk size must be positive")
    if destination.exists():
        raise FileExistsError(destination)
    record = {'method': 'noise-supported smooth blue surfaces', 'strength': strength,
              'applied': False}
    if strength == 0:
        return {**record, 'reason': 'disabled'}
    scene = tifffile.memmap(source)
    h, w = scene.shape[:2]
    if min(h, w) < 8:
        return {**record, 'reason': 'too_small'}
    scale = min(1.0, 1024 / max(h, w))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    preview = np.stack([np.asarray(Image.fromarray(scene[..., c], mode='F').resize(
        size, Image.Resampling.BOX)) for c in range(3)], axis=-1)
    mask = smooth_blue_support(preview, development_ev=development_ev)
    record.update(mask_mean=float(mask.mean()), mask_support=float(np.mean(mask > .8)))
    if np.max(mask) <= 1e-6:
        return {**record, 'reason': 'no_supported_blue_surface'}
    mask_image = Image.fromarray(mask, mode='F')
    samples = []
    for start in range(0, h - 1, 128):
        stop = min(h, start + 128)
        stop -= (stop - start) % 2
        y = np.log2(np.maximum(luminance_rec2020(np.asarray(scene[start:stop])), 1e-6))
        weight = resize_base_rows(mask_image, width=w, full_height=h, start=start, stop=stop)
        width = w - w % 2
        hh = (y[::2, :width:2] - y[::2, 1:width:2]
              - y[1::2, :width:2] + y[1::2, 1:width:2]) * .5
        values = hh[(weight[::2, :width:2] > .8) & np.isfinite(hh)]
        if values.size:
            samples.append(values[::max(1, values.size // 5000)])
    sigma = float(np.median(np.abs(np.concatenate(samples))) / .67449) if samples else 0.0
    epsilon = float(np.clip(4 * sigma * sigma, 1e-4, .04))
    with tifffile.TiffFile(source) as tif:
        icc_tag = tif.pages[0].tags.get(34675)
        icc = icc_tag.value if icc_tag is not None else None
    tags = [(34675, 'B', len(icc), icc, False)] if icc else []
    output = tifffile.memmap(destination, shape=scene.shape, dtype='float32',
                            photometric='rgb', extratags=tags)
    radius = 3
    halo = 2 * radius
    for start in range(0, h, chunk_rows):
        stop = min(h, start + chunk_rows)
        a, b = max(0, start - halo), min(h, stop + halo)
        rgb = np.asarray(scene[a:b], dtype=np.float32)
        candidate = _guided_surface(rgb, epsilon, radius)
        core = slice(start - a, stop - a)
        weight = .85 * strength * resize_base_rows(mask_image, width=w, full_height=h,
                                                   start=start, stop=stop)
        output[start:stop] = rgb[core] + weight[..., None] * (candidate[core] - rgb[core])
    output.flush()
    del output, scene
    return {**record, 'applied': True, 'native_log_noise_sigma': sigma,
            'guide_epsilon': epsilon, 'radius': radius, 'blend': .85 * strength}
