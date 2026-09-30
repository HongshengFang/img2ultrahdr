"""Prove that public Clear dispatch matches the fixed, reviewed R4 recipe."""
import argparse
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

from hdrimg.render import render_pair
from hdrimg.style import DEFAULT_STYLE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root,output):
    source=root/'candidate-checkpoint/src/hdrimg'
    spec=importlib.util.spec_from_file_location('reviewed_hdrimg',source/'__init__.py',
                                               submodule_search_locations=[str(source)])
    package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package
    spec.loader.exec_module(package)
    frozen_render=importlib.import_module('reviewed_hdrimg.render').render_pair
    frozen_style=importlib.import_module('reviewed_hdrimg.style').PHONE_CLEAR_CANDIDATE
    assert DEFAULT_STYLE.as_record()==frozen_style.as_record()
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scene in sorted((root/'candidate').glob('*/scene-preview.tif')):
        target=output/scene.parent.name;target.mkdir(exist_ok=True)
        settings=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,
                      peak_nits=1000,development_ev=-2)
        frozen_render(scene,target/'reviewed.jpg',target/'reviewed.rgba16f',style=frozen_style,**settings)
        render_pair(scene,target/'default.jpg',target/'default.rgba16f',**settings)
        row={'scene':scene.parent.name,'scene_sha256':sha(scene),
             'sdr_jpeg_identical':sha(target/'reviewed.jpg')==sha(target/'default.jpg'),
             'hdr_pixels_identical':sha(target/'reviewed.rgba16f')==sha(target/'default.rgba16f')}
        rows.append(row);print(json.dumps(row),flush=True)
    result={'scope':'Same retained float RAW inputs; frozen R4 candidate versus proposed/public default. Full RAW dispatch is checked separately.',
            'style':DEFAULT_STYLE.as_record(),'count':len(rows),'rows':rows,
            'passed':len(rows)==12 and all(r['sdr_jpeg_identical'] and r['hdr_pixels_identical'] for r in rows)}
    (output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();run(args.root,args.output)
