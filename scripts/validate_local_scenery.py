"""Scenery checks on retained Pixel DNG-derived scenes; no new input support."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import numpy as np
from PIL import Image
from hdrimg.editor import EditRecipe, EditorStore, atomic_json
from hdrimg.local_adjustments import LocalAdjustment, resolve_masks
from hdrimg.render import open_scene, render_pair
from hdrimg.style import STYLE_PRESETS
from hdrimg.tools import resolve_tools
from hdrimg.ultrahdr import encode_ultrahdr, validate_ultrahdr

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--helper',type=Path,required=True);a=p.parse_args()
a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
os.environ['HDRIMG_LOCAL_SELECTION_HELPER']=str(a.helper.resolve())
store=EditorStore(cache=a.output/'cache',support=a.output/'support');tools=resolve_tools();rows=[]
scenes=['01_PXL_20241128_233519825','06_PXL_20241129_005548780','10_PXL_20241130_190219288']
for name in scenes:
    scene=Path('outputs/phone-clear-v8-20260930/candidate-phone')/name/'scene-preview.tif'
    _,info=open_scene(scene);w,h=info.width,info.height;short=min(w,h)
    for style in ('phone-clear','phone-natural'):
        work=a.output/name/style;work.mkdir(parents=True,exist_ok=True)
        opts=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000,
                  development_ev=-2,style=STYLE_PRESETS[style])
        render_pair(scene,work/'base.jpg',work/'base-hdr.rgba16f',_preview_output=work/'base-sdr.rgba16f',**opts)
        source={'sha256':hashlib.sha256(scene.read_bytes()).hexdigest()}
        packet={'width':w,'height':h,'sdr':str(work/'base-sdr.rgba16f')}
        store.render=lambda *args,**kwargs:{'preview_packet':packet}
        center=store.select_region(source,EditRecipe(style=style),[.5,.5])
        analysis=max((store.cache/'derived'/store.engine).glob('selection-*'),key=lambda p:p.stat().st_mtime)
        with Image.open(analysis/'labels.png') as image:labels=np.asarray(image)
        yy,xx=np.nonzero(labels==0);background=None
        if len(xx):
            point=[(float(xx[0])+.5)/labels.shape[1],(float(yy[0])+.5)/labels.shape[0]]
            background=store.select_region(source,EditRecipe(style=style),point)
            assert background['mode']=='soft'
        yy,xx=np.nonzero(labels>0);foreground=None
        if len(xx):
            i=len(xx)//2;point=[(float(xx[i])+.5)/labels.shape[1],(float(yy[i])+.5)/labels.shape[0]]
            foreground=store.select_region(source,EditRecipe(style=style),point)
            assert foreground['mode']=='smart'
        soft=LocalAdjustment('soft-range',shape='ellipse',center_x=.44,center_y=.57,radius_x=.32*short/w,
                             radius_y=.17*short/h,rotation=27,amount=.5)
        regions=(soft,)
        if foreground:
            regions+=(LocalAdjustment('foreground-object',mode='smart',mask_ref=foreground['mask_ref'],direction='darken',amount=.3),)
        masks=resolve_masks(regions,store.support,source['sha256'])
        record=render_pair(scene,work/'edited_sdr.jpg',work/'edited-hdr.rgba16f',_preview_output=work/'edited-sdr.rgba16f',
                           _local_adjustments=regions,_local_masks=masks,**opts)
        encode_ultrahdr(work/'edited_sdr.jpg',work/'edited-hdr.rgba16f',work/'edited_ultrahdr.jpg',width=w,height=h,
                       peak_nits=1000,max_boost=record.max_content_boost,gainmap_quality=record.gainmap_quality,sdr_gamut='display-p3',tools=tools)
        validation=validate_ultrahdr(work/'edited_ultrahdr.jpg',width=w,height=h,work_dir=work,tools=tools)
        row={'scene':name,'style':style,'source':'retained 1536-pixel Pixel DNG-derived scene','center_selection':center,
             'background_selection':background,'foreground_selection':foreground,'validation':asdict(validation),'regions':len(regions),
             'physical_hdr_review':'pending human observation'}
        rows.append(row);atomic_json(a.output/'scenery.json',rows);print(json.dumps(row),flush=True)
        for path in work.glob('*.rgba*'):path.unlink()
assert len(rows)==6
