"""Local GPU targets on retained real RAW preparations (no redevelopment)."""
import argparse
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from hdrimg.editor import EditRecipe, EditorStore, atomic_json
from hdrimg.local_adjustments import LocalAdjustment

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--preparations',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
store=EditorStore(cache=a.output/'cache',support=a.output/'support');store.prune=lambda *args,**kwargs:None
rows=[]
for index,(name,item) in enumerate(json.loads(a.preparations.read_text()).items()):
    if name.startswith('_'):continue
    prepared=json.loads(Path(item['record']).read_text());store.prepare=lambda *args,**kwargs:prepared
    w,h=prepared['scene_info']['width'],prepared['scene_info']['height'];short=min(w,h)
    r=LocalAdjustment('soft-one',center_x=.43,center_y=.58,radius_x=.28*short/w,radius_y=.16*short/h,shape='ellipse',rotation=38,amount=.5)
    # A spatially asymmetric mask checks top/bottom orientation and interpolation.
    mask=np.zeros((117,81),np.uint16);mask[9:88,17:69]=50000;mask[20:35,25:40]=65535
    data=io.BytesIO();Image.fromarray(mask).save(data,format='PNG');ref=hashlib.sha256(data.getvalue()).hexdigest()
    assets=store.support/'selection-assets'/prepared['source']['sha256'];assets.mkdir(parents=True,exist_ok=True)
    (assets/(ref+'.png')).write_bytes(data.getvalue())
    smart=LocalAdjustment('smart-one',mode='smart',mask_ref=ref,amount=.5)
    variants=[('soft-bright',(r,)),('soft-dark',(replace(r,direction='darken'),)),
              ('smart',(smart,)),('eight',tuple(replace(r,id=f'region-{i}',center_x=.15+i*.08,center_y=.3+i*.05) for i in range(8))),
              ('overlap',(r,replace(r,id='second',direction='darken',center_x=.5))),
              ('zero',(replace(r,amount=0),))]
    for style in ['phone-clear','phone-natural']:
        recipe=EditRecipe(style=style)
        anchor=store.render(prepared['source'],recipe,remember=False,floating_preview=True,preview_version=2)
        for label,regions in variants:
            target=store.render(prepared['source'],replace(recipe,local_adjustments=regions),remember=False,floating_preview=True,preview_version=2)
            # The active target's packet carries the fixed mask assets and base.
            rows.append({'source':name,'style':style,'variant':label,'anchor':target,'target':target})
            atomic_json(a.output/'cases.json',rows)
            print(name,style,label,flush=True)
        combined=store.render(prepared['source'],replace(recipe,exposure_ev=.25,shadow_ev=.2,local_adjustments=(r,)),remember=False,floating_preview=True,preview_version=2)
        rows.append({'source':name,'style':style,'variant':'global-combined','anchor':anchor,'target':combined})
        atomic_json(a.output/'cases.json',rows)
