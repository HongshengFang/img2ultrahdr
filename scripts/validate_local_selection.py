"""Inspect all camera samples through the real foreground helper and asset store."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
from PIL import Image

from hdrimg.editor import EditRecipe, EditorStore, atomic_json
from hdrimg.local_adjustments import LocalAdjustment, resolve_masks

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--helper',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
os.environ['HDRIMG_LOCAL_SELECTION_HELPER']=str(a.helper.resolve())
store=EditorStore(cache=a.output/'cache',support=a.output/'support')
original_render=store.render;rows=[]
baseline=Path('outputs/phone-clear-v8-20260930/best-ultrahdr-37')
for raw in sorted(Path('pics').glob('*')):
    if raw.suffix.lower() not in ('.cr2','.raf'):continue
    original=baseline/(raw.stem+'_ultrahdr.jpg')
    if not original.is_file():continue
    source,_,_=store.restore(raw)
    with Image.open(original) as image:
        image=image.convert('RGB');image.thumbnail((1536,1536),Image.Resampling.BOX)
        w,h=image.size;encoded=np.asarray(image,np.float32)/255
    rgba=np.ones((h,w,4),'<f2');rgba[...,:3]=np.where(encoded<=.04045,encoded/12.92,((encoded+.055)/1.055)**2.4)
    from hdrimg.render import _quantize_sdr
    reference=_quantize_sdr(rgba[...,:3].astype(np.float32))
    key=hashlib.sha256(reference.tobytes()+str(reference.shape).encode()+b'local-selection-2').hexdigest()
    root=store.cache/'derived'/store.engine/('selection-'+key);root.mkdir(parents=True,exist_ok=True)
    Image.fromarray(reference).save(root/'reference.png');rgba.tofile(root/'reference.rgba16f')
    started=time.monotonic();reason=None
    try:
        subprocess.run([str(a.helper.resolve()),str(root/'reference.png'),str(root)],check=True,capture_output=True,timeout=8)
    except subprocess.TimeoutExpired:reason='timeout'
    except subprocess.CalledProcessError:reason='failed'
    elapsed=time.monotonic()-started
    count=0;chosen=None
    if reason is None:
        with Image.open(root/'labels.png') as im:labels=np.asarray(im)
        labels_present,counts=np.unique(labels[labels>0],return_counts=True);count=len(labels_present)
        if count:
            instance=labels_present[np.argmax(counts)];yy,xx=np.nonzero(labels==instance)
            mid=len(xx)//2;point=[(float(xx[mid])+.5)/labels.shape[1],(float(yy[mid])+.5)/labels.shape[0]]
            fixture={'key':key,'preview_packet':{'width':w,'height':h,'sdr':str(root/'reference.rgba16f')}}
            store.render=lambda *args,**kwargs:fixture
            cached_started=time.monotonic();chosen=store.select_region(source,EditRecipe(),point);cached_seconds=time.monotonic()-cached_started
            if chosen['mode']=='smart':
                region=LocalAdjustment('verified-region',mode='smart',mask_ref=chosen['mask_ref'],center_x=point[0],center_y=point[1],amount=.5)
                store.remember(source,replace(EditRecipe(),local_adjustments=(region,)))
                assert store.restore(raw)[1].local_adjustments[0]==region
                resolve_masks((region,),store.support,source['sha256'])
    row={'source':raw.name,'reference':'accepted V8 R5 SDR fallback','instance_count':count,'analysis_seconds':elapsed,
         'reason':reason,'selection':chosen,'cached_selection_seconds':cached_seconds if chosen else None}
    rows.append(row);atomic_json(a.output/'selection.json',rows);print(json.dumps(row),flush=True)
store.render=original_render
assert len(rows)==37,len(rows)
assert all((row['selection'] or {}).get('mode')=='smart' for row in rows), 'A labelled foreground click must produce a nonzero selection'
