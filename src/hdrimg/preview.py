"""Versioned, immutable scene-linear packets for the native interactive renderer.

The exact frames remain the authority. The renderer recomputes global tone from
the retained RAW scene and transports the last precise spatial/color corrections.
This is deliberately an approximation until the next complete render finishes.
"""
from dataclasses import asdict
from pathlib import Path
import numpy as np
from .render import open_scene, write_rgba16f
from .tone import luminance_rec2020

PACKET_VERSION = 1
GPU_VERSION = 'scene-tone-2'


def make_packet(work: Path, target: Path, prepared: dict, recipe, render: dict) -> dict:
    scene, info = open_scene(Path(prepared['preview']))
    # Half precision is for display buffers only; RAW and the precise renderer
    # continue to use their original float32 input.
    write_rgba16f(work / 'scene.rgba16f', scene)
    analysis = np.load(prepared['analysis']['sample'], mmap_mode='r')
    y = luminance_rec2020(analysis)
    quantiles = np.quantile(y[np.isfinite(y) & (y >= 0)], np.linspace(0, 1, 1025))
    return {'version': PACKET_VERSION, 'gpu_version': GPU_VERSION,
        'width': info.width, 'height': info.height, 'pixel_format': 'rgba16FloatLE',
        'row_bytes': info.width * 8, 'scene_color_space': 'linear-rec2020',
        'sdr_color_space': 'linear-display-p3', 'hdr_color_space': 'linear-rec2020',
        'reference_white_nits': 203.0, 'peak_nits': 1000.0,
        'scene': str(target / 'scene.rgba16f'), 'sdr': str(target / 'sdr.rgba16f'),
        'hdr': str(target / 'hdr.rgba16f'), 'anchor_recipe': asdict(recipe),
        'tone': render['tone_mapping'], 'exposure': render['exposure'],
        'look': render['look'], 'luminance_quantiles': quantiles.tolist(),
        'precision': 'exact', 'natural_sdr_precision': 'accepted pre-JPEG uint8' if recipe.style == 'phone-natural' else 'float32 to float16'}
