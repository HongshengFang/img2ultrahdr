"""Isolate tone compression on saved float RAW scenes; diagnostic, not production."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile
from PIL import Image, ImageDraw
import hdrimg.render as renderer
from hdrimg.phone_tone import PhoneSceneDecision, _smoothstep
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from hdrimg.color import REC2020_TO_DISPLAY_P3
from audit_phone_clear import _panel


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    original_compression = renderer.compress_dark_scene_illumination
    original_sdr = renderer.phone_sdr_luminance
    original_curve = renderer.sdr_curve
    original_shoulder = renderer.highlight_shoulder
    variants = ['current', 'gentle', 'gentle_reinhard', 'single_shoulder']
    for folder in sorted(args.candidate.glob('[0-9][0-9]_*')):
        if int(folder.name[:2]) not in set(map(int,args.ids.split(','))):
            continue
        target = args.output/folder.name; target.mkdir()
        record = json.loads((folder/'record.json').read_text())
        decision = PhoneSceneDecision(**record['render']['tone_mapping']['phone_clear_metering']['decision'])
        day = 1-float(_smoothstep((decision.dark_fraction-.05)/.10))
        sheet = Image.new('RGB',(1200,880),'#eeeeee'); draw=ImageDraw.Draw(sheet)
        for index, variant in enumerate(variants):
            def compression(y, **kwargs):
                if variant != 'current':
                    kwargs['dark_weight'] *= 1-.65*day
                return original_compression(y,**kwargs)
            def phone_sdr(y, **kwargs):
                if variant != 'current':
                    kwargs['dark_shoulder'] *= 1-day
                return original_sdr(y,**kwargs)
            def curve(y):
                return y/(1+y) if variant in {'gentle_reinhard','single_shoulder'} else original_curve(y)
            def shoulder(y, **kwargs):
                return y if variant == 'single_shoulder' else original_shoulder(y,**kwargs)
            with patch.object(renderer,'compress_dark_scene_illumination',compression), \
                    patch.object(renderer,'phone_sdr_luminance',phone_sdr), \
                    patch.object(renderer,'sdr_curve',curve), \
                    patch.object(renderer,'highlight_shoulder',shoulder):
                renderer.render_pair(folder/'scene-preview.tif',target/f'{variant}.jpg',target/f'{variant}.raw',
                    auto_exposure=True,exposure_ev=None,development_ev=-2,highlight_ev=0,
                    hdr_strength=1,peak_nits=1000,style=PHONE_CLEAR_CANDIDATE,
                    _scene_decision=decision,_linear_sdr_output=target/f'{variant}.tif')
            a=tifffile.imread(target/f'{variant}.tif')
            b=np.memmap(target/f'{variant}.raw',dtype='<f2',mode='r',shape=(*a.shape[:2],4))[...,:3].astype(np.float32)@REC2020_TO_DISPLAY_P3.T
            for row,values in enumerate((a,b)):
                panel=_panel(values,hdr=row==1,width=300,height=415)
                sheet.paste(panel,(index*300+(300-panel.width)//2,row*440))
                draw.text((index*300+5,row*440+419),variant+(' HDR mapped' if row else ' SDR'),fill='black')
            print(folder.name[:2],variant,flush=True)
        sheet.save(target/'comparison.jpg',quality=96)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--ids',default='1,5,6');run(p.parse_args())
