"""Audit paired phone fixtures through the complete production RAW pipeline.

DNG is accepted only by the scoped test adapter below, never by the public CLI.
RAW profiles are optional experimental overlays; all reference developments use
the same overlay. Existing experiments are immutable and resumable only with
identical source hashes, options and fixture hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from hdrimg import pipeline
from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.pipeline import RenderOptions
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from hdrimg.tools import resolve_tools, run_checked
from audit_phone_clear import (
    _contact_sheet, _metrics, _panel, _read_hdr, _read_sdr, _regions,
    _spatial_metrics,
)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def reduced(rgb, edge=1024):
    h, w = rgb.shape[:2]
    ratio = min(1., edge / max(h, w))
    size = (max(1, round(w * ratio)), max(1, round(h * ratio)))
    return np.stack([np.asarray(Image.fromarray(rgb[..., c]).resize(
        size, Image.Resampling.BOX)) for c in range(3)], axis=-1)


def run(args):
    samples, output = args.samples.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixtures = sorted(samples.glob('*.dng'))
    if not fixtures:
        raise ValueError('No paired DNG fixtures')
    profile = args.raw_profile.read_text() if args.raw_profile else ''
    options = dict(style='phone-clear', keep_intermediates=True,
                   local_contrast=args.local_contrast)
    root = Path(__file__).resolve().parents[1]
    sources = [*sorted((root / 'src/hdrimg').rglob('*.py')),
               *sorted((root / 'src/hdrimg/profiles').glob('*')), Path(__file__).resolve(),
               root / 'scripts/audit_phone_clear.py', args.regions.resolve()]
    config = {'source_sha256': {str(p.relative_to(root)): sha(p) for p in sources if p.is_file()},
              'fixtures_sha256': {p.name: sha(p) for p in sorted(samples.iterdir())
                                  if p.suffix.lower() in ('.jpg', '.dng')},
              'raw_profile': profile, 'options': options,
              'candidate': args.candidate, 'compact': args.compact, 'ids': args.ids}
    config_path = output / 'configuration.json'
    if config_path.exists():
        if json.loads(config_path.read_text()) != config:
            raise ValueError('Experiment configuration changed; use a new output directory')
    else:
        config_path.write_text(json.dumps(config, indent=2) + '\n')
    tools = resolve_tools(require_raw=True, require_exif=True)
    region_specs = json.loads(args.regions.read_text())
    original_develop = pipeline.develop_raw

    def development(source, destination, *, work_dir, profile_overlay=None, **kwargs):
        if profile:
            combined = work_dir / 'experiment.pp3'
            combined.write_text((profile_overlay.read_text() if profile_overlay else '') + '\n' + profile)
            profile_overlay = combined
        return original_develop(source, destination, work_dir=work_dir,
                                profile_overlay=profile_overlay, **kwargs)

    records, sheets = [], []
    for number, source in enumerate(fixtures, 1):
        if args.ids and number not in {int(n) for n in args.ids.split(',')}:
            continue
        stem = source.name.split('.RAW-', 1)[0]
        target = output / f'{number:02}_{stem}'
        record_path = target / 'record.json'
        if record_path.exists():
            row = json.loads(record_path.read_text())
            records.append(row)
            continue
        phone = list(samples.glob(stem + '*.jpg'))
        if len(phone) != 1:
            raise ValueError(f'Expected one paired JPEG for {stem}')
        print(f'Start {number:02} {stem}', flush=True)
        started = time.monotonic()
        # Exact-fixture adapter: no format validation changes escape this call.
        def fixture_input(path):
            if path.resolve() != source or not source.is_file():
                raise ValueError('Unexpected internal DNG fixture')
            return source
        with patch.object(pipeline, 'validate_raw_input', fixture_input), \
             patch.object(pipeline, 'develop_raw', development):
            result = pipeline.render_raw(source, RenderOptions(output=target, **options),
                **({'_style_override': PHONE_CLEAR_CANDIDATE} if args.candidate else {}))
        manifest = json.loads(result.manifest.read_text())
        with tempfile.TemporaryDirectory(prefix='phone-reference-') as tmp:
            decoded = Path(tmp) / 'phone.rgba16f'
            run_checked([tools.ultrahdr, '-m', '1', '-j', phone[0], '-o', '0',
                         '-O', '4', '-z', decoded], label='decode phone reference', timeout=600)
            ps, pd = _read_sdr(phone[0], step=1)
            ps, ph = reduced(ps), reduced(_read_hdr(decoded, pd, step=1))
        # Evaluate the delivered JPEG, including gain-map reconstruction.
        decoded = target / 'decoded_hdr.rgba16f'
        run_checked([tools.ultrahdr, '-m', '1', '-j', result.ultrahdr, '-o', '0',
                     '-O', '4', '-z', decoded], label='decode candidate audit', timeout=600)
        rs, rd = _read_sdr(result.ultrahdr, step=1)
        rs, rh = reduced(rs), reduced(_read_hdr(decoded, rd, step=1))
        target_hdr = reduced(_read_hdr(result.hdr_raw, rd, step=1))
        target_y = target_hdr @ np.array([.2627, .6780, .0593], np.float32)
        decoded_y = rh @ np.array([.2627, .6780, .0593], np.float32)
        codec_error = {str(p): float(abs(np.percentile(decoded_y,p)-np.percentile(target_y,p)) /
                       max(float(np.percentile(target_y,p)),1e-5)) for p in (50,95)}
        codec_error['passed'] = codec_error['50'] < .06 and codec_error['95'] < .08
        rhp = rh @ REC2020_TO_DISPLAY_P3.T
        spec = region_specs.get(str(number), {})
        row = {'id': number, 'stem': stem, 'scene': spec.get('scene'),
               'phone': _metrics(ps, ph, hdr_gamut='display-p3'),
               'repo': _metrics(rs, rh, hdr_gamut='rec2020'),
               'spatial': _spatial_metrics(ps, rs, ph, rhp),
               'regions': _regions(ps, rs, ph, rhp, spec),
               'not_present': spec.get('not_present', []),
               'render': manifest['render'], 'raw_development': manifest['raw_development'],
               'validation': manifest['validation'],
               'codec': manifest['outputs']['ultrahdr'],
               'metric_source': 'final Ultra HDR decoded SDR/HDR',
               'codec_luminance_relative_error': codec_error,
               'manifest': str(result.manifest), 'elapsed_seconds': time.monotonic() - started}
        np.savez_compressed(target / 'previews.npz', phone_sdr=ps, phone_hdr=ph,
                            repo_sdr=rs, repo_hdr=rhp)
        panels = [_panel(im, hdr=h, width=520, height=720)
                  for im, h in ((ps, False), (rs, False), (ph, True), (rhp, True))]
        sheet = Image.new('RGB', (2080, 720), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for col, panel in enumerate(panels):
            sheet.paste(panel, (col * 520 + (520-panel.width)//2, 4))
            draw.text((col * 520 + 10, 697), f'{number:02} ' +
                      ('Phone SDR', 'Phone Clear SDR', 'Phone HDR preview', 'Phone Clear HDR preview')[col], fill='#111111')
        sheet.save(target / 'comparison.jpg', quality=94)
        if args.compact:
            # Keep final decoded native HDR for review, not multiple huge copies.
            import tifffile
            raw = tifffile.memmap(result.scene)
            tifffile.imwrite(target/'scene-preview.tif', reduced(raw), photometric='rgb')
            del raw
            result.scene.unlink()
            result.hdr_raw.unlink()
            row['retention'] = 'full scene and encoder target released after audit; final decoded HDR retained'
        for name, real in [('sdr.jpg', result.ultrahdr.name), ('hdr.rgba16f', decoded.name)]:
            alias = target / name
            if not alias.exists():
                alias.symlink_to(real)
        record_path.write_text(json.dumps(row, indent=2) + '\n')
        records.append(row)
        print(f'Completed {number:02}: {row["elapsed_seconds"]:.1f}s', flush=True)
    for row in records:
        with np.load(output / f'{row["id"]:02}_{row["stem"]}' / 'previews.npz') as preview:
            panels = [_panel(preview[key], hdr='hdr' in key) for key in
                      ('phone_sdr', 'repo_sdr', 'phone_hdr', 'repo_hdr')]
        sheets.append((row['id'], panels))
    for start in range(0, len(sheets), 4):
        _contact_sheet(sheets[start:start+4], output / f'contact_{start+1:02}_{min(start+4,len(sheets)):02}.png')
    report = {'scope': 'full production RAW development and full-resolution render; internal DNG adapter',
              'metric_sampling': 'area reduction to 1024 after audit framing; reference HDR is linear Display P3',
              'pair_count': len(records), 'pairs': records}
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regions', type=Path, default=Path('docs/phone-clear-regions.json'))
    parser.add_argument('--raw-profile', type=Path)
    parser.add_argument('--local-contrast', type=float)
    parser.add_argument('--candidate', action='store_true', help='Evaluate internal Clear V7 without changing the preset')
    parser.add_argument('--compact', action='store_true', help='Retain native decoded HDR and a float scene preview')
    parser.add_argument('--ids', help='Comma-separated diagnostic sample numbers')
    run(parser.parse_args())
