"""Create precise targets from immutable v0.1 RAW preparations for GPU comparison.

The current renderer is always used. Reusing the retained float input isolates
interactive math from external RAW auto-WB nondeterminism. Both styles here use
that same input intentionally; end-to-end RAW validation is a separate suite.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from hdrimg.editor import EditRecipe, EditorStore, atomic_json

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--limit',type=int,default=6)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
os.environ['HDRIMG_ACCELERATOR']=str(Path('dist/Img2UltraHDR.app/Contents/Resources/libhdreditor.dylib').resolve())
os.environ['HDRIMG_VISION_HELPER']=str(Path('dist/Img2UltraHDR.app/Contents/Resources/phone-subject').resolve())
store=EditorStore(cache=a.output/'cache',support=a.output/'support')
# These validation artifacts must survive until the pixel comparison finishes.
store.prune=lambda *args,**kwargs:None
rows=[]
files=sorted(Path('outputs/app-validation/benchmark-isolated/cache/scenes').glob('*/complete.json'))[:a.limit]
variants=[('base',{}),('exposure+',{'exposure_ev':.5}),('exposure-',{'exposure_ev':-.5}),
    ('highlights',{'highlight_ev':-.5}),('shadows',{'shadow_ev':.5}),
    ('saturation',{'saturation':1.1}),('hdr',{'hdr_strength':.8}),('sdr',{'sdr_exposure_ev':.5}),
    ('combined',{'exposure_ev':.3,'shadow_ev':.3,'saturation':1.05,'hdr_strength':.9})]
for f in files:
 prepared=json.loads(f.read_text());store.prepare=lambda *args,**kw:prepared
 for style in ['phone-clear','phone-natural']:
  anchor=None
  for label,values in variants:
   recipe=replace(EditRecipe(style=style),**values)
   started=time.monotonic();result=store.render(prepared['source'],recipe,remember=False,floating_preview=True)
   if label=='base':anchor=result
   row={'source':Path(prepared['source']['path']).name,'style':style,'variant':label,
        'anchor':anchor,'target':result,'elapsed':time.monotonic()-started}
   rows.append(row);atomic_json(a.output/'cases.json',rows)
   print(json.dumps({k:row[k] for k in ['source','style','variant','elapsed']}),flush=True)
