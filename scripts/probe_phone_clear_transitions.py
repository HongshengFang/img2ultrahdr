"""Strict spatial-transition acceptance probe, separate from unit regressions.

Reports new extrema of the complete spatial exposure field, including slopes
too small to judge reliably from a fit-to-screen preview. This is a promotion
gate, not a score that other improvements can compensate for.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from hdrimg.phone_clear import local_exposure_field, apply_exposure_field
from hdrimg.color import srgb_oetf


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    rows=[];tiles=[]
    for name,foreground,background in [('dark_subject',.12,.7),('bright_subject',.7,.12),('pale_on_pale',.55,.65)]:
        rgb=np.full((128,128,3),background,np.float32);rgb[:,32:96]=foreground
        reference=None
        for variant,offset,blur in [('nominal',0,0),('shift_left',-2,0),('shift_right',2,0),('soft',0,2)]:
            matte=np.zeros((128,128),np.uint8);matte[:,32+offset:96+offset]=255
            mask=Image.fromarray(matte)
            if blur: mask=mask.filter(ImageFilter.GaussianBlur(blur))
            mask=Image.fromarray(np.asarray(mask,np.float32)/255)
            field,record=local_exposure_field(rgb,person=mask,
                faces=[dict(x=.4,y=.22,width=.2,height=.2,confidence=.9)],gamut='srgb',
                high_key=1,indoor=0,dark=0,subject_strength=1,local_strength=1)
            ev=np.asarray(field)
            if reference is None: reference=ev
            for mode,upper in [('sdr',1.),('hdr',1000/203)]:
                y=rgb[...,0]*(1 if mode=='sdr' else 2)
                result=apply_exposure_field(y,ev,upper=upper)
                transect=result[42,24:40]
                # Input has one monotone step; a reverse derivative on either
                # side is a new extremum, even if the overall step stays sharp.
                sign=np.sign(foreground-background)
                reverse=np.maximum(-sign*np.diff(transect),0)
                rows.append({'case':name,'matte':variant,'mode':mode,
                    'strict_monotone_passed':bool(np.max(reverse)<1e-6),
                    'max_reverse_step':float(np.max(reverse)),
                    'total_reverse_variation':float(reverse.sum()),
                    'matte_shift_max_ev':float(np.max(np.abs(ev-reference))),
                    'input_transect':y[42,24:40].tolist(),'output_transect':transect.tolist()})
                if variant=='nominal':
                    view=result/(1+result) if mode=='hdr' else result
                    tile=Image.fromarray(np.uint8(np.round(np.clip(srgb_oetf(view),0,1)*255))).convert('RGB')
                    canvas=Image.new('RGB',(300,310),'white');canvas.paste(tile.resize((256,256)),(22,0))
                    ImageDraw.Draw(canvas).text((8,266),f'{name} {mode}',fill='black');tiles.append(canvas)
    sheet=Image.new('RGB',(900,620),'white')
    for i,tile in enumerate(tiles): sheet.paste(tile,((i//2)*300,(i%2)*310))
    sheet.save(output/'transitions.png')
    report={'passed':all(r['strict_monotone_passed'] for r in rows),'cases':len(rows),
            'scope':'spatial local-exposure field, before quantization and codec; strict promotion gate',
            'finding':'A monotone scalar curve does not guarantee a monotone spatially varying exposure field.',
            'rows':rows}
    (output/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    run(p.parse_args().output)
