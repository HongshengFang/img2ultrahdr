"""Native RAW WB comparison with identical denoise and rendering conditions.

Only the diagnostic resize is shared by all variants. No developed pixels are
recolored to simulate WB. Wider Auto bias is scoped to this experiment until
its actual engine output has been inspected.
"""
import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from PIL import Image,ImageDraw

import hdrimg.raw as raw
from hdrimg.tools import resolve_tools
from hdrimg.render import render_pair
from hdrimg.phone_tone import PhoneSceneDecision
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from hdrimg.color import REC2020_TO_DISPLAY_P3
from audit_phone_clear import _panel


def run(args):
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    sources=sorted(Path('jpg_hdr_sample_effect').resolve().glob('*.dng'))
    tools=resolve_tools(require_raw=True,require_exif=True)
    original_overlay=raw._white_balance_overlay
    for number in map(int,args.ids.split(',')):
        previous=next((args.previous/'candidate').glob(f'{number:02}_*'))
        record=json.loads((previous/'record.json').read_text())
        decision=PhoneSceneDecision(**record['render']['tone_mapping']['phone_clear_metering']['decision'])
        target=args.output/previous.name;target.mkdir();panels=[];rows=[]
        for label,mode,bias in [('auto','auto',0.),('bias12','auto',.12),('bias25','auto',.25),('camera','camera',0.)]:
            started=time.monotonic();folder=target/label;folder.mkdir()
            profile=folder/'probe.pp3'
            profile.write_text(raw.phone_denoise_overlay(.6)+'\n[Resize]\nEnabled=true\nWidth=1024\nHeight=1024\nDataSpecified=3\nMethod=Lanczos\nAllowUpscaling=false\n')
            def wb(mode,temperature_k,tint,**kw):
                text=original_overlay(mode,temperature_k,tint)
                return text.replace('[White Balance]\n',f'[White Balance]\nTemperatureBias={bias:.8f}\n')
            with patch.object(raw,'_white_balance_overlay',wb):
                raw.develop_raw(sources[number-1],folder/'scene.tif',tools=tools,white_balance=mode,
                    temperature_k=None,tint=1.,work_dir=folder,profile_overlay=profile)
            scene=tifffile.imread(folder/'scene.tif');h,w=scene.shape[:2]
            info=render_pair(folder/'scene.tif',folder/'sdr.jpg',folder/'hdr.raw',
                auto_exposure=True,exposure_ev=None,development_ev=-2,highlight_ev=0,hdr_strength=1,
                peak_nits=1000,style=PHONE_CLEAR_CANDIDATE,_scene_decision=decision,
                skin_protection_strength=0,subject_adaptation_strength=0,
                _skin_context=(None,{'status':'disabled_for_controlled_wb_probe'}))
            hdr=np.memmap(folder/'hdr.raw',dtype='<f2',shape=(h,w,4))[...,:3].astype(np.float32)@REC2020_TO_DISPLAY_P3.T
            panel=_panel(hdr,hdr=True,width=420,height=550);panels.append((label,panel))
            rows.append({'mode':mode,'bias':bias,'seconds':time.monotonic()-started,'size':[w,h],
                         'conditions':'matching RAW denoise; no RAW skin fallback or display skin/subject adaptation in any variant'})
            print(number,label,rows[-1]['seconds'],flush=True)
        sheet=Image.new('RGB',(1680,580),'#eee');draw=ImageDraw.Draw(sheet)
        for col,(label,panel) in enumerate(panels):
            sheet.paste(panel,(420*col+(420-panel.width)//2,0));draw.text((420*col+10,555),label,fill='black')
        sheet.save(target/'comparison.jpg',quality=95)
        (target/'audit.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('previous',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--ids',default='1,4,5,8');run(p.parse_args())
