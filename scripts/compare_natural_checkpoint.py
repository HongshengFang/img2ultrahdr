"""Compare frozen and current Natural renderers on exactly the same float data.

This separates implementation changes from repeat-run differences in external
RAW development. The original checkpoint and production results stay intact.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

from hdrimg.render import render_pair
from hdrimg.style import STYLE_PRESETS


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root,extra_scenes=(),output=None):
    source=root/'checkpoint/src/hdrimg'
    spec=importlib.util.spec_from_file_location('frozen_hdrimg',source/'__init__.py',
                                               submodule_search_locations=[str(source)])
    package=importlib.util.module_from_spec(spec)
    sys.modules['frozen_hdrimg']=package
    spec.loader.exec_module(package)
    old_render=importlib.import_module('frozen_hdrimg.render').render_pair
    old_style=importlib.import_module('frozen_hdrimg.style').STYLE_PRESETS['phone-natural']
    output=output or root/'natural-same-input';output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scene in [*sorted((root/'candidate').glob('*/scene-preview.tif')), *extra_scenes]:
        target=output/scene.parent.name;target.mkdir(exist_ok=True)
        settings=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,
                      peak_nits=1000,development_ev=-2)
        for label,function,style in [('frozen',old_render,old_style),
                                      ('current',render_pair,STYLE_PRESETS['phone-natural'])]:
            function(scene,target/f'{label}.jpg',target/f'{label}.rgba16f',style=style,**settings)
        row={'scene':scene.parent.name,'scene_sha256':sha(scene),
             'sdr_jpeg_identical':sha(target/'frozen.jpg')==sha(target/'current.jpg'),
             'hdr_pixels_identical':sha(target/'frozen.rgba16f')==sha(target/'current.rgba16f')}
        rows.append(row);print(json.dumps(row),flush=True)
    (output/'audit.json').write_text(json.dumps({'scope':'same float input; renderer only; independent RAW repeats are separately reported',
          'passed':bool(rows) and all(r['sdr_jpeg_identical'] and r['hdr_pixels_identical'] for r in rows),
          'count':len(rows),'rows':rows},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--scene',type=Path,action='append',default=[])
    p.add_argument('--output',type=Path)
    args=p.parse_args();run(args.root.resolve(),args.scene,args.output)
