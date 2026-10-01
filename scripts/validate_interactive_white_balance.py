"""Real RAW redevelopment targets for the native approximate WB preview."""
from dataclasses import replace
import argparse,json,os,time
from pathlib import Path
from hdrimg.editor import EditRecipe,EditorStore,atomic_json
p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
root=Path.cwd();os.environ['HDRIMG_ACCELERATOR']=str(root/'dist/Img2UltraHDR.app/Contents/Resources/libhdreditor.dylib')
os.environ['HDRIMG_VISION_HELPER']=str(root/'dist/Img2UltraHDR.app/Contents/Resources/phone-subject')
store=EditorStore(cache=a.output/'cache',support=a.output/'support',progress=lambda p:print(p,flush=True))
rows=[]
for sourcepath in [Path('pics/0N6A9406.CR2'),Path('pics/DSCF8111.RAF')]:
 source,_,_=store.restore(sourcepath)
 base=EditRecipe(white_balance='custom')
 anchor=store.render(source,base,remember=False,floating_preview=True)
 # Protect every packet in the suite, while allowing old full RAW preparations
 # to be evicted under the ordinary 10 GB limit.
 protected=set()
 original_prune=EditorStore.prune.__get__(store)
 store.prune=lambda p=None:original_prune(set(p or set())|protected)
 protected.add(Path(anchor['preview_packet']['hdr']).parent)
 for label,values in [('temperature+',{'temperature_k':6600}),('temperature-',{'temperature_k':4600}),('tint+',{'tint':10}),('tint-',{'tint':-10})]:
  recipe=replace(base,**values);started=time.monotonic()
  result=store.render(source,recipe,remember=False,floating_preview=True)
  protected.add(Path(result['preview_packet']['hdr']).parent)
  rows.append({'source':sourcepath.name,'style':'phone-clear','variant':label,'anchor':anchor,'target':result,'elapsed':time.monotonic()-started})
  atomic_json(a.output/'cases.json',rows);print(label,rows[-1]['elapsed'],flush=True)
