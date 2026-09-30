"""Full camera pipeline validation with compact, decoded deliverable evidence."""
import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import hdrimg
from hdrimg.pipeline import render_raw, RenderOptions
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from hdrimg.tools import resolve_tools, run_checked
from hdrimg.color import REC2020_TO_DISPLAY_P3
from audit_phone_raw_first import reduced, sha
from audit_phone_clear import _decode_transfer, _metrics, _panel


def full_sdr(path):
    with Image.open(path) as image:
        return _decode_transfer(np.asarray(image, np.float32)/255), image.size


def run(args):
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for p in Path('pics').iterdir() if p.suffix.lower() in {'.cr2','.raf'})
    if args.names:
        names = set(args.names.split(',')); sources = [p for p in sources if p.stem in names]
    if args.shard:
        index, count = map(int, args.shard.split('/'))
        sources = [p for i,p in enumerate(sources) if i % count == index]
    tools = resolve_tools()
    implementation = Path(hdrimg.__file__).resolve().parent
    config = {str(Path('src/hdrimg')/p.relative_to(implementation)): sha(p)
              for p in sorted(implementation.rglob('*')) if p.is_file() and '__pycache__' not in str(p)}
    config['recipe'] = args.style
    config['validation_script_sha256'] = sha(Path(__file__))
    recipe_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    for source in sources:
        target = root/source.stem; record_path = target/'audit.json'
        if record_path.exists():
            old = json.loads(record_path.read_text())
            if old['recipe_sha256'] != recipe_hash or old['source_sha256'] != sha(source):
                raise ValueError('Changed experiment; use a fresh directory')
            continue
        started = time.monotonic()
        print('START', source.name, args.style, flush=True)
        style = 'phone-natural' if args.style == 'natural' else 'phone-clear'
        result = render_raw(source, RenderOptions(output=target, style=style),
            **({'_style_override':PHONE_CLEAR_CANDIDATE} if args.style == 'candidate' else {}))
        record = json.loads(result.manifest.read_text())
        with tempfile.TemporaryDirectory(prefix='hdrimg-camera-audit-') as temporary:
            decoded = Path(temporary)/'decoded.rgba16f'
            run_checked([tools.ultrahdr, '-m','1','-j',result.ultrahdr,'-o','0','-O','4','-z',decoded],
                        label='decode camera audit', timeout=600)
            sdr, (width,height) = full_sdr(result.ultrahdr)
            raw = np.memmap(decoded, dtype='<f2', mode='r', shape=(height,width,4))
            hdr = np.asarray(raw[..., :3], np.float32)
            a, b = reduced(sdr), reduced(hdr)
            metrics = _metrics(a, b, hdr_gamut='rec2020')
            b = b @ REC2020_TO_DISPLAY_P3.T
            np.savez_compressed(target/'previews.npz', sdr=a, hdr=b)
            for mode, rgb in [('sdr',a),('hdr',b)]:
                _panel(rgb, hdr=mode=='hdr', width=800, height=1050).save(target/f'{mode}-preview.jpg',quality=95)
            # Original-coordinate native boundary crops, never production masks.
            if source.stem in {'0N6A9406', '0N6A9416'}:
                # Full-frame coordinates, independent of paired-phone framing.
                hh, ww = sdr.shape[:2]
                boxes = {'legs':(.28,.62,.63,.995), 'hair':(.32,.1,.68,.34)}
                for label, box in boxes.items():
                    x0,y0,x1,y1 = [round(v*n) for v,n in zip(box,[ww,hh,ww,hh])]
                    for mode, rgb in [('sdr',sdr),('hdr',hdr)]:
                        crop = np.asarray(rgb[y0:y1,x0:x1],np.float32)
                        if mode == 'hdr': crop = crop @ REC2020_TO_DISPLAY_P3.T
                        _panel(crop,hdr=mode=='hdr',width=x1-x0+10,height=y1-y0+34).save(
                            target/f'{label}-{mode}-native.png')
            decoded_hash = sha(decoded)
            del raw
        natural_comparison = None
        if args.compare:
            before = args.compare/source.stem/f'{source.stem}_ultrahdr.jpg'
            if before.exists():
                before_sdr, _ = full_sdr(before)
                natural_comparison = {'jpeg_sha256_equal':sha(before)==sha(result.ultrahdr),
                                      'sdr_pixels_equal':bool(np.array_equal(before_sdr,sdr))}
                with tempfile.TemporaryDirectory(prefix='hdrimg-before-decode-') as temporary:
                    path = Path(temporary)/'before.raw'
                    run_checked([tools.ultrahdr,'-m','1','-j',before,'-o','0','-O','4','-z',path],
                                label='decode frozen baseline',timeout=600)
                    natural_comparison['decoded_hdr_sha256_equal'] = sha(path)==decoded_hash
        report = {'source':str(source.resolve()), 'source_sha256':sha(source),
                  'recipe_sha256':recipe_hash,'recipe':config,'metrics':metrics,
                  'metric_source':'final Ultra HDR decoded SDR/HDR',
                  'metric_sampling':'full frame; area reduction to 1024 after final decoding',
                  'render':record['render'],'validation':record['validation'],
                  'ultrahdr':str(result.ultrahdr), 'decoded_hdr_sha256':decoded_hash,
                  'natural_comparison':natural_comparison,'elapsed_seconds':time.monotonic()-started}
        record_path.write_text(json.dumps(report,indent=2)+'\n')
        print('DONE',source.name,round(report['elapsed_seconds'],1),natural_comparison or '',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--style', choices=['candidate','natural','baseline'], default='candidate')
    parser.add_argument('--names')
    parser.add_argument('--shard', help='Zero-based index/count for disjoint batches')
    parser.add_argument('--compare',type=Path)
    run(parser.parse_args())
