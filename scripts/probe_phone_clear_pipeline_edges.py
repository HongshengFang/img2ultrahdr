"""Check complete float render and final Ultra HDR encoding on controlled edges."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from PIL import Image

from hdrimg.color import DISPLAY_P3_TO_XYZ
from hdrimg.phone_tone import PhoneSceneDecision
from hdrimg.render import render_pair
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from hdrimg.tone import luminance_rec2020
from hdrimg.tools import resolve_tools
from hdrimg.ultrahdr import encode_ultrahdr, validate_ultrahdr


def run(output,extended=False):
    output.mkdir(parents=True,exist_ok=False)
    tools=resolve_tools();rows=[]
    cases=[(name,fg,bg,vertical,softness,scale) for name,fg,bg in
           [('dark',.12,.7),('bright',.7,.12),('pale',.55,.65)]
           for vertical in [False,True] for softness,scale in
           ([(0,1),(6,1),(12,2)] if extended else [(0,1)])]
    for name,fg,bg,vertical,softness,scale in cases:
            target=output/(name+('-vertical' if vertical else '-horizontal')+f'-soft{softness}-scale{scale}');target.mkdir()
            original=np.full((128,192,3),bg,np.float32);original[:,64:128]=fg
            mask=np.zeros((128,192),np.float32);mask[:,64:128]=1
            if softness:
                xx=np.arange(192,dtype=np.float32)
                coverage=np.clip((xx-64+softness/2)/softness,0,1)*np.clip((128+softness/2-xx)/softness,0,1)
                original[:]=bg+(fg-bg)*coverage[None,:,None]
            if scale != 1:
                original=np.repeat(np.repeat(original,scale,axis=0),scale,axis=1)
                mask=np.repeat(np.repeat(mask,scale,axis=0),scale,axis=1)
            if vertical:original=original.transpose(1,0,2);mask=mask.T
            h,w=mask.shape;tifffile.imwrite(target/'scene.tif',original/4,photometric='rgb')
            decision=PhoneSceneDecision(.1,.4,1.2,1.8,2,0,0,0,1,1,2.3,3.5,99.5,1)
            with patch('hdrimg.phone_subject.detect_subject_fields',return_value=(None,None,
                       {'faces':[dict(x=.4,y=.22,width=.2,height=.2,confidence=.9)]})):
                info=render_pair(target/'scene.tif',target/'sdr.jpg',target/'hdr.raw',
                    auto_exposure=True,exposure_ev=None,development_ev=-2,highlight_ev=0,
                    hdr_strength=1,peak_nits=1000,style=PHONE_CLEAR_CANDIDATE,
                    _scene_decision=decision,_skin_context=(Image.fromarray(mask),{'status':'synthetic'}),
                    _linear_sdr_output=target/'sdr.tif')
            encode_ultrahdr(target/'sdr.jpg',target/'hdr.raw',target/'ultrahdr.jpg',width=w,height=h,
                peak_nits=1000,max_boost=info.max_content_boost,gainmap_quality=95,sdr_gamut='display-p3',tools=tools)
            validate_ultrahdr(target/'ultrahdr.jpg',width=w,height=h,work_dir=target,tools=tools)
            linear=tifffile.imread(target/'sdr.tif')@DISPLAY_P3_TO_XYZ[1]
            raw=np.memmap(target/'hdr.raw',dtype='<f2',shape=(h,w,4))
            hdr=luminance_rec2020(raw[...,:3].astype(np.float32))
            decoded=np.memmap(target/'decoded_hdr.rgba16f',dtype='<f2',shape=(h,w,4))
            decoded_y=luminance_rec2020(decoded[...,:3].astype(np.float32))
            sdr_codes=np.asarray(Image.open(target/'ultrahdr.jpg').convert('RGB'),np.float32).mean(-1)/255
            for mode,y in [('float_sdr',linear),('float16_hdr',hdr),('decoded_sdr',sdr_codes),('decoded_hdr',decoded_y)]:
                if vertical:y=y.T
                strip=y[42*scale,52*scale:76*scale]
                reverse=np.maximum(-np.sign(fg-bg)*np.diff(strip),0)
                # Float constraints are exact within arithmetic. Encoded data
                # allows one SDR code or 0.5% of the HDR step for quantization.
                tolerance=(1/255 if mode=='decoded_sdr' else max(1e-6,.005*abs(float(strip[-1]-strip[0])))
                           if mode=='decoded_hdr' else 1e-6)
                rows.append({'case':name,'vertical':vertical,'softness':softness,'scale':scale,'mode':mode,
                             'max_reverse_step':float(reverse.max()),'tolerance':tolerance,
                             'passed':bool(reverse.max()<=tolerance),'transect':strip.tolist()})
    report={'passed':all(r['passed'] for r in rows),'count':len(rows),'rows':rows,
            'scope':'synthetic render, real Ultra HDR encode/decode; does not replace real-photo HDR viewing'}
    (output/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'count':len(rows),'failed':[r for r in rows if not r['passed']]}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    p.add_argument('--extended',action='store_true');args=p.parse_args()
    run(args.output,args.extended)
