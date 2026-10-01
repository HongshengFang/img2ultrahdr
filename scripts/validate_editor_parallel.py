"""Check preview concurrency against serial rendering on prepared RAW scenes."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from PIL import Image

from hdrimg.editor import atomic_json, file_digest
from hdrimg.phone_tone import PhoneSceneDecision
from hdrimg.render import RenderPolicy, render_pair
from hdrimg.style import STYLE_PRESETS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--zero', action='store_true', help='Compare default, zero-adjustment pixels')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for manifest in sorted((args.cache / 'scenes').glob('*/complete.json')):
        prepared = json.loads(manifest.read_text())
        name = Path(prepared['source']['path']).stem
        context = (Image.fromarray(np.load(prepared['person'])) if prepared['person'] else None,
                   prepared['skin_record'])
        for style in ('phone-clear', 'phone-natural'):
            hashes = []
            for parallel in (False, True):
                base = args.output / f'{name}-{style}-{parallel}'
                options = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0 if args.zero else .3,
                               shadow_ev=0 if args.zero else .4, edit_exposure_ev=0 if args.zero else .2,
                               saturation_scale=1 if args.zero else 1.05,
                               hdr_strength=1 if args.zero else .8, peak_nits=1000, development_ev=-2,
                               style=STYLE_PRESETS[style], _analysis=prepared['analysis'],
                               _skin_context=context,
                               _scene_decision=PhoneSceneDecision(**prepared['scene_decision']),
                               chunk_rows=256 if parallel and style == 'phone-clear' else 512,
                               _processing_policy=replace(RenderPolicy(), parallel_preview=parallel,
                                                          preview_workers=4 if parallel else 3))
                render_pair(Path(prepared['preview']), base.with_suffix('.jpg'),
                            base.with_suffix('.raw'), **options)
                hashes.append((file_digest(base.with_suffix('.jpg')), file_digest(base.with_suffix('.raw'))))
                base.with_suffix('.raw').unlink()
            row = {'source': name, 'style': style, 'zero_adjustment': args.zero,
                   'sdr_equal': hashes[0][0] == hashes[1][0],
                   'hdr_equal': hashes[0][1] == hashes[1][1]}
            rows.append(row)
            atomic_json(args.output / 'parallel-equivalence.json', rows)
            print(json.dumps(row), flush=True)
    assert rows and all(row['sdr_equal'] and row['hdr_equal'] for row in rows)


if __name__ == '__main__':
    main()
