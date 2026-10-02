"""Use a real exact base to check differently sized fixed mask assets together."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import numpy as np
from hdrimg.editor import EditRecipe, atomic_json
from hdrimg.local_adjustments import LocalAdjustment, field_rows, adjust_rgb

p=argparse.ArgumentParser();p.add_argument('cases',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
base=json.loads(a.cases.read_text())[0]['anchor'];packet=base['preview_packet'];w,h=packet['width'],packet['height']
recipe=replace(EditRecipe.from_dict(base['recipe']),local_adjustments=())
masks={}
for i,shape in enumerate([(117,81),(29,63)]):
    values=np.random.default_rng(i).random(shape,dtype=np.float32);values[0,:]=1;values[-1,:]=0
    values[:,0]=1;values[:,-1]=0;masks[f'mask-{i}']=values
smart=tuple(LocalAdjustment(f'mask-{i}',mode='smart',mask_ref=str(i)*64,amount=.8,direction='brighten' if i==0 else 'darken') for i in range(2))
soft=tuple(LocalAdjustment(f'soft-{i}',shape='ellipse',center_x=.1+i*.13,center_y=.2+i*.09,
                           radius_x=.17*min(w,h)/w,radius_y=.12*min(w,h)/h,rotation=-42+i*19,amount=.7) for i in range(6))
rows=[]
for index,regions in enumerate([smart,smart+soft,(replace(smart[0],amount=0),smart[1])]):
    work=a.output/str(index);work.mkdir(exist_ok=True)
    target_recipe=replace(recipe,local_adjustments=regions)
    target_packet=dict(packet,anchor_recipe=asdict(target_recipe),local_masks={})
    for id,mask in masks.items():
        path=work/(id+'.r32f');mask.astype('<f4').tofile(path)
        target_packet['local_masks'][id]={'path':str(path),'width':mask.shape[1],'height':mask.shape[0]}
    field=field_rows(regions,masks,w,h,0,h)
    for hdr in (False,True):
        key='hdr' if hdr else 'sdr';rgba=np.fromfile(packet['base_'+key],dtype='<f2').reshape(h,w,4).astype(np.float32)
        rgba[...,:3]=adjust_rgb(rgba[...,:3],field,1000/203 if hdr else 1)
        path=work/(key+'.rgba16f');rgba.astype('<f2').tofile(path);target_packet[key]=str(path)
    value=dict(base,recipe=asdict(target_recipe),preview_packet=target_packet)
    rows.append({'source':Path(base['source']['path']).name,'style':recipe.style,'variant':'mixed-mask-sizes','anchor':value,'target':value})
atomic_json(a.output/'cases.json',rows)
