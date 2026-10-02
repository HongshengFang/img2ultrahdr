"""Seeded full-range rendering stress on reduced copies of real RAW scenes.

This isolates tone/local math, not RAW development or custom-WB redevelopment.
The exported cases can be fed to check_local_preview.swift for Metal checks.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import numpy as np
import tifffile
from hdrimg.editor import EditorStore, EditRecipe, atomic_json, resize_scene, write_scene
from hdrimg.render import open_scene

parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--prepared-cache',required=True,type=Path)
args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
os.environ['HDRIMG_ENGINE_ID']='stress-render-limits-20261001'
store=EditorStore(cache=root/'cache',support=root/'support')
store.prune=lambda *a,**k:None
rng=np.random.default_rng(20261001);cases=[];checks=[]
for record_path in sorted((args.prepared_cache/'scenes').glob('*/complete.json')):
    prepared=json.loads(record_path.read_text());source=prepared['source']
    scene,_=open_scene(Path(prepared['preview']));reduced=resize_scene(scene,256)
    with tifffile.TiffFile(prepared['preview']) as image:icc=image.pages[0].tags[34675].value
    scene_path=root/(Path(source['path']).stem+'.tif');write_scene(scene_path,reduced,icc)
    prepared['preview']=str(scene_path)
    store.prepare=lambda *a,**k:prepared
    for style in ['phone-clear','phone-natural']:
        anchor=store.render(source,EditRecipe(style=style),remember=False,floating_preview=True,preview_version=2)
        for index in range(16):
            values=dict(exposure_ev=float(rng.uniform(-3,3)),highlight_ev=float(rng.uniform(-2,2)),
                shadow_ev=float(rng.uniform(-2,2)),white_ev=float(rng.uniform(-2,2)),black_ev=float(rng.uniform(-2,2)),
                saturation=float(rng.uniform(.8,1.2)),hdr_strength=float(rng.uniform(0,1)),sdr_exposure_ev=float(rng.uniform(-2,2)))
            if index in (0,1):
                values={k:(-3 if k=='exposure_ev' else .8 if k=='saturation' else 0 if k=='hdr_strength' else -2) if index==0 else
                    (3 if k=='exposure_ev' else 1.2 if k=='saturation' else 1 if k=='hdr_strength' else 2) for k in values}
            if index>=8:
                values['local_adjustments']=[dict(id=f'limit-{i}',shape='ellipse',center_x=float(rng.choice([0,1,.5])),
                    center_y=float(rng.choice([0,1,.5])),radius_x=float(rng.choice([.00001,.2,4])),radius_y=float(rng.choice([.00001,.2,4])),
                    rotation=float(rng.uniform(-180,180)),direction='brighten' if i%2 else 'darken',amount=1) for i in range(8)]
            recipe=EditRecipe.from_dict(dict(style=style,**values))
            frame=store.render(source,recipe,remember=False,floating_preview=True,preview_version=2)
            packet=frame['preview_packet']
            for mode,ceiling in [('sdr',1),('hdr',1000/203+.003)]:
                pixels=np.fromfile(packet[mode],dtype='<f2').reshape(packet['height'],packet['width'],4)
                assert np.isfinite(pixels).all() and pixels[...,:3].min()>=0 and pixels[...,:3].max()<=ceiling
                checks.append(dict(source=Path(source['path']).name,style=style,case=index,mode=mode,peak=float(pixels[...,:3].max())))
            cases.append(dict(source=Path(source['path']).name,style=style,variant=f'full-range-{index}',anchor=anchor,target=frame))
            atomic_json(root/'cases.json',cases);atomic_json(root/'checks.json',checks)
        print(f'Passed 16 full-range recipes: {Path(source["path"]).name} / {style}',flush=True)
print(f'Passed {len(checks)} precise SDR/HDR frames',flush=True)
