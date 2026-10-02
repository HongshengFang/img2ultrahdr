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


def make_packet(work: Path, target: Path, prepared: dict, recipe, render: dict, *, version=PACKET_VERSION, masks=None) -> dict:
    scene, info = open_scene(Path(prepared['preview']))
    # Half precision is for display buffers only; RAW and the precise renderer
    # continue to use their original float32 input.
    write_rgba16f(work / 'scene.rgba16f', scene)
    analysis = np.load(prepared['analysis']['sample'], mmap_mode='r')
    y = luminance_rec2020(analysis)
    valid = y[np.isfinite(y) & (y >= 0)]
    quantiles = np.quantile(valid, np.linspace(0, 1, 1025)) if valid.size else np.zeros(1025)
    packet = {'version': version, 'gpu_version': GPU_VERSION,
        'width': info.width, 'height': info.height, 'pixel_format': 'rgba16FloatLE',
        'row_bytes': info.width * 8, 'scene_color_space': 'linear-rec2020',
        'sdr_color_space': 'linear-display-p3', 'hdr_color_space': 'linear-rec2020',
        'reference_white_nits': 203.0, 'peak_nits': 1000.0,
        'scene': str(target / 'scene.rgba16f'), 'sdr': str(target / 'sdr.rgba16f'),
        'hdr': str(target / 'hdr.rgba16f'), 'anchor_recipe': asdict(recipe),
        'tone': render['tone_mapping'], 'exposure': render['exposure'],
        'look': render['look'], 'luminance_quantiles': quantiles.tolist(),
        'precision': 'exact', 'natural_sdr_precision': 'accepted pre-JPEG uint8' if recipe.style == 'phone-natural' else 'float32 to float16'}
    if version == 2:
        packet['base_sdr'] = str(target / ('base-sdr.rgba16f' if (work / 'base-sdr.rgba16f').exists() else 'sdr.rgba16f'))
        packet['base_hdr'] = str(target / ('base-hdr.rgba16f' if (work / 'base-hdr.rgba16f').exists() else 'hdr.rgba16f'))
        packet['local_masks'] = {}
        for region_id, values in (masks or {}).items():
            name = 'mask-' + region_id + '.r32f'
            np.asarray(values, dtype='<f4').tofile(work / name)
            packet['local_masks'][region_id] = {'path': str(target / name), 'width': values.shape[1], 'height': values.shape[0]}
    return packet
