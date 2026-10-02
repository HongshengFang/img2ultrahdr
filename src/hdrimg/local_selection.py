"""Optional local Vision instance selection; durable masks are never cache entries."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image

from .errors import InputError


def reference_frame(store, source, recipe):
    """Reuse a current exact base even if its published result contains locals."""
    from dataclasses import replace
    from .editor import EditRecipe
    candidates = sorted((store.cache / 'renders').glob('*/complete.json'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            value = json.loads(path.read_text())
            packet = value.get('preview_packet', {})
            if (value.get('engine') == store.engine and value.get('source', {}).get('sha256') == source['sha256']
                and replace(EditRecipe.from_dict(value['recipe']), local_adjustments=()) == recipe
                and packet.get('version') == 2 and Path(packet['base_sdr']).is_file()):
                return packet
        except (OSError, ValueError, KeyError, InputError):
            continue
    return store.render(source, recipe, remember=False, floating_preview=True, preview_version=2)['preview_packet']


def _helper(cache):
    configured = os.environ.get('HDRIMG_LOCAL_SELECTION_HELPER')
    if configured and Path(configured).is_file():
        return Path(configured)
    source = Path(__file__).parent / 'profiles/local-selection.swift'
    key = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
    target = cache / 'local-helper' / key / 'local-selection'
    if not target.is_file():
        compiler = shutil.which('swiftc')
        if not compiler: return None
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            staged = Path(temp) / 'helper'
            subprocess.run([compiler, '-O', str(source), '-o', str(staged)], check=True, capture_output=True, timeout=8)
            os.replace(staged, target)
    return target


def select_region(store, source, recipe, point):
    if not isinstance(point, list) or len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in point):
        raise InputError('Selection point must be normalized image coordinates')
    fallback = {'mode': 'soft', 'reason': 'background', 'point': point}
    packet = reference_frame(store, source, recipe)
    pixels = np.fromfile(packet.get('base_sdr', packet['sdr']), dtype='<f2').reshape(packet['height'], packet['width'], 4)[..., :3].astype(np.float32)
    from .render import _quantize_sdr
    reference = _quantize_sdr(pixels)
    identity = hashlib.sha256(reference.tobytes() + str(reference.shape).encode() + b'local-selection-2').hexdigest()
    root = store.cache / 'derived' / store.engine / ('selection-'+identity)
    started = time.monotonic()
    try:
        if not (root / 'complete.json').is_file():
            store.progress('正在选择局部范围')
            helper = _helper(store.cache)
            if helper is None: return {**fallback, 'reason': 'unavailable'}
            root.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.selection-', dir=root.parent) as temp:
                work = Path(temp)
                Image.fromarray(reference).save(work / 'reference.png')
                subprocess.run([str(helper), str(work / 'reference.png'), str(work)], check=True,
                               capture_output=True, timeout=max(.01, 8-(time.monotonic()-started)))
                if root.exists(): shutil.rmtree(root)
                Path(temp).rename(root)
        record = json.loads((root / 'complete.json').read_text())
        with Image.open(root / 'labels.png') as image:
            labels = np.asarray(image)
        x = min(labels.shape[1]-1, int(point[0]*labels.shape[1]))
        y = min(labels.shape[0]-1, int(point[1]*labels.shape[0]))
        instance = int(labels[y, x])
        if not instance or instance not in record['instances']: return fallback
        selected = root / f'{instance}.png'
        with Image.open(selected) as image:
            values = np.asarray(image, np.float32)
            px = min(image.width-1, int(point[0]*image.width)); py = min(image.height-1, int(point[1]*image.height))
            if values[py, px] < 6553 or values.max() <= 0: return fallback
        data = selected.read_bytes(); checksum = hashlib.sha256(data).hexdigest()
        assets = store.support / 'selection-assets' / source['sha256']; assets.mkdir(parents=True, exist_ok=True)
        asset = assets / (checksum+'.png')
        try:
            intact = hashlib.sha256(asset.read_bytes()).hexdigest() == checksum
        except OSError:
            intact = False
        # Reselecting the same instance must also repair a damaged durable
        # asset; existence alone is not proof that its content is usable.
        if not intact:
            with tempfile.NamedTemporaryFile(dir=assets, suffix='.partial', delete=False) as out:
                out.write(data); temporary = Path(out.name)
            os.replace(temporary, asset)
        return {'mode': 'smart', 'mask_ref': checksum, 'point': point,
                'elapsed_seconds': time.monotonic()-started, 'mask_width': values.shape[1], 'mask_height': values.shape[0]}
    except subprocess.TimeoutExpired:
        return {**fallback, 'reason': 'timeout'}
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return {**fallback, 'reason': 'unavailable'}
