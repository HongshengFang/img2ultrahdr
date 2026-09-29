"""Develop an isolated all-scene deconvolution experiment after adaptive NR."""
from pathlib import Path
import argparse
import json
import tempfile

import numpy as np
import tifffile
from PIL import Image

from hdrimg.raw import develop_raw, phone_denoise_overlay
from hdrimg.render import open_scene
from hdrimg.tools import resolve_tools


def prepare(samples: Path, adaptive: Path, output: Path, amount: float,
            radius: float, damping: float):
    samples, adaptive, output = samples.resolve(), adaptive.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ('full', 'area', 'profiles'):
        (output / name).mkdir()
    tools = resolve_tools(require_raw=True, require_exif=True)
    rows = []
    for decision in json.loads((adaptive / 'cache.json').read_text()):
        stem = decision['stem']
        source = next(samples.glob(stem + '.RAW-*.dng'))
        profile = output / 'profiles' / f'{stem}.pp3'
        profile.write_text(phone_denoise_overlay(1, extra_luma=decision['extra_denoise_weight'])
            + '\n[Sharpening]\nEnabled=true\nMethod=rld\nContrast=20\nBlurRadius=0.2\n'
            + f'DeconvRadius={radius:g}\nDeconvAmount={round(amount)}\nDeconvDamping={round(damping)}\nDeconvIterations=20\n')
        target = output / 'full' / f'{stem}.tif'
        with tempfile.TemporaryDirectory(prefix='phone-sharpen-cache-') as tmp:
            develop_raw(source.resolve(), target.resolve(), tools=tools,
                        white_balance='camera', temperature_k=None, tint=1,
                        work_dir=Path(tmp), profile_overlay=profile)
        scene, info = open_scene(target)
        size = (round(info.width*1024/max(info.width, info.height)),
                round(info.height*1024/max(info.width, info.height)))
        small = np.stack([np.asarray(Image.fromarray(scene[..., c], mode='F')
                            .resize(size, Image.Resampling.BOX)) for c in range(3)], axis=-1)
        tifffile.imwrite(output / 'area' / target.name, small, photometric='rgb')
        del scene
        row = {**decision, 'sharpening': {'method': 'rld', 'radius': radius,
                'amount': amount, 'damping': damping, 'iterations': 20},
                'full_size': [info.width, info.height]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    (output / 'cache.json').write_text(json.dumps(rows, indent=2)+'\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('samples', 'adaptive', 'output'):
        p.add_argument(name, type=Path)
    p.add_argument('--amount', type=float, default=50)
    p.add_argument('--radius', type=float, default=.55)
    p.add_argument('--damping', type=float, default=20)
    a = p.parse_args()
    prepare(a.samples, a.adaptive, a.output, a.amount, a.radius, a.damping)
