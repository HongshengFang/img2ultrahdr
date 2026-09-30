"""Compare the promoted default with the frozen R5 renderer on identical scenes.

The supplied checkpoint must include the retained RAW-derived float previews.
This checks dispatch equivalence, not physical HDR display acceptance. Complete
RAW-to-final-decode checks are recorded separately during promotion.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys

from hdrimg.render import render_pair
from hdrimg.style import DEFAULT_STYLE, STYLE_PRESETS


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(root, output):
    source = root / 'candidate-checkpoint/src/hdrimg'
    spec = importlib.util.spec_from_file_location(
        'reviewed_clear_v8', source / '__init__.py',
        submodule_search_locations=[str(source)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    frozen_render = importlib.import_module('reviewed_clear_v8.render').render_pair
    frozen_style = importlib.import_module('reviewed_clear_v8.style')
    frozen_recipe = importlib.import_module('reviewed_clear_v8.phone_clear_v8').RECIPE
    from hdrimg.phone_clear_v8 import RECIPE
    assert DEFAULT_STYLE == STYLE_PRESETS['phone-clear']
    assert DEFAULT_STYLE.as_record() == frozen_style.PHONE_CLEAR_V8_EXPERIMENT.as_record()
    assert asdict(RECIPE) == asdict(frozen_recipe)
    assert STYLE_PRESETS['phone-natural'].as_record() == frozen_style.STYLE_PRESETS['phone-natural'].as_record()
    output.mkdir(parents=True, exist_ok=False)
    phone = sorted((root / 'candidate-phone').glob('*/scene-preview.tif'))
    cameras = sorted((root / 'camera-scenes').glob('*/scene-preview.tif'))
    if len(phone) != 12 or len(cameras) < 6:
        raise ValueError('Expected all 12 phone scenes and at least 6 camera scenes')
    rows = []
    settings = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0,
                    hdr_strength=1, peak_nits=1000, development_ev=-2)
    for family, scenes in [('phone', phone), ('camera', cameras)]:
        for scene in scenes:
            target = output / family / scene.parent.name
            target.mkdir(parents=True)
            old = frozen_render(scene, target / 'reviewed.jpg', target / 'reviewed.rgba16f',
                                style=frozen_style.PHONE_CLEAR_V8_EXPERIMENT, **settings)
            new = render_pair(scene, target / 'default.jpg', target / 'default.rgba16f', **settings)
            row = {'family': family, 'scene': scene.parent.name, 'scene_sha256': sha(scene),
                   'sdr_jpeg_identical': sha(target / 'reviewed.jpg') == sha(target / 'default.jpg'),
                   'hdr_pixels_identical': sha(target / 'reviewed.rgba16f') == sha(target / 'default.rgba16f'),
                   'render_parameters_identical': asdict(old) == asdict(new),
                   'gainmap_quality': new.gainmap_quality}
            rows.append(row)
            print(json.dumps(row), flush=True)
    passed = all(r['sdr_jpeg_identical'] and r['hdr_pixels_identical']
                 and r['render_parameters_identical'] and r['gainmap_quality'] == 98 for r in rows)
    report = {'scope': __doc__, 'count': len(rows), 'phone_count': len(phone),
              'camera_count': len(cameras), 'style': DEFAULT_STYLE.as_record(),
              'recipe': asdict(RECIPE), 'rows': rows, 'passed': passed}
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise RuntimeError('Promoted default differs from the reviewed R5 renderer')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.checkpoint.resolve(), args.output.resolve())
